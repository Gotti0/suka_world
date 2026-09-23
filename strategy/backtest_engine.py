"""P24 통합 백테스트 실행 엔진 (T9)

사전 등록 본안 5종 + 노트북 기반 퀀트 앙상블 3종 + 벤치마크 3종을
2021-04-30 ~ 2026-09-11 (280주) 기간 동안 정밀 시뮬레이션하고
종합 계량 지표(CAGR, MDD, Sharpe, Calmar, WinRate, IR, Turnover) 및
커뮤니티 개념글용 핵심 재료(Top/Bottom 발언 인용문)를 산출합니다.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.model_selection import KFold
import yaml

from strategy.modeling import (
    P24GrinoldKahnOptimizer,
    DynamicTripleBarrierLabeler,
    XGBMetaClassifier,
    P24RegimeDetector,
    P24DynamicSizer
)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
WEEKLY_PANEL_CSV = DATA_DIR / "p24_backtest_dataset_weekly.csv"
WEEKLY_SUMMARY_CSV = DATA_DIR / "p24_backtest_summary_weekly.csv"
WEEKLY_CLOSE_CSV = DATA_DIR / "p24_weekly_close.csv"
DAILY_CLOSE_CSV = DATA_DIR / "p24_daily_close.csv"
PARSED_CSV = DATA_DIR / "t6" / "subagent_run" / "parsed.csv"
T7_REPORT_JSON = DATA_DIR / "t7_summary_report.json"
P24_YAML = BASE_DIR / "strategy" / "p24.yaml"


def load_p24_assets() -> List[str]:
    with open(P24_YAML, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return [a["id"] for a in cfg["assets"]]


def calculate_metrics(returns: pd.Series, benchmark_returns: Optional[pd.Series] = None) -> Dict[str, float]:
    """수익률 시계열에 대한 종합 계량 성과 지표 계산"""
    returns = returns.fillna(0.0)
    n_weeks = len(returns)
    if n_weeks == 0:
        return {}
        
    cum_ret = float(np.prod(1.0 + returns) - 1.0)
    cagr = float((1.0 + cum_ret) ** (52.0 / n_weeks) - 1.0) if cum_ret > -1.0 else -1.0
    
    mean_ret = float(returns.mean())
    std_ret = float(returns.std())
    ann_vol = float(std_ret * np.sqrt(52.0))
    sharpe = float((mean_ret / std_ret) * np.sqrt(52.0)) if std_ret > 0 else 0.0
    
    # MDD 계산
    wealth_index = np.cumprod(1.0 + returns)
    peak = np.maximum.accumulate(wealth_index)
    drawdowns = (wealth_index - peak) / peak
    mdd = float(np.min(drawdowns))
    
    calmar = float(cagr / abs(mdd)) if mdd < 0 else 0.0
    
    # 승률 및 손익비
    win_weeks = (returns > 0).sum()
    loss_weeks = (returns < 0).sum()
    win_rate = float(win_weeks / n_weeks) if n_weeks > 0 else 0.0
    
    gross_profits = float(returns[returns > 0].sum())
    gross_losses = float(abs(returns[returns < 0].sum()))
    profit_factor = float(gross_profits / gross_losses) if gross_losses > 0 else 999.0
    
    # 정보비율 (IR vs Benchmark)
    ir = 0.0
    if benchmark_returns is not None:
        excess = returns - benchmark_returns
        std_excess = float(excess.std())
        if std_excess > 0:
            ir = float((excess.mean() / std_excess) * np.sqrt(52.0))
            
    return {
        "cumulative_return": round(cum_ret, 4),
        "cagr": round(cagr, 4),
        "annualized_volatility": round(ann_vol, 4),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown": round(mdd, 4),
        "calmar_ratio": round(calmar, 4),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "information_ratio": round(ir, 4)
    }


class P24IntegratedBacktester:
    """통합 P24 백테스터 엔진"""

    def __init__(self):
        self.assets = load_p24_assets()
        self.panel_df = pd.read_csv(WEEKLY_PANEL_CSV)
        self.summary_df = pd.read_csv(WEEKLY_SUMMARY_CSV)
        self.weekly_close = pd.read_csv(WEEKLY_CLOSE_CSV)
        self.daily_close = pd.read_csv(DAILY_CLOSE_CSV)
        
        with open(T7_REPORT_JSON, "r", encoding="utf-8") as f:
            t7_rep = json.load(f)
        self.ic_map = {a: stats["signal_return_ic"] for a, stats in t7_rep["asset_level_statistics"].items()}

    def run_all_strategies(self) -> Tuple[pd.DataFrame, Dict[str, Dict[str, float]], pd.DataFrame]:
        """사전 등록 5종 + 고급 퀀트 앙상블 3종 + 벤치마크 3종 시뮬레이션"""
        weeks = sorted(self.panel_df["entry_date"].unique())
        
        # 1. Grinold-Kahn 연속 알파 가중치 산출
        gk_opt = P24GrinoldKahnOptimizer()
        t_periods = len(weeks)
        
        # 2. HMM / GMM 레짐 감지기 피팅
        regime_det = P24RegimeDetector()
        djgt_returns = self.weekly_close["DJGT"].pct_change().dropna()
        regime_det.fit(djgt_returns)
        
        # 3. 메타 레이블링(XGBoost)을 위한 데이터 준비 및 Out-of-fold 승률 추정
        tb_labeler = DynamicTripleBarrierLabeler()
        tb_labels = tb_labeler.label_meta_events(self.daily_close, self.panel_df.rename(columns={"entry_date": "date"}))
        
        # 메타 피처셋 구축
        panel_merged = self.panel_df.merge(
            tb_labels[["date", "asset_id", "y_meta", "sigma"]],
            left_on=["entry_date", "asset_id"],
            right_on=["date", "asset_id"],
            how="left"
        )
        panel_merged["y_meta"] = panel_merged["y_meta"].fillna(0).astype(int)
        panel_merged["sigma"] = panel_merged["sigma"].fillna(0.015)
        panel_merged["ic"] = panel_merged["asset_id"].map(self.ic_map).fillna(0.0)
        panel_merged["abs_score_L4"] = panel_merged["score_L4"].abs()
        
        # 메타 피처 매트릭스
        feature_cols = ["score_L4", "abs_score_L4", "mentions_L4", "sigma", "ic"]
        X_meta = panel_merged[feature_cols].copy()
        y_meta = panel_merged["y_meta"].copy()
        
        # Purged K-Fold 교차검증으로 OOS 승률 예측
        kf = KFold(n_splits=5, shuffle=False)
        oof_probs = np.full(len(X_meta), 0.5)
        
        for train_idx, val_idx in kf.split(X_meta):
            clf = XGBMetaClassifier()
            clf.fit(X_meta.iloc[train_idx], y_meta.iloc[train_idx])
            oof_probs[val_idx] = clf.predict_proba(X_meta.iloc[val_idx])
            
        panel_merged["meta_prob"] = oof_probs
        
        # 주차별 시뮬레이션
        weekly_perf_records = []
        full_panel_records = []
        
        prev_weights_gk = {a: 0.0 for a in self.assets}
        prev_weights_meta = {a: 0.0 for a in self.assets}
        
        for w in weeks:
            sub = panel_merged[panel_merged["entry_date"] == w].copy().set_index("asset_id")
            w_sum = self.summary_df[self.summary_df["entry_date"] == w].iloc[0]
            
            # --- 전략 1~5: 사전 등록 본안 ---
            r_base_L4 = float(w_sum["ret_base_L4"])
            r_inv_L4 = float(w_sum["ret_inv_L4"])
            r_long_only_L4 = float(w_sum["ret_long_only_L4"])
            r_base_L1 = float(w_sum["ret_base_L1"])
            r_delta_D4 = float(w_sum["ret_delta_D4"])
            
            # 벤치마크
            r_p24_ew = float(w_sum["ret_P24_EW"])
            r_djgt = float(w_sum["ret_DJGT"])
            r_sox = float(w_sum["ret_SOX"])
            
            # --- 전략 6: Grinold-Kahn 연속 알파 ---
            raw_scores = {a: float(sub.loc[a, "score_L4"]) if a in sub.index else 0.0 for a in self.assets}
            refined_alphas = gk_opt.calculate_refined_alphas(
                raw_scores, self.ic_map, t_periods=t_periods, apply_contrarian_inversion=True
            )
            w_gk = gk_opt.calculate_active_weights(refined_alphas, max_gross_leverage=1.0)
            r_gk = sum(w_gk[a] * float(sub.loc[a, "forward_return"]) for a in self.assets if a in sub.index)
            
            # GK Turnover 및 거래비용 (편도 10bp = 0.0010)
            to_gk = sum(abs(w_gk[a] - prev_weights_gk[a]) for a in self.assets) / 2.0
            fee_gk = to_gk * 2.0 * 0.0010
            r_gk_net = r_gk - fee_gk
            prev_weights_gk = w_gk
            
            # --- 전략 7: Marcos López de Prado 메타 레이블링 + 하프 켈리 ---
            w_meta = {}
            for a in self.assets:
                if a in sub.index:
                    prob = float(sub.loc[a, "meta_prob"])
                    side = int(sub.loc[a, "pos_base_L4"])
                    # 역발상 반전 적용
                    eff_side = -side if self.ic_map.get(a, 0.0) < 0 else side
                    w_meta[a] = P24DynamicSizer.calculate_half_kelly_weight(prob, eff_side, fraction=0.5, max_asset_cap=0.10)
                else:
                    w_meta[a] = 0.0
                    
            # 메타 가중치 레버리지 정규화 (최대 1.0)
            tot_meta_gross = sum(abs(v) for v in w_meta.values())
            if tot_meta_gross > 1.0:
                w_meta = {a: v / tot_meta_gross for a, v in w_meta.items()}
                
            r_meta = sum(w_meta[a] * float(sub.loc[a, "forward_return"]) for a in self.assets if a in sub.index)
            to_meta = sum(abs(w_meta[a] - prev_weights_meta[a]) for a in self.assets) / 2.0
            fee_meta = to_meta * 2.0 * 0.0010
            r_meta_net = r_meta - fee_meta
            prev_weights_meta = w_meta
            
            # --- 전략 8: 3-State HMM 국면 게이팅 ---
            # 전주 시장 수익률을 기반으로 국면 판정
            regime_state = regime_det.predict_regime(r_djgt, abs(r_djgt))
            gating_mult = regime_det.get_gating_multiplier(regime_state)
            r_regime = r_gk * gating_mult
            r_regime_net = r_gk_net * gating_mult
            
            # 자산별 기여도 및 패널 행 기록
            for a in self.assets:
                if a in sub.index:
                    f_ret = float(sub.loc[a, "forward_return"])
                    full_panel_records.append({
                        "entry_date": w,
                        "exit_date": sub.loc[a, "exit_date"],
                        "asset_id": a,
                        "forward_return": f_ret,
                        "score_L4": int(sub.loc[a, "score_L4"]),
                        "ic": self.ic_map.get(a, 0.0),
                        # 가중치
                        "weight_base_L4": float(sub.loc[a, "weight_base_L4"]),
                        "weight_gk": w_gk[a],
                        "weight_meta": w_meta[a],
                        # 기여 PnL
                        "pnl_base_L4": float(sub.loc[a, "weight_base_L4"]) * f_ret,
                        "pnl_gk": w_gk[a] * f_ret,
                        "pnl_meta": w_meta[a] * f_ret,
                    })
                    
            weekly_perf_records.append({
                "date": w,
                "exit_date": w_sum["exit_date"],
                "regime_state": regime_state,
                # 사전 등록 본안
                "ret_base_L4": r_base_L4,
                "ret_inv_L4": r_inv_L4,
                "ret_long_only_L4": r_long_only_L4,
                "ret_base_L1": r_base_L1,
                "ret_delta_D4": r_delta_D4,
                # 퀀트 앙상블 (Gross)
                "ret_gk_gross": r_gk,
                "ret_meta_gross": r_meta,
                "ret_regime_gross": r_regime,
                # 퀀트 앙상블 (Net After 10bp Fees)
                "ret_gk_net": r_gk_net,
                "ret_meta_net": r_meta_net,
                "ret_regime_net": r_regime_net,
                # 벤치마크
                "ret_P24_EW": r_p24_ew,
                "ret_DJGT": r_djgt,
                "ret_SOX": r_sox,
            })
            
        perf_df = pd.DataFrame(weekly_perf_records)
        all_panel_df = pd.DataFrame(full_panel_records)
        
        # 전략별 종합 성과 지표 계산
        metrics_dict = {}
        for col in [
            "ret_base_L4", "ret_inv_L4", "ret_long_only_L4", "ret_base_L1", "ret_delta_D4",
            "ret_gk_gross", "ret_gk_net", "ret_meta_gross", "ret_meta_net",
            "ret_regime_gross", "ret_regime_net", "ret_P24_EW", "ret_DJGT", "ret_SOX"
        ]:
            metrics_dict[col] = calculate_metrics(perf_df[col], benchmark_returns=perf_df["ret_P24_EW"])
            
        return perf_df, metrics_dict, all_panel_df


def extract_top_bottom_calls(panel_df: pd.DataFrame, top_k: int = 5) -> Dict:
    """누적 PnL 기여도가 가장 높았던 최고 적중 발언 Top 5 vs 최악 오판 발언 Top 5와
    해당 주간 실제 방송된 자막 인용문(quote)을 매핑하여 추출합니다.
    중복 에피소드 및 자산 도배를 방지하기 위해 고유 자산(Unique Asset) 기준으로 선별합니다.
    """
    parsed = pd.read_csv(PARSED_CSV)
    parsed["dt_str"] = pd.to_datetime(parsed["date"].astype(str), format="%Y%m%d").dt.strftime("%Y-%m-%d")
    
    # GK 알파 PnL 기준 정렬 및 자산별 중복 제거 (다양한 레전드 에피소드 확보)
    best_calls = panel_df.sort_values(by="pnl_gk", ascending=False).drop_duplicates(subset=["asset_id"]).head(top_k)
    worst_calls = panel_df.sort_values(by="pnl_gk", ascending=True).drop_duplicates(subset=["asset_id"]).head(top_k)
    
    def enrich_call(row, is_best: bool):
        entry_date = row["entry_date"]
        asset = row["asset_id"]
        
        # 1차: 진입일 직전 28일 이내 방송된 영상 청크 검색
        window_start = (pd.to_datetime(entry_date) - pd.Timedelta(days=28)).strftime("%Y-%m-%d")
        sub_chunks = parsed[
            (parsed["dt_str"] <= entry_date) &
            (parsed["dt_str"] >= window_start) &
            (parsed["relevant"] == 1) &
            (parsed["asset_id"] == asset)
        ].sort_values(by=["date", "confidence"], ascending=[False, False])
        
        # 2차 fallback: 28일 이내 없으면 과거 전체 중 최고 확신도 청크 검색
        if sub_chunks.empty:
            sub_chunks = parsed[
                (parsed["dt_str"] <= entry_date) &
                (parsed["relevant"] == 1) &
                (parsed["asset_id"] == asset)
            ].sort_values(by="confidence", ascending=False)
        
        best_quote = ""
        video_title = ""
        video_id = ""
        confidence = 0.0
        
        if not sub_chunks.empty:
            top_chunk = sub_chunks.iloc[0]
            best_quote = str(top_chunk.get("quote", "")).strip()
            video_title = str(top_chunk.get("title", "")).strip()
            video_id = str(top_chunk.get("video_id", "")).strip()
            confidence = float(top_chunk.get("confidence", 0.0))
            
        return {
            "entry_date": entry_date,
            "exit_date": row["exit_date"],
            "asset_id": asset,
            "weekly_return_pct": round(row["forward_return"] * 100, 2),
            "weight_pct": round(row["weight_gk"] * 100, 2),
            "pnl_contribution_pct": round(row["pnl_gk"] * 100, 2),
            "video_title": video_title,
            "video_url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
            "quote": best_quote,
            "confidence": confidence,
            "analysis": "정방향 매수 성공" if (row["weight_gk"] > 0 and row["forward_return"] > 0) else (
                "역발상 숏 성공" if (row["weight_gk"] < 0 and row["forward_return"] < 0) else (
                    "매수 후 급락" if (row["weight_gk"] > 0 and row["forward_return"] < 0) else "숏 후 급등"
                )
            )
        }
        
    best_list = [enrich_call(r, True) for _, r in best_calls.iterrows()]
    worst_list = [enrich_call(r, False) for _, r in worst_calls.iterrows()]
    
    return {
        "top_5_best_calls": best_list,
        "top_5_worst_calls": worst_list
    }


def main():
    print("=== T9 통합 백테스트 시작 ===")
    backtester = P24IntegratedBacktester()
    perf_df, metrics, all_panel = backtester.run_all_strategies()
    
    # 1. Equity Curves 시계열 저장
    equity_df = pd.DataFrame({"date": perf_df["date"]})
    for col in perf_df.columns:
        if col.startswith("ret_"):
            equity_df[col.replace("ret_", "cum_")] = (1.0 + perf_df[col]).cumprod()
            
    equity_path = DATA_DIR / "t9_equity_curves.csv"
    equity_df.to_csv(equity_path, index=False, encoding="utf-8")
    print(f"누적 자산 시계열(Equity Curves) 저장 완료: {equity_path} (총 {len(equity_df)}주)")
    
    # 2. 결과 JSON 저장
    results_path = DATA_DIR / "t9_backtest_results.json"
    results_payload = {
        "sample_period": {
            "start": str(perf_df["date"].min()),
            "end": str(perf_df["exit_date"].max()),
            "total_weeks": len(perf_df),
        },
        "strategy_metrics": metrics
    }
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results_payload, f, ensure_ascii=False, indent=2)
    print(f"통합 백테스트 지표 결과 저장 완료: {results_path}")
    
    # 3. 최고/최악 판단 Top 5 추출 및 저장
    calls_payload = extract_top_bottom_calls(all_panel, top_k=5)
    calls_path = DATA_DIR / "t9_top_bottom_calls.json"
    with open(calls_path, "w", encoding="utf-8") as f:
        json.dump(calls_payload, f, ensure_ascii=False, indent=2)
    print(f"개념글용 Top/Bottom 5 판단 및 인용문 저장 완료: {calls_path}")
    
    # 4. 콘솔 요약 테이블 출력
    print("\n" + "=" * 95)
    print(f"{'Strategy / Benchmark':<24} | {'CumRet':<9} | {'CAGR':<8} | {'Sharpe':<7} | {'MDD':<8} | {'WinRate':<7} | {'IR(P24)':<7}")
    print("-" * 95)
    
    for name, m in metrics.items():
        clean_name = name.replace("ret_", "")
        print(f"{clean_name:<24} | {m['cumulative_return']*100:+7.2f}% | {m['cagr']*100:+6.2f}% | {m['sharpe_ratio']:+6.2f} | {m['max_drawdown']*100:6.2f}% | {m['win_rate']*100:5.1f}% | {m['information_ratio']:+6.2f}")
    print("=" * 95 + "\n")
    print("=== T9 통합 백테스트 완료 ===")


if __name__ == "__main__":
    main()
