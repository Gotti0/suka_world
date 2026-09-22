"""
cybos_conn.py — Cybos Plus COM 연결 관리 및 Rate Limit 백오프

.venv32 (32비트 Python) 환경에서만 실행 가능.
CpUtil.CpCybos 객체를 통해 연결 상태 검증 및 API 호출 빈도 제어를 담당한다.
"""

import sys
import time
import logging

logger = logging.getLogger(__name__)


class CybosConnection:
    """Cybos Plus COM 연결 싱글톤 관리자."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        try:
            import win32com.client
            self._cybos = win32com.client.Dispatch("CpUtil.CpCybos")
        except Exception as e:
            logger.critical("win32com 로드 실패: %s", e)
            logger.critical("32비트 Python + Cybos Plus HTS 관리자 권한 실행 필요")
            sys.exit(1)
        self._initialized = True

    def verify(self) -> bool:
        """CpCybos.IsConnect 로 연결 상태를 검증한다."""
        status = self._cybos.IsConnect
        if status == 0:
            logger.critical("Cybos Plus 서버 연결 끊김 — HTS 관리자 권한 로그인 확인 필요")
            sys.exit(1)
        logger.info("Cybos Plus 연결 정상")
        return True

    def wait_if_needed(self):
        """Rate Limit 잔여 횟수가 2 이하이면 대기한다."""
        remain_count = self._cybos.GetLimitRemainCount(1)  # 1: 시세 조회
        if remain_count <= 2:
            remain_ms = self._cybos.LimitRequestRemainTime
            sleep_sec = (remain_ms / 1000) + 0.3  # 안전 마진
            logger.info(
                "Rate Limit 임박 (잔여 %d건) — %.2f초 대기", remain_count, sleep_sec
            )
            time.sleep(sleep_sec)

    def get_strategy_list(self, is_mine: bool = True) -> list:
        """
        HTS에 등록된 종목검색 전략 목록을 조회하여 반환한다.
        
        Args:
            is_mine (bool): True이면 '나의 전략', False이면 '예제 전략' 반환. 기본값 True
            
        Returns:
            list[dict]: 각 전략에 대한 정보(이름, ID, 등록일시, 수익률 등)를 담은 딕셔너리 리스트
        """
        self.wait_if_needed()
        
        try:
            import win32com.client
            obj_rq = win32com.client.Dispatch("CpSysDib.CssStgList")
        except Exception as e:
            logger.error("CpSysDib.CssStgList Dispatch 실패: %s", e)
            return []
            
        req_type = ord('1') if is_mine else ord('0')
        obj_rq.SetInputValue(0, req_type)
        obj_rq.BlockRequest()
        
        status = obj_rq.GetDibStatus()
        if status != 0:
            msg = obj_rq.GetDibMsg1()
            logger.error("CssStgList 통신 실패 (상태: %s, 메시지: %s)", status, msg)
            return []
            
        cnt = obj_rq.GetHeaderValue(0)
        strategies = []
        for i in range(cnt):
            strategies.append({
                'name': obj_rq.GetDataValue(0, i),
                'id': obj_rq.GetDataValue(1, i),
                'reg_time': obj_rq.GetDataValue(2, i),
                'creator': obj_rq.GetDataValue(3, i),
                'avg_stocks': obj_rq.GetDataValue(4, i),
                'avg_win_rate': obj_rq.GetDataValue(5, i),
                'avg_return': obj_rq.GetDataValue(6, i)
            })
            
        logger.info("전략 목록 조회 완료 (is_mine=%s, 개수: %d)", is_mine, cnt)
        return strategies


    def get_strategy_stocks(self, strategy_id: str) -> list:
        """
        특정 전략 ID를 기반으로 해당 시점에 조건에 맞는 종목들을 단발성으로 검색하여 반환한다.
        
        Args:
            strategy_id (str): 전략 ID (get_strategy_list에서 획득)
            
        Returns:
            list[dict]: 검색된 종목들의 리스트. [{"code": "A005930", "name": "삼성전자"}, ...]
        """
        self.wait_if_needed()
        
        try:
            import win32com.client
            obj_rq = win32com.client.Dispatch("CpSysDib.CssStgFind")
            code_mgr = win32com.client.Dispatch("CpUtil.CpCodeMgr")
        except Exception as e:
            logger.error("COM 객체 Dispatch 실패 (CssStgFind 또는 CpCodeMgr): %s", e)
            return []
            
        obj_rq.SetInputValue(0, strategy_id)
        obj_rq.BlockRequest()
        
        status = obj_rq.GetDibStatus()
        if status != 0:
            msg = obj_rq.GetDibMsg1()
            logger.error("CssStgFind 통신 실패 (상태: %s, 메시지: %s)", status, msg)
            return []
            
        cnt = obj_rq.GetHeaderValue(0)
        tot_cnt = obj_rq.GetHeaderValue(1)
        search_time = obj_rq.GetHeaderValue(2)
        
        logger.info("전략 [%s] 단발성 검색 성공 (시간: %s, 검색/총종목: %d/%d)", 
                    strategy_id, search_time, cnt, tot_cnt)
                    
        stocks = []
        for i in range(cnt):
            code = obj_rq.GetDataValue(0, i)
            name = code_mgr.CodeToName(code)
            stocks.append({'code': code, 'name': name})
            
        return stocks


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    conn = CybosConnection()
    conn.verify()
    
    print("\n" + "="*40)
    print("=== 나의 전략 (is_mine=True) ===")
    my_strategies = conn.get_strategy_list(is_mine=True)
    for stg in my_strategies:
        print(f"[ID: {stg['id']}] {stg['name']} (수익 {stg['avg_return']})")
        
    if my_strategies:
        test_id = my_strategies[0]['id']
        test_name = my_strategies[0]['name']
        print(f"\n[테스트] 나의 1번 전략 '{test_name}'의 종목을 단발성 검색합니다...")
        stocks = conn.get_strategy_stocks(test_id)
        for s in stocks[:10]:  # 상위 10개만 출력
            print(f"  - {s['code']}: {s['name']}")
        if len(stocks) > 10:
            print(f"  ... 외 {len(stocks) - 10}개 종목")
        
    print("\n" + "="*40)
    print("=== 예제 전략 (is_mine=False) ===")
    example_strategies = conn.get_strategy_list(is_mine=False)
    for i, stg in enumerate(example_strategies):
        print(f"[ID: {stg['id']}] {stg['name']} (수익 {stg['avg_return']})")
        if i >= 4:  # 예제 전략은 상위 5개만 출력
            print("... (이하 생략)")
            break

