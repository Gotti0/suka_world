import pandas as pd
import logging
from datetime import datetime, timedelta

# 로깅 설정
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TimeController:
    """
    유튜브 방송 시점과 실제 거래 가능 시점 사이의 시차를 조절하여 
    Lookahead Bias(미래 참조 편향)를 방지합니다.
    """
    
    @staticmethod
    def get_trade_execution_date(upload_date_str: str) -> str:
        """
        방송 업로드 날짜를 기준으로 실제 매매가 집행되어야 할 날짜(월요일 시가)를 반환합니다.
        보통 일요일 밤 방송 -> 월요일 오전 9시 시가 매매 가정.
        """
        try:
            # YYYYMMDD 또는 YYYY-MM-DD 형식 처리
            date_str = upload_date_str.replace("-", "")
            upload_date = datetime.strptime(date_str, "%Y%m%dd") if len(date_str) == 8 else datetime.strptime(date_str, "%Y%m%d")
            
            # 요일 확인 (0: 월, 1: 화, ..., 5: 토, 6: 일)
            weekday = upload_date.weekday()
            
            # 금, 토, 일 방송인 경우 -> 다음주 월요일
            if weekday >= 4:
                days_to_monday = 7 - weekday
                execution_date = upload_date + timedelta(days=days_to_monday)
            else:
                # 평일 방송인 경우 -> 다음 영업일 (단순화를 위해 +1일)
                # 실제로는 휴장일 데이터를 체크해야 하지만 우선 다음날로 설정
                execution_date = upload_date + timedelta(days=1)
                
            return execution_date.strftime("%Y-%m-%d")
            
        except Exception as e:
            logger.error(f"날짜 변환 중 오류 발생 ({upload_date_str}): {e}")
            return upload_date_str

if __name__ == "__main__":
    tc = TimeController()
    test_dates = ["2026-05-10", "2026-05-14"] # 10일은 일요일, 14일은 목요일
    for d in test_dates:
        print(f"방송일: {d} -> 매매일: {tc.get_trade_execution_date(d)}")
