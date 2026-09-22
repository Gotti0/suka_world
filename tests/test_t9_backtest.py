"""T9 통합 백테스트 결과 및 지표 정합성 단위 테스트"""

import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

DATA_DIR = Path("data")
REVIEW_DIR = Path("review")


def test_t9_files_exist():
    assert (DATA_DIR / "t9_backtest_results.json").exists()
    assert (DATA_DIR / "t9_equity_curves.csv").exists()
    assert (DATA_DIR / "t9_top_bottom_calls.json").exists()
    assert (REVIEW_DIR / "backtest_dashboard.html").exists()


def test_t9_results_structure():
    with open(DATA_DIR / "t9_backtest_results.json", "r", encoding="utf-8") as f:
        res = json.load(f)
        
    assert res["sample_period"]["total_weeks"] == 280
    metrics = res["strategy_metrics"]
    
    # 9종 전략 + 벤치마크 키 존재 확인
    expected_keys = [
        "ret_base_L4", "ret_inv_L4", "ret_long_only_L4", "ret_base_L1", "ret_delta_D4",
        "ret_gk_gross", "ret_gk_net", "ret_regime_net", "ret_P24_EW", "ret_DJGT"
    ]
    for k in expected_keys:
        assert k in metrics
        m = metrics[k]
        assert "cumulative_return" in m
        assert "cagr" in m
        assert "sharpe_ratio" in m
        assert "max_drawdown" in m
        assert "win_rate" in m


def test_t9_equity_curves_consistency():
    eq_df = pd.read_csv(DATA_DIR / "t9_equity_curves.csv")
    assert len(eq_df) == 280
    
    # 시작 시점 값은 1.0 부근이어야 함
    for col in ["cum_gk_net", "cum_base_L4", "cum_P24_EW", "cum_DJGT"]:
        assert col in eq_df.columns
        # 누적 수익률과 마지막 자산 가치 일치 검증: cum_ret == final_val - 1.0 (오차 0.01 이내)
        with open(DATA_DIR / "t9_backtest_results.json", "r", encoding="utf-8") as f:
            res = json.load(f)
        expected_cum = res["strategy_metrics"][col.replace("cum_", "ret_")]["cumulative_return"]
        actual_cum = eq_df[col].iloc[-1] - 1.0
        assert np.isclose(expected_cum, actual_cum, atol=1e-3)


def test_t9_top_bottom_calls_quotes():
    with open(DATA_DIR / "t9_top_bottom_calls.json", "r", encoding="utf-8") as f:
        calls = json.load(f)
        
    assert len(calls["top_5_best_calls"]) == 5
    assert len(calls["top_5_worst_calls"]) == 5
    
    for item in calls["top_5_best_calls"]:
        assert len(item["quote"]) > 0
        assert item["pnl_contribution_pct"] > 0
        assert item["weekly_return_pct"] != 0
        
    for item in calls["top_5_worst_calls"]:
        assert len(item["quote"]) > 0
        assert item["pnl_contribution_pct"] < 0
