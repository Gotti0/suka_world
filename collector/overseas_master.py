"""
overseas_master.py — 해외 종목·거시지표 마스터 수집기

CpUtil.CpUsCode 를 사용하여 해외 개별주식, 지수, 환율, 원자재 등
6개 카테고리의 코드·종목명을 수집한다.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# UsType → category 매핑
USTYPE_MAP = {
    2: "COUNTRY_INDEX",     # 국가대표 지수
    3: "SECTOR_INDEX",      # 업종 지수
    4: "STOCK",             # 해외 개별주식
    5: "DR",                # 한국 ADR
    6: "COMMODITY",         # 상품선물/원자재/반도체
    7: "EXCHANGE_RATE",     # 환율
}


def fetch_all() -> list[dict]:
    """해외 6개 카테고리 전 종목 마스터 데이터를 수집한다."""
    import win32com.client

    us_code_mgr = win32com.client.Dispatch("CpUtil.CpUsCode")
    now = datetime.now().isoformat()
    universe = []

    for us_type, category in USTYPE_MAP.items():
        codes = us_code_mgr.GetUsCodeList(us_type)
        logger.info("%s (UsType=%d) 종목 수: %d", category, us_type, len(codes))

        from tqdm import tqdm
        for code in tqdm(codes, desc=f"{category[:10]} 마스터", leave=False, dynamic_ncols=True):
            name = us_code_mgr.GetNameByUsCode(code)
            universe.append({
                "us_code": code,
                "us_name": name,
                "us_type": us_type,
                "category": category,
                "updated_at": now,
            })

    logger.info("해외 마스터 총 %d건 수집 완료", len(universe))
    return universe


def save(db_writer, data: list[dict] | None = None) -> int:
    """수집 데이터를 overseas_master 테이블에 안전하게 갱신 적재한다."""
    if data is None:
        data = fetch_all()
    return db_writer.upsert_many("overseas_master", data)
