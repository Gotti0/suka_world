"""
Unit tests for FastAPI Dashboard Backend API (dashboard/app.py)
"""

import pytest
from fastapi.testclient import TestClient

from dashboard.app import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["app"] == "suka_dashboard"
    assert data["total_weeks"] == 280
    assert data["assets_count"] == 24


def test_summary_endpoint(client):
    response = client.get("/api/summary")
    assert response.status_code == 200
    data = response.json()
    assert "sample_period" in data
    assert "strategy_metrics" in data
    
    metrics = data["strategy_metrics"]
    assert "ret_gk_net" in metrics
    assert "ret_base_L4" in metrics
    assert "ret_inv_L4" in metrics
    assert "ret_regime_net" in metrics
    assert "ret_DJGT" in metrics

    # Verify GK Quant Ensemble Net metrics
    gk = metrics["ret_gk_net"]
    assert gk["cumulative_return"] > 2.0  # +243%
    assert gk["sharpe_ratio"] > 2.0      # 2.14
    assert gk["max_drawdown"] < 0        # -10.6%


def test_equity_endpoint(client):
    response = client.get("/api/equity")
    assert response.status_code == 200
    data = response.json()
    assert "dates" in data
    assert "series" in data
    assert "drawdowns" in data
    assert len(data["dates"]) == 280

    series = data["series"]
    assert "cum_gk_net" in series
    assert len(series["cum_gk_net"]) == 280
    assert "cum_DJGT" in series

    # Drawdown verification: max should be 0.0, min should match max drawdown
    dd = data["drawdowns"]["cum_gk_net"]
    assert max(dd) <= 0.0001
    assert min(dd) < -0.05


def test_calls_endpoint(client):
    response = client.get("/api/calls")
    assert response.status_code == 200
    data = response.json()
    assert "top_5_best_calls" in data
    assert "top_5_worst_calls" in data
    assert len(data["top_5_best_calls"]) == 5
    assert len(data["top_5_worst_calls"]) == 5

    best_call = data["top_5_best_calls"][0]
    assert "entry_date" in best_call
    assert "asset_id" in best_call
    assert "weekly_return_pct" in best_call
    assert "video_url" in best_call
    assert "quote" in best_call
    assert "youtube.com" in best_call["video_url"]


def test_assets_endpoint(client):
    response = client.get("/api/assets")
    assert response.status_code == 200
    assets = response.json()
    assert isinstance(assets, list)
    assert len(assets) == 24

    asset_ids = {a["id"] for a in assets}
    assert "MEMORY" in asset_ids
    assert "TECH" in asset_ids
    assert "ENERGY" in asset_ids
    assert "BANKS" in asset_ids

    # Check Memory asset metadata & IC
    mem = next(a for a in assets if a["id"] == "MEMORY")
    assert "signal_return_ic" in mem
    assert mem["signal_return_ic"] > 0.1  # Memory has strong positive IC (+0.196)


def test_asset_signals_endpoint_valid(client):
    response = client.get("/api/signals/MEMORY")
    assert response.status_code == 200
    data = response.json()
    assert data["asset_id"] == "MEMORY"
    assert "summary" in data
    assert data["summary"]["total_weeks"] == 280
    assert "history" in data
    assert len(data["history"]) == 280

    # Test individual weekly record structure
    first_record = data["history"][0]
    assert "entry_date" in first_record
    assert "exit_date" in first_record
    assert "price_entry" in first_record
    assert "score_L4" in first_record
    assert "pos_base_L4" in first_record


def test_asset_signals_endpoint_invalid(client):
    response = client.get("/api/signals/UNKNOWN_ASSET_XYZ")
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data


def test_index_html_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert "슈카 ETF (P24) 통합 백테스트 퀀트 대시보드" in html
    assert "equityChart" in html
    assert "icChart" in html
    assert "assetDropdown" in html
    assert "apiFetch('/api/summary')" in html
    assert "apiFetch" in html
