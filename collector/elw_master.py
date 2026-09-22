"""
elw_master.py — ELW 종목 마스터 수집기

CpUtil.CpElwCode + CpSysDib.Elw를 사용해 ELW 메타데이터를 수집한다.
"""

import logging
from datetime import datetime

from .cybos_conn import CybosConnection

logger = logging.getLogger(__name__)


_MOJIBAKE_HINT_CHARS = set("¹ºÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞß·»¼½¾")


def _contains_hangul(text: str) -> bool:
    return any("가" <= ch <= "힣" for ch in text)


def _looks_like_mojibake(text: str) -> bool:
    if not text:
        return False
    if _contains_hangul(text):
        return False
    return any(ch in _MOJIBAKE_HINT_CHARS for ch in text)


def _repair_mojibake(text: str) -> str:
    if not _looks_like_mojibake(text):
        return text
    try:
        repaired = text.encode("latin1").decode("cp949")
    except Exception:
        return text
    return repaired if _contains_hangul(repaired) else text


def _normalize_text(value, default: str = "") -> str:
    if value is None:
        return default

    if isinstance(value, bytes):
        for enc in ("utf-8", "cp949", "euc-kr"):
            try:
                return _repair_mojibake(value.decode(enc))
            except Exception:
                continue
        return value.decode("utf-8", errors="replace")

    text = str(value)
    if not text:
        return default
    return _repair_mojibake(text)


def _safe_header(elw_obj, idx, default=None):
    try:
        return elw_obj.GetHeaderValue(idx)
    except Exception:
        return default


def _normalize_underlying_code(v):
    if isinstance(v, (list, tuple)):
        return ",".join(str(x) for x in v)
    return _normalize_text(v, "")


def _normalize_option_type(raw_value) -> str:
    """API의 콜/풋 구분값을 CALL/PUT으로 정규화한다."""
    text = _normalize_text(raw_value, "").upper()
    if "콜" in text or "CALL" in text:
        return "CALL"
    if "풋" in text or "PUT" in text:
        return "PUT"
    return ""


def fetch_all(limit: int | None = None) -> list[dict]:
    """ELW 마스터를 수집한다 (호가형성 종목 필터 적용)."""
    import win32com.client

    conn = CybosConnection()
    conn.wait_if_needed()

    elw_all = win32com.client.Dispatch("CpSysDib.ElwAll")
    
    # 0: 조회구분 (1: 전체), 1: 기초자산구분 (1: 일반종목), 2: 정렬구분 (1: 코드순)
    elw_all.SetInputValue(0, ord('1'))
    elw_all.SetInputValue(1, ord('1'))
    elw_all.SetInputValue(2, 1)
    # 3: 필터구분 ('2': 호가형성종목) -> 좀비 종목 원천 차단
    elw_all.SetInputValue(3, ord('2'))

    rows = []
    now = datetime.now().isoformat()

    while True:
        conn.wait_if_needed()
        elw_all.BlockRequest()

        if elw_all.GetDibStatus() != 0:
            logger.warning("ElwAll 조회 실패: %s", elw_all.GetDibMsg1())
            break

        count = elw_all.GetHeaderValue(0)
        for i in range(count):
            code = _normalize_text(elw_all.GetDataValue(0, i), "")
            elw_name = _normalize_text(elw_all.GetDataValue(3, i), "")
            underlying_code = _normalize_underlying_code(elw_all.GetDataValue(36, i))
            
            # Fallback for missing index underlying codes
            if not underlying_code:
                if "KOSPI200" in elw_name.upper():
                    underlying_code = "K2G01P"
                elif "KOSDAQ" in elw_name.upper():
                    underlying_code = "Q5G01P"

            rows.append({
                "elw_code": code,
                "elw_name": elw_name, # 초단축명 활용
                "elw_short_name": elw_name,
                "option_type": _normalize_option_type(elw_all.GetDataValue(4, i)),
                "underlying_code": underlying_code,
                "underlying_name": _normalize_text(elw_all.GetDataValue(37, i), ""),
                "issue_quantity": elw_all.GetDataValue(9, i) or 0,
                "unit_ratio": float(elw_all.GetDataValue(11, i) or 0.0),
                "strike_price": float(elw_all.GetDataValue(10, i) or 0.0),
                "last_trading_date": str(elw_all.GetDataValue(6, i)),
                "maturity_date": str(elw_all.GetDataValue(7, i)),
                "remain_days": int(elw_all.GetDataValue(13, i) or 0),
                "market_state": "2", # 장중(호가형성) 종목임을 보장
                "prev_volume": int(elw_all.GetDataValue(41, i) or 0),
                "is_active": 1,
                "last_seen_at": now,
                "updated_at": now,
            })

            if limit and len(rows) >= limit:
                break
        
        if limit and len(rows) >= limit:
            break
            
        if not elw_all.Continue:
            break

    logger.info("ELW 마스터 수집 완료 (호가형성 종목 필터링): %d건", len(rows))
    return rows


def save(db_writer, data: list[dict] | None = None, limit: int | None = None) -> int:
    """elw_master 테이블에 안전하게 갱신 적재하고 비활성 종목을 처리한다."""
    if data is None:
        data = fetch_all(limit=limit)
    
    count = db_writer.upsert_many("elw_master", data)
    
    # --- Cleanup: 이번 수집에서 발견되지 않은 종목은 비활성화 ---
    if data and not limit:
        now = datetime.now().isoformat()
        # 최근 1시간 이내에 보이지 않은 종목은 비활성 처리 (임계치 1시간)
        # last_seen_at이 현재 수집 시각보다 훨씬 이전이면 시장에서 사라진 것임.
        db_writer.conn.execute(
            "UPDATE elw_master SET is_active = 0 WHERE last_seen_at < ? OR last_seen_at IS NULL",
            (data[0]['last_seen_at'],)
        )
        db_writer.conn.commit()
        logger.info("ELW 마스터 상태 동기화 완료 (비활성 종목 처리 포함)")

    return count
