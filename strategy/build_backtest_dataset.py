"""백테스트 데이터셋 구축 모듈 (T7)

P24 가격 패널(data/p24_weekly_close.csv, data/p24_daily_close.csv)과
주간 신호 집계 데이터(data/p24_weekly_signals.csv)를 결합하여
T9 백테스트에 필요한 통합 데이터셋을 생성합니다.

산출물:
1. data/p24_backtest_dataset_weekly.csv : (진입일, 청산일, 자산군) 단위의 상세 패널
2. data/p24_backtest_summary_weekly.csv : 주차별 전략 포트폴리오 수익률 및 벤치마크 요약
3. data/p24_backtest_dataset_daily.csv  : 일별 포지션 및 일간 수익률 패널
4. data/t7_summary_report.json          : T7 전체 정합성 및 요약 리포트
"""

import json
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
WEEKLY_SIGNALS_CSV = DATA_DIR / "p24_weekly_signals.csv"
WEEKLY_CLOSE_CSV = DATA_DIR / "p24_weekly_close.csv"
DAILY_CLOSE_CSV = DATA_DIR / "p24_daily_close.csv"
P24_YAML = BASE_DIR / "strategy" / "p24.yaml"


def load_p24_assets() -> List[str]:
    with open(P24_YAML, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return [a["id"] for a in cfg["assets"]]


def build_weekly_backtest_dataset():
    """주간 백테스트 상세 패널 및 주차별 요약 데이터셋을 구축합니다."""
    assets = load_p24_assets()
    
    # 1. 가격 데이터 로드
    wk_close = pd.read_csv(WEEKLY_CLOSE_CSV)
    wk_close["dt"] = pd.to_datetime(wk_close["date"])
    wk_close = wk_close.sort_values("dt").reset_index(drop=True)
    
    # 2. 신호 데이터 로드
    sig_df = pd.read_csv(WEEKLY_SIGNALS_CSV)
    sig_df["dt"] = pd.to_datetime(sig_df["date"])
    
    # 3. 금요일 날짜 시퀀스 매칭
    # 주 t 금요일에 진입 -> 주 t+1 금요일에 청산
    # t번째 행의 가격 P_t, t+1번째 행의 가격 P_{t+1}, 수익률 R_{t+1} = P_{t+1}/P_t - 1
    dates_list = wk_close["dt"].tolist()
    date_to_idx = {d: i for i, d in enumerate(dates_list)}
    
    # 신호가 있는 주차 목록
    sig_dates = sorted(sig_df["dt"].unique())
    
    panel_rows = []
    weekly_summary_rows = []
    
    for sig_dt in sig_dates:
        if sig_dt not in date_to_idx:
            continue
        idx_t = date_to_idx[sig_dt]
        if idx_t + 1 >= len(dates_list):
            # 다음 주 금요일 가격이 없으면 청산 불가 (마지막 주차)
            continue
            
        exit_dt = dates_list[idx_t + 1]
        entry_date_str = sig_dt.strftime("%Y-%m-%d")
        exit_date_str = exit_dt.strftime("%Y-%m-%d")
        
        # 주 t의 가격 행 & 주 t+1의 가격 행
        row_t = wk_close.iloc[idx_t]
        row_t1 = wk_close.iloc[idx_t + 1]
        
        # 벤치마크 주간 수익률
        ret_djgt = float(row_t1["DJGT"] / row_t["DJGT"] - 1.0) if pd.notna(row_t["DJGT"]) and row_t["DJGT"] > 0 else 0.0
        ret_sox = float(row_t1["SOX"] / row_t["SOX"] - 1.0) if pd.notna(row_t["SOX"]) and row_t["SOX"] > 0 else 0.0
        
        # P24 개별 자산 주간 수익률 계산
        asset_rets = {}
        for a in assets:
            p0 = row_t[a]
            p1 = row_t1[a]
            if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                asset_rets[a] = float(p1 / p0 - 1.0)
            else:
                asset_rets[a] = 0.0
                
        ret_p24_ew = float(np.mean(list(asset_rets.values())))
        
        # 해당 주차의 신호 추출
        w_sig = sig_df[sig_df["dt"] == sig_dt].set_index("asset_id")
        
        # 전략별 포지션
        pos_base_L4 = {}
        pos_inv_L4 = {}
        pos_long_only_L4 = {}
        pos_base_L1 = {}
        pos_delta_D4 = {}
        
        for a in assets:
            if a in w_sig.index:
                s_row = w_sig.loc[a]
                pos_base_L4[a] = int(s_row["sign_L4"])
                pos_inv_L4[a] = -int(s_row["sign_L4"])
                pos_long_only_L4[a] = max(int(s_row["sign_L4"]), 0)
                pos_base_L1[a] = int(s_row["sign_L1"])
                pos_delta_D4[a] = int(s_row["sign_D4"])
            else:
                pos_base_L4[a] = 0
                pos_inv_L4[a] = 0
                pos_long_only_L4[a] = 0
                pos_base_L1[a] = 0
                pos_delta_D4[a] = 0
                
        # 가중치 계산 함수 (1/N 동일가중 정규화)
        def calc_weights(pos_dict):
            active_count = sum(1 for p in pos_dict.values() if p != 0)
            long_count = sum(1 for p in pos_dict.values() if p > 0)
            short_count = sum(1 for p in pos_dict.values() if p < 0)
            
            w_active = {}
            w_dollar_neutral = {}
            w_fixed24 = {}
            
            for a in assets:
                p = pos_dict[a]
                # 1. 활성 포지션 동일가중 (합계 절대값 1.0)
                w_active[a] = (p / active_count) if active_count > 0 else 0.0
                
                # 2. 롱/숏 각각 50%씩 배분 (달러 중립: 롱 합 0.5, 숏 합 -0.5)
                if p > 0:
                    w_dollar_neutral[a] = 0.5 / long_count if long_count > 0 else 0.0
                elif p < 0:
                    w_dollar_neutral[a] = -0.5 / short_count if short_count > 0 else 0.0
                else:
                    w_dollar_neutral[a] = 0.0
                    
                # 3. 고정 24분할 동일가중 (미보유 자산은 현금 0%)
                w_fixed24[a] = p / 24.0
                
            return w_active, w_dollar_neutral, w_fixed24, active_count, long_count, short_count
            
        w_base_act, w_base_dn, w_base_f24, n_base_act, n_base_long, n_base_short = calc_weights(pos_base_L4)
        w_inv_act, _, _, _, _, _ = calc_weights(pos_inv_L4)
        w_lo_act, _, _, n_lo_act, _, _ = calc_weights(pos_long_only_L4)
        w_l1_act, _, _, n_l1_act, _, _ = calc_weights(pos_base_L1)
        w_d4_act, _, _, n_d4_act, _, _ = calc_weights(pos_delta_D4)
        
        # 포트폴리오 주간 수익률
        port_ret_base_L4 = sum(w_base_act[a] * asset_rets[a] for a in assets)
        port_ret_inv_L4 = sum(w_inv_act[a] * asset_rets[a] for a in assets)
        port_ret_lo_L4 = sum(w_lo_act[a] * asset_rets[a] for a in assets)
        port_ret_base_L1 = sum(w_l1_act[a] * asset_rets[a] for a in assets)
        port_ret_delta_D4 = sum(w_d4_act[a] * asset_rets[a] for a in assets)
        port_ret_base_f24 = sum(w_base_f24[a] * asset_rets[a] for a in assets)
        
        # 패널 행 추가
        for a in assets:
            s_row = w_sig.loc[a] if a in w_sig.index else None
            
            panel_rows.append({
                "entry_date": entry_date_str,
                "exit_date": exit_date_str,
                "asset_id": a,
                "price_entry": round(float(row_t[a]), 6) if pd.notna(row_t[a]) else np.nan,
                "price_exit": round(float(row_t1[a]), 6) if pd.notna(row_t1[a]) else np.nan,
                "forward_return": round(asset_rets[a], 6),
                # 신호 원시 점수
                "score_L4": int(s_row["score_L4"]) if s_row is not None else 0,
                "score_L1": int(s_row["score_L1"]) if s_row is not None else 0,
                "score_D4": int(s_row["score_D4"]) if s_row is not None else 0,
                "mentions_L4": int(s_row["mentions_L4"]) if s_row is not None else 0,
                # 포지션 부호 (+1, 0, -1)
                "pos_base_L4": pos_base_L4[a],
                "pos_inv_L4": pos_inv_L4[a],
                "pos_long_only_L4": pos_long_only_L4[a],
                "pos_base_L1": pos_base_L1[a],
                "pos_delta_D4": pos_delta_D4[a],
                # 가중치 (기본 활성 동일가중)
                "weight_base_L4": round(w_base_act[a], 6),
                "weight_inv_L4": round(w_inv_act[a], 6),
                "weight_long_only_L4": round(w_lo_act[a], 6),
                "weight_fixed24_L4": round(w_base_f24[a], 6),
                # 자산별 기여 수익률 (weight * return)
                "contrib_base_L4": round(w_base_act[a] * asset_rets[a], 6),
                "contrib_inv_L4": round(w_inv_act[a] * asset_rets[a], 6),
                "contrib_long_only_L4": round(w_lo_act[a] * asset_rets[a], 6),
            })
            
        # 주간 요약 행
        weekly_summary_rows.append({
            "entry_date": entry_date_str,
            "exit_date": exit_date_str,
            # 포지션 개수
            "active_assets_L4": n_base_act,
            "long_assets_L4": n_base_long,
            "short_assets_L4": n_base_short,
            # 전략별 주간 수익률
            "ret_base_L4": round(port_ret_base_L4, 6),
            "ret_inv_L4": round(port_ret_inv_L4, 6),
            "ret_long_only_L4": round(port_ret_lo_L4, 6),
            "ret_base_L1": round(port_ret_base_L1, 6),
            "ret_delta_D4": round(port_ret_delta_D4, 6),
            "ret_base_f24": round(port_ret_base_f24, 6),
            # 벤치마크 수익률
            "ret_P24_EW": round(ret_p24_ew, 6),
            "ret_DJGT": round(ret_djgt, 6),
            "ret_SOX": round(ret_sox, 6),
        })
        
    dataset_weekly = pd.DataFrame(panel_rows)
    summary_weekly = pd.DataFrame(weekly_summary_rows)
    
    return dataset_weekly, summary_weekly


def build_daily_backtest_dataset(dataset_weekly: pd.DataFrame):
    """일별 가격 패널과 활성 주간 포지션을 매핑하여 일별 백테스트 패널을 구축합니다."""
    assets = load_p24_assets()
    daily_close = pd.read_csv(DAILY_CLOSE_CSV)
    daily_close["dt"] = pd.to_datetime(daily_close["date"])
    daily_close = daily_close.sort_values("dt").reset_index(drop=True)
    
    # 일별 수익률 계산
    daily_rets = daily_close.set_index("dt")[assets].pct_change(fill_method=None).fillna(0)
    
    # 각 일자가 속한 주간 진입일 매핑
    # 주간 진입일 entry_date (금요일 종가) ~ exit_date (다음 주 금요일 종가)
    # 해당 기간 동안의 일간 거래일에 포지션 매핑
    unique_weeks = dataset_weekly[["entry_date", "exit_date"]].drop_duplicates()
    
    daily_rows = []
    
    for _, w_row in unique_weeks.iterrows():
        e_str = w_row["entry_date"]
        x_str = w_row["exit_date"]
        e_dt = pd.to_datetime(e_str)
        x_dt = pd.to_datetime(x_str)
        
        # 해당 주차의 자산별 포지션 및 가중치 추출
        sub_panel = dataset_weekly[dataset_weekly["entry_date"] == e_str].set_index("asset_id")
        
        # 금요일 진입 이후 ~ 다음 금요일까지의 일간 거래일
        # 예: e_dt가 금요일이면 다음 영업일(월)부터 x_dt(금)까지가 보유 일수
        trading_days = daily_close[(daily_close["dt"] > e_dt) & (daily_close["dt"] <= x_dt)]["dt"].tolist()
        
        for t_day in trading_days:
            t_str = t_day.strftime("%Y-%m-%d")
            for a in assets:
                p_base = int(sub_panel.loc[a, "pos_base_L4"])
                w_base = float(sub_panel.loc[a, "weight_base_L4"])
                d_ret = float(daily_rets.loc[t_day, a]) if t_day in daily_rets.index else 0.0
                
                daily_rows.append({
                    "date": t_str,
                    "week_entry": e_str,
                    "week_exit": x_str,
                    "asset_id": a,
                    "daily_return": round(d_ret, 6),
                    "pos_base_L4": p_base,
                    "weight_base_L4": round(w_base, 6),
                    "contrib_base_L4": round(w_base * d_ret, 6),
                })
                
    dataset_daily = pd.DataFrame(daily_rows)
    return dataset_daily


def compute_t7_summary_report(dataset_weekly: pd.DataFrame, summary_weekly: pd.DataFrame) -> Dict:
    """T7 데이터셋에 대한 종합 검증 지표 및 통계 리포트를 작성합니다."""
    assets = load_p24_assets()
    
    total_weeks = len(summary_weekly)
    start_date = summary_weekly["entry_date"].min()
    end_date = summary_weekly["exit_date"].max()
    
    # 누적 수익률 계산 (복리)
    cum_returns = {}
    for col in ["ret_base_L4", "ret_inv_L4", "ret_long_only_L4", "ret_base_L1", "ret_delta_D4", "ret_base_f24", "ret_P24_EW", "ret_DJGT", "ret_SOX"]:
        cum = float(np.prod(1.0 + summary_weekly[col]) - 1.0)
        cum_returns[col] = round(cum, 4)
        
    # 샤프 비율 (주간 -> 연율화, 무위험 이자율 0 가정)
    sharpe_ratios = {}
    for col in ["ret_base_L4", "ret_inv_L4", "ret_long_only_L4", "ret_base_L1", "ret_delta_D4", "ret_P24_EW", "ret_DJGT"]:
        s = summary_weekly[col]
        mean = s.mean()
        std = s.std()
        sr = float((mean / std) * np.sqrt(52)) if std > 0 else 0.0
        sharpe_ratios[col] = round(sr, 4)
        
    # 자산별 활성 통계
    asset_stats = {}
    for a in assets:
        sub = dataset_weekly[dataset_weekly["asset_id"] == a]
        long_weeks = int((sub["pos_base_L4"] == 1).sum())
        short_weeks = int((sub["pos_base_L4"] == -1).sum())
        zero_weeks = int((sub["pos_base_L4"] == 0).sum())
        avg_score = float(sub["score_L4"].mean())
        
        # 신호와 전진수익률 간의 상관관계 (IC: Information Coefficient)
        valid = sub.dropna(subset=["score_L4", "forward_return"])
        ic = float(valid["score_L4"].corr(valid["forward_return"])) if len(valid) > 10 else 0.0
        
        asset_stats[a] = {
            "long_weeks": long_weeks,
            "short_weeks": short_weeks,
            "zero_weeks": zero_weeks,
            "avg_score_L4": round(avg_score, 2),
            "signal_return_ic": round(ic, 4),
        }
        
    report = {
        "report_title": "T7 P24 Signal Aggregation and Backtest Dataset Summary",
        "sample_period": {
            "start_entry_date": start_date,
            "end_exit_date": end_date,
            "total_weeks": total_weeks,
            "total_panel_rows": len(dataset_weekly),
        },
        "cumulative_returns": cum_returns,
        "annualized_sharpe": sharpe_ratios,
        "average_active_assets_per_week": round(float(summary_weekly["active_assets_L4"].mean()), 2),
        "average_long_assets_per_week": round(float(summary_weekly["long_assets_L4"].mean()), 2),
        "average_short_assets_per_week": round(float(summary_weekly["short_assets_L4"].mean()), 2),
        "asset_level_statistics": asset_stats,
        "data_integrity_check": {
            "missing_prices_in_panel": int(dataset_weekly["price_entry"].isna().sum() + dataset_weekly["price_exit"].isna().sum()),
            "missing_returns": int(dataset_weekly["forward_return"].isna().sum()),
            "all_24_assets_present": bool(set(dataset_weekly["asset_id"].unique()) == set(assets)),
        }
    }
    return report


def main():
    print("=== T7 백테스트 데이터셋 구축 시작 ===")
    
    # 1. 주간 데이터셋 구축
    dataset_weekly, summary_weekly = build_weekly_backtest_dataset()
    weekly_path = DATA_DIR / "p24_backtest_dataset_weekly.csv"
    summary_path = DATA_DIR / "p24_backtest_summary_weekly.csv"
    
    dataset_weekly.to_csv(weekly_path, index=False, encoding="utf-8")
    summary_weekly.to_csv(summary_path, index=False, encoding="utf-8")
    print(f"주간 백테스트 상세 패널 저장 완료: {weekly_path} (총 {len(dataset_weekly)}행)")
    print(f"주간 포트폴리오 요약 저장 완료: {summary_path} (총 {len(summary_weekly)}주차)")
    
    # 2. 일별 데이터셋 구축
    dataset_daily = build_daily_backtest_dataset(dataset_weekly)
    daily_path = DATA_DIR / "p24_backtest_dataset_daily.csv"
    dataset_daily.to_csv(daily_path, index=False, encoding="utf-8")
    print(f"일별 백테스트 상세 패널 저장 완료: {daily_path} (총 {len(dataset_daily)}행)")
    
    # 3. 요약 리포트 작성
    report = compute_t7_summary_report(dataset_weekly, summary_weekly)
    report_path = DATA_DIR / "t7_summary_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"T7 요약 리포트 저장 완료: {report_path}")
    
    print("\n--- [T7 백테스트 성과 예비 프리뷰] ---")
    print(f"분석 기간: {report['sample_period']['start_entry_date']} ~ {report['sample_period']['end_exit_date']} ({report['sample_period']['total_weeks']}주)")
    print(f"주당 평균 활성 포지션: {report['average_active_assets_per_week']}개 (롱 {report['average_long_assets_per_week']}개, 숏 {report['average_short_assets_per_week']}개)")
    print(f"누적 수익률 요약:")
    for k, v in report["cumulative_returns"].items():
        print(f"  {k:20s}: {v*100:+.2f}%")
        
    print(f"\n연율화 샤프비율:")
    for k, v in report["annualized_sharpe"].items():
        print(f"  {k:20s}: {v:+.4f}")
        
    print(f"\n데이터 무결성 점검:")
    print(json.dumps(report["data_integrity_check"], ensure_ascii=False, indent=2))
    
    print("\n=== T7 백테스트 데이터셋 구축 완료 ===")


if __name__ == "__main__":
    main()
