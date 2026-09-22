"""
stock_master.py — 국내 종목 마스터 수집기

CpUtil.CpCodeMgr 을 사용하여 KOSPI + KOSDAQ 전 종목의
종목코드·종목명·시장구분·NXT 거래 가능 여부를 수집한다.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# 시장 코드 → 시장명 매핑
MARKET_MAP = {1: "KOSPI", 2: "KOSDAQ"}


def fetch_all() -> list[dict]:
    """KOSPI + KOSDAQ 전 종목 마스터 데이터를 수집한다."""
    import win32com.client

    code_mgr = win32com.client.Dispatch("CpUtil.CpCodeMgr")
    now = datetime.now().isoformat()
    universe = []

    for market_code, market_name in MARKET_MAP.items():
        codes = code_mgr.GetStockListByMarket(market_code)
        logger.info("%s 종목 수: %d", market_name, len(codes))

        from tqdm import tqdm
        for code in tqdm(codes, desc=f"{market_name} 마스터 수집", leave=False, dynamic_ncols=True):
            name = code_mgr.CodeToName(code)

            try:
                is_nxt = 1 if code_mgr.IsNxtTrdPsbl(code) else 0
            except AttributeError:
                is_nxt = 0

            universe.append({
                "stock_code": code,
                "stock_name": name,
                "market": market_name,
                "is_nxt_eligible": is_nxt,
                "updated_at": now,
            })

    logger.info("국내 종목 마스터 총 %d건 수집 완료", len(universe))
    return universe


def save(db_writer, data: list[dict] | None = None) -> int:
    """수집 데이터를 stock_master 테이블에 안전하게 갱신 적재한다."""
    if data is None:
        data = fetch_all()
    count = db_writer.upsert_many("stock_master", data)
    db_writer.mark_stock_master_seen([row["stock_code"] for row in data])
    return count
