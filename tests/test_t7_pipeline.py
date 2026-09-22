"""T7 단계 데이터 정합성 및 무결성 단위 테스트"""

import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

DATA_DIR = Path("data")


def test_t7_files_exist():
    assert (DATA_DIR / "p24_daily_signals.csv").exists()
    assert (DATA_DIR / "p24_daily_signal_matrix.csv").exists()
    assert (DATA_DIR / "p24_weekly_signals.csv").exists()
    assert (DATA_DIR / "p24_backtest_dataset_weekly.csv").exists()
    assert (DATA_DIR / "p24_backtest_summary_weekly.csv").exists()
    assert (DATA_DIR / "p24_backtest_dataset_daily.csv").exists()
    assert (DATA_DIR / "t7_summary_report.json").exists()


def test_t7_weekly_dataset_integrity():
    df = pd.read_csv(DATA_DIR / "p24_backtest_dataset_weekly.csv")
    
    # 1. 24개 자산 존재 확인
    assert df["asset_id"].nunique() == 24
    
    # 2. 결측치 확인
    assert df["price_entry"].isna().sum() == 0
    assert df["price_exit"].isna().sum() == 0
    assert df["forward_return"].isna().sum() == 0
    
    # 3. 수익률 계산 검증: (price_exit / price_entry) - 1 == forward_return (오차 1e-4 이내)
    calc_ret = (df["price_exit"] / df["price_entry"]) - 1.0
    diff = np.abs(calc_ret - df["forward_return"])
    assert (diff < 1e-4).all()
    
    # 4. 포지션 부호 정합성
    assert set(df["pos_base_L4"].unique()).issubset({-1, 0, 1})
    assert set(df["pos_inv_L4"].unique()).issubset({-1, 0, 1})
    assert set(df["pos_long_only_L4"].unique()).issubset({0, 1})


def test_t7_no_lookahead_bias():
    """금요일 t 진입 시점의 신호에 금요일 이후 업로드된 영상이 섞이지 않았는지 검증"""
    sig_df = pd.read_csv(DATA_DIR / "p24_weekly_signals.csv")
    parsed_df = pd.read_csv(DATA_DIR / "t6" / "subagent_run" / "parsed.csv")
    parsed_df["dt"] = pd.to_datetime(parsed_df["date"].astype(str), format="%Y%m%d")
    
    # 샘플 주차 5개 검증
    sample_dates = sig_df["date"].drop_duplicates().sample(5, random_state=42).tolist()
    
    for d_str in sample_dates:
        fri_dt = pd.to_datetime(d_str)
        thu_dt = fri_dt - pd.Timedelta(days=1)
        prev_fri_dt = fri_dt - pd.Timedelta(days=7)
        
        # 주간 신호 데이터에서 1주 멘션 수
        w_sig = sig_df[sig_df["date"] == d_str].set_index("asset_id")
        
        # parsed.csv에서 해당 주차 윈도우 [prev_fri, thu] 필터링
        mask = (parsed_df["dt"] >= prev_fri_dt) & (parsed_df["dt"] <= thu_dt) & (parsed_df["relevant"] == 1)
        expected_chunks = parsed_df[mask]
        
        for asset in ["TECH", "REALEST", "AUTO"]:
            exp_pos = int((expected_chunks[expected_chunks["asset_id"] == asset]["stance"] == 1).sum())
            exp_neg = int((expected_chunks[expected_chunks["asset_id"] == asset]["stance"] == -1).sum())
            exp_net = exp_pos - exp_neg
            
            actual_score = int(w_sig.loc[asset, "score_L1"])
            assert actual_score == exp_net


def test_t7_summary_report():
    with open(DATA_DIR / "t7_summary_report.json", "r", encoding="utf-8") as f:
        report = json.load(f)
        
    assert report["data_integrity_check"]["missing_prices_in_panel"] == 0
    assert report["data_integrity_check"]["missing_returns"] == 0
    assert report["data_integrity_check"]["all_24_assets_present"] is True
    assert report["sample_period"]["total_weeks"] == 280
