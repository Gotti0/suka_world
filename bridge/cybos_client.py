import urllib.request
import json
import logging
import asyncio
import websockets

logger = logging.getLogger(__name__)

class CybosBridgeClient:
    """
    64비트 환경(.venv64)에서 분석 엔진이나 백테스터가 사용할 REST 브릿지 인터페이스.
    동기 통신이 필요한 초기화나 1회성 스냅샷 조회에 사용.
    """
    def __init__(self, host="127.0.0.1", port=5050):
        self.base_url = f"http://{host}:{port}"
        
    def verify(self) -> bool:
        """브릿지 서버 상태 및 CYBOS HTS 실제 연결 상태를 통합 검증한다."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/status")
            with urllib.request.urlopen(req, timeout=3) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    return data.get("connected", False)
                return False
        except Exception as e:
            logger.error("브릿지 서버 연결 오류: %s", e)
            return False

    def get_strategy_stocks(self, strategy_id: str) -> list:
        try:
            url = f"{self.base_url}/api/strategy/stocks/{strategy_id}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    return data.get("data", [])
                else:
                    return []
        except Exception as e:
            logger.error("브릿지 클라이언트 HTTP GET 에러: %s", e)
            return []

    def get_elw_master(self) -> list:
        try:
            url = f"{self.base_url}/api/elw_master"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    return data.get("data", [])
                return []
        except Exception as e:
            logger.error("ELW Master 수신 에러: %s", e)
            return []

    def get_marketeye(self, codes: list, fields: list, exchange: str = None) -> list:
        try:
            url = f"{self.base_url}/api/marketeye"
            payload_dict = {"codes": codes, "fields": fields}
            if exchange:
                payload_dict["exchange"] = exchange
            payload = json.dumps(payload_dict).encode('utf-8')
            req = urllib.request.Request(url, data=payload, method='POST')
            req.add_header('Content-Type', 'application/json')
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    return data.get("data", [])
                return []
        except Exception as e:
            logger.error("MarketEye 수신 에러: %s", e)
            return []

    def get_elw_detail(self, code: str) -> dict:
        """특정 ELW 종목의 상세 Greeks 및 기본 정보를 조회합니다."""
        try:
            url = f"{self.base_url}/api/elw_detail/{code}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    return data.get("data", {})
                return {}
        except Exception as e:
            logger.error(f"ELW Detail 조회 에러 ({code}): {e}")
            return {}

    def get_stock_chart(self, code: str, count: int = 100) -> list:
        """기초자산의 일봉 차트 데이터를 조회합니다."""
        try:
            url = f"{self.base_url}/api/chart/stock/daily/{code}?count={count}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    return data.get("data", [])
                return []
        except Exception as e:
            logger.error(f"get_stock_chart 에러 ({code}): {e}")
            return []

class AsyncCybosStreamClient:
    """
    WebSocket을 이용하여 브릿지 서버와 실시간으로 연결하고 틱 이벤트를 수신하는 64비트 클라이언트.
    """
    def __init__(self, host="127.0.0.1", port=5050):
        self.ws_url = f"ws://{host}:{port}/ws/realtime"
        self.ws = None
        self.callbacks = []

    def register_callback(self, callback_func):
        """실시간 데이터를 수신하면 실행할 비동기 콜백 함수 등록"""
        self.callbacks.append(callback_func)

    async def connect_and_listen(self):
        """웹소켓 서버에 접속하고 무한 루프로 메시지를 수신한다."""
        logger.info(f"브릿지 WS 서버({self.ws_url})에 연결 중...")
        try:
            async with websockets.connect(self.ws_url) as ws:
                self.ws = ws
                logger.info("WebSocket 브릿지 연결 성공 및 이벤트 리스닝 시작.")
                
                while True:
                    message_str = await ws.recv()
                    data = json.loads(message_str)
                    
                    # 등록된 콜백 함수들에게 수신 데이터를 뿌려준다.
                    for cb in self.callbacks:
                        await cb(data)
                        
        except websockets.exceptions.ConnectionClosed:
            logger.warning("웹소켓 서버와의 연결이 종료되었습니다.")
        except Exception as e:
            logger.error(f"웹소켓 수신 중 에러 발생: {e}")
            
    async def request_subscribe(self, code: str):
        """특정 종목의 실시간 틱 체결 데이터 구독(Subscribe) 요청"""
        if self.ws:
            payload = {"action": "subscribe", "code": code}
            await self.ws.send(json.dumps(payload))
            logger.info(f"구독 요청 전송: {code}")
        else:
            logger.warning("웹소켓이 연결되지 않아 구독 요청을 보낼 수 없습니다.")
