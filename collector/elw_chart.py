"""
elw_chart.py — ELW 일봉/분봉 OHLCV 수집기

CpSysDib.StockChart로 ELW 시계열을 수집한다.
"""

import logging

from .cybos_conn import CybosConnection

logger = logging.getLogger(__name__)


def _fetch_chart(elw_code: str, timeframe: str, count: int, interval: int = 0) -> list[dict]:
    import win32com.client

    conn = CybosConnection()
    conn.wait_if_needed()

    chart = win32com.client.Dispatch("CpSysDib.StockChart")

    chart.SetInputValue(0, elw_code)
    chart.SetInputValue(1, ord("2"))
    chart.SetInputValue(4, count)
    chart.SetInputValue(6, ord(timeframe))
    chart.SetInputValue(9, ord("1"))

    if timeframe == "m":
        chart.SetInputValue(5, (0, 1, 2, 3, 4, 5, 8))
        chart.SetInputValue(7, interval)
    else:
        chart.SetInputValue(5, (0, 2, 3, 4, 5, 8))

    rows = []

    while len(rows) < count:
        chart.BlockRequest()

        if chart.GetDibStatus() != 0:
            logger.warning("[%s] ELW 차트 오류: %s", elw_code, chart.GetDibMsg1())
            break

        received = chart.GetHeaderValue(3)
        if received == 0:
            break

        for i in range(received):
            if timeframe == "m":
                rows.append({
                    "elw_code": elw_code,
                    "date": str(chart.GetDataValue(0, i)),
                    "time": str(chart.GetDataValue(1, i)).zfill(4),
                    "open": chart.GetDataValue(2, i),
                    "high": chart.GetDataValue(3, i),
                    "low": chart.GetDataValue(4, i),
                    "close": chart.GetDataValue(5, i),
                    "volume": chart.GetDataValue(6, i),
                })
            else:
                rows.append({
                    "elw_code": elw_code,
                    "date": str(chart.GetDataValue(0, i)),
                    "open": chart.GetDataValue(1, i),
                    "high": chart.GetDataValue(2, i),
                    "low": chart.GetDataValue(3, i),
                    "close": chart.GetDataValue(4, i),
                    "volume": chart.GetDataValue(5, i),
                })

        if len(rows) >= count:
            break
        if not chart.Continue:
            break
        conn.wait_if_needed()

    rows = rows[:count]
    rows.reverse()
    return rows


def fetch_daily(elw_code: str, count: int = 3000) -> list[dict]:
    return _fetch_chart(elw_code, "D", count)




def batch_daily(db_writer, elw_codes: list[str], batch_id: str, count: int = 3000):
    db_writer.init_batch(batch_id, elw_codes)
    pending = db_writer.get_pending_codes(batch_id)
    logger.info("ELW 일봉 배치 시작 — 대상 %d건 (전체 %d건)", len(pending), len(elw_codes))

    from tqdm import tqdm
    for code in tqdm(pending, desc=f"ELW 일봉 {batch_id}", leave=False, dynamic_ncols=True):
        try:
            rows = fetch_daily(code, count)
            if rows:
                db_writer.upsert_many("elw_daily_ohlcv", rows)
            db_writer.mark_done(batch_id, code)
        except Exception as e:
            logger.error("[%s] ELW 일봉 수집 오류: %s", code, e)
            db_writer.mark_error(batch_id, code, str(e))

    logger.info("ELW 일봉 배치 완료 — %s", batch_id)


