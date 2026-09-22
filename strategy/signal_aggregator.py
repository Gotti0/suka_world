"""P24 자산군별 일자별 및 주간 신호 점수 집계 모듈 (T7)

T6 단계에서 완결된 자막 청크 스탠스 판정 결과(data/t6/subagent_run/parsed.csv)를 바탕으로,
1. 일자별(Daily) P24 자산군 신호 집계 (data/p24_daily_signals.csv, data/p24_daily_signal_matrix.csv)
2. 주간 시점 규약(목요일 23:59:59 KST 컷오프)을 준수한 주간 신호 집계 (data/p24_weekly_signals.csv)
   - Level 4주 누적 (본안)
   - Level 1주 (민감도)
   - Delta 4주 변화량 (보조 변형)

Lookahead Bias(미래 참조 편향) 방지:
- 주 t 신호는 금요일 종가 매매 전, 목요일 23:59:59 KST까지 게시된 영상만 반영합니다.
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PARSED_CSV = DATA_DIR / "t6" / "subagent_run" / "parsed.csv"
WEEKLY_CLOSE_CSV = DATA_DIR / "p24_weekly_close.csv"
P24_YAML = BASE_DIR / "strategy" / "p24.yaml"


def load_p24_assets() -> List[str]:
    """p24.yaml에서 정의된 24개 자산군 식별자 목록을 반환합니다."""
    with open(P24_YAML, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return [a["id"] for a in cfg["assets"]]


def aggregate_daily_signals(parsed_df: pd.DataFrame, assets: List[str]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """일자별 자산군 신호 집계 및 피벗 매트릭스를 생성합니다."""
    # relevant == 1인 데이터만 필터링
    rel_df = parsed_df[parsed_df["relevant"] == 1].copy()
    
    # 날짜를 datetime 및 YYYY-MM-DD 문자열로 정규화
    rel_df["dt"] = pd.to_datetime(rel_df["date"].astype(str), format="%Y%m%d")
    rel_df["date_str"] = rel_df["dt"].dt.strftime("%Y-%m-%d")
    
    # 일자 x 자산군 집계
    daily_records = []
    grouped = rel_df.groupby(["date_str", "asset_id"])
    
    for (d_str, asset_id), group in grouped:
        if asset_id not in assets:
            continue
            
        pos = (group["stance"] == 1).sum()
        neu = (group["stance"] == 0).sum()
        neg = (group["stance"] == -1).sum()
        total = len(group)
        net = pos - neg
        
        # 신뢰도 가중치
        weighted_net = float((group["stance"] * group["confidence"]).sum())
        avg_conf = float(group["confidence"].mean())
        
        # 대표 인용문 (신뢰도가 가장 높고 긴 인용문)
        quotes = group["quote"].dropna().tolist()
        best_quote = quotes[0] if quotes else ""
        if len(quotes) > 1:
            # 신뢰도 높은 순 -> 길이 긴 순
            sorted_rows = group.sort_values(by=["confidence", "chunk_idx"], ascending=[False, True])
            for q in sorted_rows["quote"].dropna():
                if len(str(q).strip()) > len(best_quote):
                    best_quote = str(q).strip()
                    break
        
        video_ids = sorted(list(group["video_id"].unique()))
        
        daily_records.append({
            "date": d_str,
            "asset_id": asset_id,
            "mention_count": total,
            "pos_count": int(pos),
            "neu_count": int(neu),
            "neg_count": int(neg),
            "net_stance": int(net),
            "weighted_net_stance": round(weighted_net, 4),
            "avg_confidence": round(avg_conf, 4),
            "primary_quote": best_quote,
            "video_count": len(video_ids),
            "video_ids": ",".join(video_ids)
        })
        
    daily_df = pd.DataFrame(daily_records)
    daily_df = daily_df.sort_values(by=["date", "asset_id"]).reset_index(drop=True)
    
    # 일자별 Net Stance 피벗 매트릭스 생성
    # min_date부터 max_date까지의 모든 일자를 생성하거나 신호가 있는 날짜 기준
    pivot_df = daily_df.pivot(index="date", columns="asset_id", values="net_stance").fillna(0).astype(int)
    
    # 누락된 P24 컬럼 채우기
    for a in assets:
        if a not in pivot_df.columns:
            pivot_df[a] = 0
            
    pivot_df = pivot_df[assets]  # canonical order
    
    return daily_df, pivot_df


def aggregate_weekly_signals(parsed_df: pd.DataFrame, assets: List[str]) -> pd.DataFrame:
    """주간 시점 규약(금요일 종가 리밸런싱, 목요일 23:59:59 KST 컷오프)을 준수하여
    주간 신호 및 4주 누적(Level L4), 1주(Level L1), 4주 변화량(Delta D4)을 집계합니다.
    """
    rel_df = parsed_df[parsed_df["relevant"] == 1].copy()
    rel_df["dt"] = pd.to_datetime(rel_df["date"].astype(str), format="%Y%m%d")
    
    # 가격 패널에서 금요일 날짜 읽기
    wk_close = pd.read_csv(WEEKLY_CLOSE_CSV)
    friday_dates = pd.to_datetime(wk_close["date"]).sort_values().tolist()
    
    # 비디오 시작일 기준 최소 금요일 필터링 (첫 비디오 2021-05-03이므로 2021-05-07 금요일부터)
    min_vid_dt = rel_df["dt"].min()
    max_vid_dt = rel_df["dt"].max()
    
    # 첫 금요일: min_vid_dt 이후 첫 금요일
    # 마지막 금요일: max_vid_dt 이후 첫 금요일
    target_fridays = [f for f in friday_dates if f >= min_vid_dt - pd.Timedelta(days=7)]
    
    weekly_asset_records = []
    
    # 각 금요일 t에 대해:
    # 관측 윈도우: (t - 7일: 직전 주 금요일) ~ (t - 1일: 이번 주 목요일)
    # 예: 2021-05-14(금)의 관측 윈도우 = 2021-05-07 ~ 2021-05-13
    # 2021-05-07(금)의 관측 윈도우 = 2021-04-30 ~ 2021-05-06
    
    # 먼저 각 금요일별 1주 순수 집계(1w net stance)를 계산
    weekly_1w_matrix = {a: [] for a in assets}
    weekly_1w_pos = {a: [] for a in assets}
    weekly_1w_neg = {a: [] for a in assets}
    weekly_1w_mentions = {a: [] for a in assets}
    valid_fridays = []
    
    for fri in target_fridays:
        window_start = fri - pd.Timedelta(days=7)
        window_end = fri - pd.Timedelta(days=1)  # 목요일 23:59:59에 대응
        
        # 윈도우 내 영상 청크 추출
        mask = (rel_df["dt"] >= window_start) & (rel_df["dt"] <= window_end)
        w_chunks = rel_df[mask]
        
        valid_fridays.append(fri)
        
        for a in assets:
            a_chunks = w_chunks[w_chunks["asset_id"] == a]
            pos = int((a_chunks["stance"] == 1).sum())
            neu = int((a_chunks["stance"] == 0).sum())
            neg = int((a_chunks["stance"] == -1).sum())
            tot = len(a_chunks)
            net = pos - neg
            
            weekly_1w_matrix[a].append(net)
            weekly_1w_pos[a].append(pos)
            weekly_1w_neg[a].append(neg)
            weekly_1w_mentions[a].append(tot)
            
    # 데이터프레임으로 변환하여 Rolling 집계
    fri_series = pd.Series(valid_fridays, name="date")
    
    # 각 자산별 신호 계산
    all_weekly_rows = []
    
    for i, fri in enumerate(valid_fridays):
        fri_str = fri.strftime("%Y-%m-%d")
        cutoff_str = (fri - pd.Timedelta(days=1)).strftime("%Y-%m-%d 23:59:59")
        
        for a in assets:
            s_1w = weekly_1w_matrix[a][i]
            pos_1w = weekly_1w_pos[a][i]
            neg_1w = weekly_1w_neg[a][i]
            mentions_1w = weekly_1w_mentions[a][i]
            
            # Level 4주 누적 (최근 4주: i-3 ~ i)
            start_4w_idx = max(0, i - 3)
            s_L4 = sum(weekly_1w_matrix[a][start_4w_idx : i + 1])
            mentions_L4 = sum(weekly_1w_mentions[a][start_4w_idx : i + 1])
            
            # 직전 4주 (i-4 ~ i-1)
            if i >= 1:
                start_prev_4w_idx = max(0, i - 4)
                s_L4_prev = sum(weekly_1w_matrix[a][start_prev_4w_idx : i])
                s_D4 = s_L4 - s_L4_prev
            else:
                s_D4 = 0
                
            # 포지션 부호 (+1, 0, -1)
            sign_L4 = int(np.sign(s_L4))
            sign_L1 = int(np.sign(s_1w))
            sign_D4 = int(np.sign(s_D4))
            
            all_weekly_rows.append({
                "date": fri_str,
                "cutoff_time": cutoff_str,
                "asset_id": a,
                # 1주 지표
                "mentions_1w": mentions_1w,
                "pos_1w": pos_1w,
                "neg_1w": neg_1w,
                "score_L1": s_1w,
                "sign_L1": sign_L1,
                # 4주 누적 지표 (본안)
                "mentions_L4": mentions_L4,
                "score_L4": s_L4,
                "sign_L4": sign_L4,
                # 4주 변화량 지표 (보조 변형)
                "score_D4": s_D4,
                "sign_D4": sign_D4,
            })
            
    weekly_signals_df = pd.DataFrame(all_weekly_rows)
    weekly_signals_df = weekly_signals_df.sort_values(by=["date", "asset_id"]).reset_index(drop=True)
    return weekly_signals_df


def main():
    print("=== T7 신호 점수 집계 시작 ===")
    assets = load_p24_assets()
    print(f"P24 자산군 목록 ({len(assets)}개): {', '.join(assets)}")
    
    print(f"T6 판정 결과 로드: {PARSED_CSV}")
    parsed_df = pd.read_csv(PARSED_CSV)
    print(f"총 청크 수: {len(parsed_df)}, relevant=1 청크 수: {(parsed_df['relevant'] == 1).sum()}")
    
    # 1. 일자별 집계
    daily_df, daily_matrix = aggregate_daily_signals(parsed_df, assets)
    daily_path = DATA_DIR / "p24_daily_signals.csv"
    daily_mat_path = DATA_DIR / "p24_daily_signal_matrix.csv"
    
    daily_df.to_csv(daily_path, index=False, encoding="utf-8")
    daily_matrix.to_csv(daily_mat_path, encoding="utf-8")
    print(f"일자별 신호 집계 저장 완료: {daily_path} (총 {len(daily_df)}행)")
    print(f"일자별 신호 매트릭스 저장 완료: {daily_mat_path} ({daily_matrix.shape[0]}일 x {daily_matrix.shape[1]}자산)")
    
    # 2. 주간 시점 규약 집계
    weekly_df = aggregate_weekly_signals(parsed_df, assets)
    weekly_path = DATA_DIR / "p24_weekly_signals.csv"
    weekly_df.to_csv(weekly_path, index=False, encoding="utf-8")
    print(f"주간 신호 집계 저장 완료: {weekly_path} (총 {len(weekly_df)}행, {weekly_df['date'].nunique()}주)")
    
    # 주요 통계 출력
    print("\n--- 주간 신호(본안 Level 4) 부호 분포 ---")
    print(weekly_df["sign_L4"].value_counts().to_dict())
    
    print("\n--- 자산별 활성 신호(sign_L4 != 0) 주수 Top 10 ---")
    active_by_asset = weekly_df[weekly_df["sign_L4"] != 0]["asset_id"].value_counts().head(10)
    print(active_by_asset.to_string())
    
    print("\n=== T7 신호 점수 집계 완료 ===")


if __name__ == "__main__":
    main()
