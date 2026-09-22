"""
stock_chart.py — 국내 일봉/5분봉 OHLCV 수집기

CpSysDib.StockChart 를 사용하여 수정주가 기반 OHLCV 시계열을 수집한다.
- 일봉: daily_ohlcv 테이블 증분 적재
- 5분봉: minute_ohlcv 테이블 증분 적재
"""

import logging
from datetime import datetime

from .cybos_conn import CybosConnection

logger = logging.getLogger(__name__)


def _fetch_chart(stock_code: str, timeframe: str, count: int,
                 interval: int = 0) -> list[dict]:
    """StockChart COM 호출 공통 함수.

    Args:
        stock_code: 종목코드 (예: 'A005930')
        timeframe: 'D'(일봉) 또는 'm'(분봉)
        count: 요청 캔들 수
        interval: 분봉 간격 (분봉 전용, 예: 5)

    Returns:
        OHLCV dict 리스트 (정방향 정렬)
    """
    import win32com.client

    conn = CybosConnection()
    conn.wait_if_needed()

    chart = win32com.client.Dispatch("CpSysDib.StockChart")

    chart.SetInputValue(0, stock_code)
    chart.SetInputValue(1, ord('2'))  # 개수 기준
    chart.SetInputValue(4, count)
    chart.SetInputValue(6, ord(timeframe))
    chart.SetInputValue(9, ord('1'))  # 수정주가

    if timeframe == 'm':
        # 분봉: 날짜 + 시간 + OHLCV
        chart.SetInputValue(5, (0, 1, 2, 3, 4, 5, 8))
        chart.SetInputValue(7, interval)
    else:
        # 일봉: 날짜 + OHLCV (시간 불필요)
        chart.SetInputValue(5, (0, 2, 3, 4, 5, 8))

    rows = []

    while len(rows) < count:
        chart.BlockRequest()

        if chart.GetDibStatus() != 0:
            logger.warning("[%s] 차트 오류: %s", stock_code, chart.GetDibMsg1())
            break

        received = chart.GetHeaderValue(3)
        if received == 0:
            break

        for i in range(received):
            if timeframe == 'm':
                rows.append({
                    "stock_code": stock_code,
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
                    "stock_code": stock_code,
                    "date": str(chart.GetDataValue(0, i)),
                    "open": chart.GetDataValue(1, i),
                    "high": chart.GetDataValue(2, i),
                    "low": chart.GetDataValue(3, i),
                    "close": chart.GetDataValue(4, i),
                    "volume": chart.GetDataValue(5, i),
                })

        # 목표 건수 도달
        if len(rows) >= count:
            break

        # 더 이상 수신할 과거 데이터가 없는 경우
        if not chart.Continue:
            break

        # 다음 차수를 받기 위한 Rate Limit 대기
        conn.wait_if_needed()

    # 정확히 count 만큼만 반환
    rows = rows[:count]
    # API 응답은 역순(최신→과거) → 정방향으로 뒤집기
    rows.reverse()
    return rows


def fetch_daily(stock_code: str, count: int = 3000) -> list[dict]:
    """단일 종목의 일봉 OHLCV 를 수집한다. (기본 약 12년치)"""
    return _fetch_chart(stock_code, 'D', count)


def fetch_minute(stock_code: str, count: int = 100000, interval: int = 5) -> list[dict]:
    """단일 종목의 분봉 OHLCV 를 수집한다. (기본 10만건 = 5분봉 기준 약 5년치)"""
    return _fetch_chart(stock_code, 'm', count, interval)


def batch_daily(db_writer, stock_codes: list[str], batch_id: str, count: int = 3000):
    """전 종목 일봉 배치 수집."""
    db_writer.init_batch(batch_id, stock_codes)
    pending = db_writer.get_pending_codes(batch_id)
    logger.info("일봉 배치 시작 — 대상 %d건 (전체 %d건)", len(pending), len(stock_codes))

    from tqdm import tqdm
    for code in tqdm(pending, desc=f"일봉 {batch_id}", leave=False, dynamic_ncols=True):
        try:
            rows = fetch_daily(code, count)
            if rows:
                db_writer.upsert_many("daily_ohlcv", rows)
                db_writer.record_stock_chart_success(code)
                db_writer.mark_done(batch_id, code)
            else:
                db_writer.record_stock_chart_miss(code)
                db_writer.mark_error(batch_id, code, "empty response")
        except Exception as e:
            db_writer.record_stock_chart_miss(code)
            logger.error("[%s] 일봉 수집 오류: %s", code, e)
            db_writer.mark_error(batch_id, code, str(e))

    logger.info("일봉 배치 완료 — %s", batch_id)


def batch_minute(db_writer, stock_codes: list[str], batch_id: str,
                 count: int = 100000, interval: int = 5):
    """전 종목 5분봉 배치 수집."""
    db_writer.init_batch(batch_id, stock_codes)
    pending = db_writer.get_pending_codes(batch_id)
    logger.info("5분봉 배치 시작 — 대상 %d건 (전체 %d건)", len(pending), len(stock_codes))

    from tqdm import tqdm
    for code in tqdm(pending, desc=f"5분봉 {batch_id}", leave=False, dynamic_ncols=True):
        try:
            rows = fetch_minute(code, count, interval)
            if rows:
                db_writer.upsert_many("minute_ohlcv", rows)
                db_writer.record_stock_chart_success(code)
                db_writer.mark_done(batch_id, code)
            else:
                db_writer.record_stock_chart_miss(code)
                db_writer.mark_error(batch_id, code, "empty response")
        except Exception as e:
            db_writer.record_stock_chart_miss(code)
            logger.error("[%s] 5분봉 수집 오류: %s", code, e)
            db_writer.mark_error(batch_id, code, str(e))

    logger.info("5분봉 배치 완료 — %s", batch_id)
