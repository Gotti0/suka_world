"""
elw_factors.py — ELW 스냅샷/파생지표 수집기

CpSysDib.MarketEye 로 다종목 ELW 스냅샷을 수집한다.
"""

import logging
from datetime import datetime

from .cybos_conn import CybosConnection

logger = logging.getLogger(__name__)

# 기본 시세/호가 + ELW 지표
DEFAULT_FIELDS = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 16,
    115, 129, 135, 136, 141, 144, 145,
]


FIELD_NAME_MAP = {
    0: "elw_code",
    1: "time",
    2: "diff_sign",
    3: "diff",
    4: "close",
    5: "open",
    6: "high",
    7: "low",
    8: "ask1",
    9: "bid1",
    10: "volume",
    12: "market_segment",
    15: "ask_vol1",
    16: "bid_vol1",
    115: "theoretical_price",
    129: "e_gearing",
    135: "gearing",
    136: "iv",
    141: "break_even_rate",
    144: "parity",
    145: "premium",
}


def _set_exchange_flag(market_eye, exchange: str) -> bool:
    try:
        market_eye.SetInputValue(3, exchange)
        return True
    except Exception:
        try:
            market_eye.SetInputValue(3, ord(exchange))
            return True
        except Exception:
            return False


def _fetch_chunk(codes: list[str], fields: list[int], exchange: str = "A") -> list[dict]:
    import win32com.client

    conn = CybosConnection()
    conn.wait_if_needed()

    market_eye = win32com.client.Dispatch("CpSysDib.MarketEye")

    sorted_fields = sorted(fields)
    market_eye.SetInputValue(0, sorted_fields)
    market_eye.SetInputValue(1, codes)
    market_eye.SetInputValue(2, "2")
    ok = _set_exchange_flag(market_eye, exchange)
    if not ok:
        logger.info("MarketEye exchange flag 미지원 환경 - 기본 범위 사용")

    market_eye.BlockRequest()

    if market_eye.GetDibStatus() != 0:
        logger.warning("MarketEye 조회 실패: %s", market_eye.GetDibMsg1())
        return []

    row_count = market_eye.GetHeaderValue(2)
    now = datetime.now().isoformat()
    today = datetime.now().strftime("%Y%m%d")

    out = []
    for r in range(row_count):
        row = {
            "snapshot_ts": now,
            "date": today,
            "updated_at": now,
        }
        for col_idx, fid in enumerate(sorted_fields):
            val = market_eye.GetDataValue(col_idx, r)
            # SQLite 호환성을 위해 Decimal 등 특수 타입 처리
            if hasattr(val, '__float__'):
                val = float(val)
            row[FIELD_NAME_MAP.get(fid, f"field_{fid}")] = val

        raw_time = row.get("time")
        if raw_time is None:
            row["time"] = ""
        else:
            row["time"] = str(raw_time).zfill(4)

        out.append(row)

    return out


def fetch_snapshot(elw_codes: list[str], fields: list[int] | None = None, exchange: str = "A") -> list[dict]:
    if not elw_codes:
        return []

    use_fields = DEFAULT_FIELDS if fields is None else fields
    all_rows = []

    # MarketEye 최대 200종목
    for i in range(0, len(elw_codes), 200):
        chunk = elw_codes[i:i + 200]
        rows = _fetch_chunk(chunk, use_fields, exchange=exchange)
        all_rows.extend(rows)

    return all_rows


def batch_snapshot(db_writer, elw_codes: list[str], batch_id: str, exchange: str = "A"):
    db_writer.init_batch(batch_id, elw_codes)
    pending = db_writer.get_pending_codes(batch_id)
    logger.info("ELW 스냅샷 배치 시작 — 대상 %d건 (전체 %d건)", len(pending), len(elw_codes))

    # 스냅샷은 200개 단위로 호출하므로 배치 상태 갱신은 chunk 후 개별 DONE 처리
    for i in range(0, len(pending), 200):
        chunk = pending[i:i + 200]
        try:
            rows = fetch_snapshot(chunk, exchange=exchange)
            if rows:
                db_writer.upsert_many("elw_snapshot_factors", rows)
            for code in chunk:
                db_writer.mark_done(batch_id, code)
        except Exception as e:
            logger.error("ELW 스냅샷 chunk 오류: %s", e)
            for code in chunk:
                db_writer.mark_error(batch_id, code, str(e))

    logger.info("ELW 스냅샷 배치 완료 — %s", batch_id)


def fetch_detailed_greeks(elw_codes: list[str]) -> list[dict]:
    """CpSysDib.Elw 를 사용하여 개별 종목의 상세 Greeks(델타, 감마, 세타)를 수집한다.
    
    주의: 이 API는 종목당 1회씩 호출해야 하므로 속도가 느립니다.
    """
    import win32com.client

    conn = CybosConnection()
    elw_obj = win32com.client.Dispatch("CpSysDib.Elw")

    now = datetime.now().isoformat()
    today = datetime.now().strftime("%Y%m%d")
    out = []

    from tqdm import tqdm
    for code in tqdm(elw_codes, desc="ELW Greeks 수집", leave=False, dynamic_ncols=True):
        try:
            conn.wait_if_needed()
            elw_obj.SetInputValue(0, code)
            elw_obj.BlockRequest()

            if elw_obj.GetDibStatus() != 0:
                logger.warning("[%s] Elw Greeks 조회 실패: %s", code, elw_obj.GetDibMsg1())
                continue

            def safe_float(val):
                try:
                    if val is None: return 0.0
                    if hasattr(val, '__float__'): return float(val)
                    return float(val)
                except (ValueError, TypeError):
                    return 0.0

            out.append({
                "elw_code": code,
                "snapshot_ts": now,
                "date": today,
                "close": safe_float(elw_obj.GetHeaderValue(14)),       # 현재가 (Index 14)
                "iv": safe_float(elw_obj.GetHeaderValue(52)),          # 내재변동성
                "theoretical_price": safe_float(elw_obj.GetHeaderValue(53)),
                "delta": safe_float(elw_obj.GetHeaderValue(59)),
                "gamma": safe_float(elw_obj.GetHeaderValue(60)),
                "theta": safe_float(elw_obj.GetHeaderValue(61)),
                "vega": safe_float(elw_obj.GetHeaderValue(62)),
                "updated_at": now
            })
        except Exception as e:
            logger.error("[%s] Greeks 수집 중 오류: %s", code, e)

    return out


def batch_detailed_greeks(db_writer, elw_codes: list[str], batch_id: str):
    """ELW 상세 Greeks 배치 수집 및 DB 저장."""
    db_writer.init_batch(batch_id, elw_codes)
    pending = db_writer.get_pending_codes(batch_id)
    logger.info("ELW Greeks 배치 시작 — 대상 %d건", len(pending))

    # Greeks는 하나씩 호출하므로 적절한 단위로 chunking하여 저장
    chunk_size = 50
    for i in range(0, len(pending), chunk_size):
        chunk = pending[i:i + chunk_size]
        try:
            rows = fetch_detailed_greeks(chunk)
            if rows:
                db_writer.upsert_many("elw_snapshot_factors", rows)
            for code in chunk:
                db_writer.mark_done(batch_id, code)
        except Exception as e:
            logger.error("ELW Greeks chunk 오류: %s", e)
            for code in chunk:
                db_writer.mark_error(batch_id, code, str(e))

    logger.info("ELW Greeks 배치 완료 — %s", batch_id)
