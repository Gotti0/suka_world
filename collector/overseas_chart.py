"""
overseas_chart.py — 해외 일봉 OHLCV 수집기

CpSysDib.CpSvrNew8300 을 사용하여 해외 종목·지수·환율·원자재의
과거 일봉 시계열 데이터를 수집한다.

주의사항:
  - StockChart 와 필드 매핑이 다름
  - SetInputValue(5, ...) 생략 시 빈 배열 반환
  - GetDataValue 의 Type 은 오름차순 정렬 후 인덱스 기준
  - SetInputValue(1, ...), (6, ...) 에 반드시 ord() 적용
"""

import logging
from datetime import datetime

from .cybos_conn import CybosConnection

logger = logging.getLogger(__name__)

# 필드 배열 (오름차순 — 자동 정렬과 일치)
# 0:날짜, 1:시가, 2:고가, 3:저가, 4:종가, 5:전일대비, 6:거래량
FIELDS = (0, 1, 2, 3, 4, 5, 6)


def fetch_daily(us_code: str, count: int = 3000) -> list[dict]:
    """단일 해외 종목의 일봉 OHLCV 를 수집한다. (기본 약 12년)"""
    import win32com.client

    conn = CybosConnection()
    conn.wait_if_needed()

    chart = win32com.client.Dispatch("CpSysDib.CpSvrNew8300")

    chart.SetInputValue(0, us_code)         # 종목코드
    chart.SetInputValue(1, ord('2'))        # 개수 기준 요청
    chart.SetInputValue(4, count)           # 요청 건수
    chart.SetInputValue(5, FIELDS)          # [필수] 필드 배열
    chart.SetInputValue(6, ord('D'))        # 일봉 (ord 필수!)
    rows = []

    while len(rows) < count:
        chart.BlockRequest()

        if chart.GetDibStatus() != 0:
            logger.warning("[%s] 해외 차트 오류: %s", us_code, chart.GetDibMsg1())
            break

        received = chart.GetHeaderValue(3)
        if received == 0:
            break

        for i in range(received):
            # 필드 배열이 이미 오름차순이므로 인덱스 = 필드 번호
            rows.append({
                "us_code": us_code,
                "date": str(chart.GetDataValue(0, i)),
                "open": chart.GetDataValue(1, i),
                "high": chart.GetDataValue(2, i),
                "low": chart.GetDataValue(3, i),
                "close": chart.GetDataValue(4, i),
                "change": chart.GetDataValue(5, i),
                "volume": chart.GetDataValue(6, i),
            })

        if len(rows) >= count:
            break

        if not chart.Continue:
            break

        conn.wait_if_needed()

    rows = rows[:count]
    # 역순(최신→과거) → 정방향
    rows.reverse()
    return rows


def batch_daily(db_writer, us_codes: list[str], batch_id: str, count: int = 3000):
    """해외 전 종목 일봉 배치 수집."""
    db_writer.init_batch(batch_id, us_codes)
    pending = db_writer.get_pending_codes(batch_id)
    logger.info("해외 일봉 배치 시작 — 대상 %d건 (전체 %d건)", len(pending), len(us_codes))

    from tqdm import tqdm
    for code in tqdm(pending, desc=f"해외일봉 {batch_id}", leave=False, dynamic_ncols=True):
        try:
            rows = fetch_daily(code, count)
            if rows:
                db_writer.upsert_many("overseas_daily_ohlcv", rows)
            db_writer.mark_done(batch_id, code)
        except Exception as e:
            logger.error("[%s] 해외 일봉 수집 오류: %s", code, e)
            db_writer.mark_error(batch_id, code, str(e))

    logger.info("해외 일봉 배치 완료 — %s", batch_id)
