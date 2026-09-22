import sys
import os
import json
import logging
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import List, Optional
from contextlib import asynccontextmanager
import uvicorn
import win32com.client
import pythoncom

# 모듈 호출을 위해 Root 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector.cybos_conn import CybosConnection

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

_cybos_conn = None

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"WS send error: {e}")

ws_manager = ConnectionManager()

# --- 실시간 모듈 테스트용 객체 (StockBsccnsCnld) ---
class BsccnsCnldEventHandler:
    def set_dispatcher(self, dispatcher, callback):
        self.dispatcher = dispatcher
        self.callback = callback

    def OnReceived(self):
        try:
            code = self.dispatcher.GetHeaderValue(0)
            name = self.dispatcher.GetHeaderValue(1)
            time_str = self.dispatcher.GetHeaderValue(3)
            price = self.dispatcher.GetHeaderValue(13)
            exchange = self.dispatcher.GetHeaderValue(29)

            data = {
                "event": "tick",
                "code": code,
                "name": name,
                "time": time_str,
                "price": price,
                "exchange": exchange
            }
            if self.callback:
                loop = asyncio.get_event_loop()
                loop.create_task(self.callback(data))
        except Exception as e:
            logger.error(f"Event 파싱 예외 발생: {e}")

class ATSRealtimeMonitor:
    def __init__(self, target_code: str, broadcast_callback):
        self.target_code = target_code
        # win32com 이벤트 바인딩 시 DispatchWithEvents를 써야 Python 콜백 메서드가 원본 객체에 매핑됩니다.
        self.obj = win32com.client.DispatchWithEvents("Dscbo1.StockBsccnsCnld", BsccnsCnldEventHandler)
        self.obj.set_dispatcher(self.obj, broadcast_callback)

    def subscribe(self):
        self.obj.SetInputValue(0, self.target_code)
        self.obj.Subscribe()
        logger.info(f"{self.target_code} 실시간 통합 체결 틱 구독 시작")

    def unsubscribe(self):
        self.obj.Unsubscribe()
        logger.info(f"{self.target_code} 실시간 구독 해지")

_active_monitors = {}
_message_pump_task = None

async def com_message_pump():
    logger.info("COM Message Pump가 작동을 시작합니다...")
    while True:
        try:
            pythoncom.PumpWaitingMessages()
        except Exception as e:
            logger.error(f"COM pump error: {e}")
        await asyncio.sleep(0.01)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cybos_conn, _message_pump_task
    pythoncom.CoInitialize()
    try:
        logger.info("CYBOS 연결 초기화 중 (32비트 모드)...")
        _cybos_conn = CybosConnection()
        _cybos_conn.verify()
        logger.info("CYBOS API 및 COM 오브젝트 로딩 성공.")
    except Exception as e:
        logger.critical("CYBOS 초기화 실패. HTS 구동 여부를 확인하세요: %s", e)

    _message_pump_task = asyncio.create_task(com_message_pump())
    
    yield  # 앱 구동 상태 진입
    
    # 종료 로직
    for mon in _active_monitors.values():
        mon.unsubscribe()
    pythoncom.CoUninitialize()
    if _message_pump_task:
        _message_pump_task.cancel()

app = FastAPI(lifespan=lifespan)

# REST Endpoints
@app.get("/api/status")
async def get_status():
    if _cybos_conn:
        is_connected = _cybos_conn.verify()
        return {"status": "ok", "connected": is_connected}
    return {"status": "error", "message": "Connection not initialized"}

@app.get("/api/limit")
async def get_limit():
    """Cybos Plus 잔여 요청 제한 정보를 반환합니다."""
    if not _cybos_conn:
        return {"error": "Cybos connection uninitialized"}
    
    # 1: 시세 조회용 리미트 체크
    try:
        remain_count = _cybos_conn._cybos.GetLimitRemainCount(1)
        remain_ms = _cybos_conn._cybos.LimitRequestRemainTime
        return {
            "remain_count": remain_count,
            "remain_ms": remain_ms,
            "type": "quote"
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/strategy/stocks/{strategy_id}")
async def get_strategy_stocks(strategy_id: str):
    if not _cybos_conn:
        return {"error": "Cybos connection uninitialized"}
    try:
        stocks = _cybos_conn.get_strategy_stocks(strategy_id)
        return {"strategy_id": strategy_id, "data": stocks}
    except Exception as e:
        logger.error("종목 조회 에러: %s", e)
        return {"error": str(e)}

@app.get("/api/elw_master")
async def get_elw_master():
    try:
        elw_obj = win32com.client.Dispatch("CpUtil.CpElwCode")
        count = elw_obj.GetCount()
        data = []
        for i in range(count):
            code = elw_obj.GetData(0, i)
            # You can extract more details like strike price, ATM type if needed. We'll start with code.
            data.append(code)
        return {"data": data}
    except Exception as e:
        logger.error(f"ELW Master 조회 에러: {e}")
        return {"error": str(e)}

class MarketEyeRequest(BaseModel):
    codes: List[str]
    fields: List[int]
    exchange: Optional[str] = None

class PrecisionQuoteRequest(BaseModel):
    codes: List[str]

@app.post("/api/marketeye")
async def get_marketeye(req: MarketEyeRequest):
    if not _cybos_conn:
        return {"error": "Cybos connection uninitialized"}
    try:
        obj = win32com.client.Dispatch("CpSysDib.MarketEye")
        sorted_fields = sorted(req.fields)
        
        obj.SetInputValue(0, sorted_fields)
        obj.SetInputValue(1, req.codes)
            
        _cybos_conn.wait_if_needed()
        obj.BlockRequest()
        
        status = obj.GetDibStatus()
        if status != 0:
            return {"error": f"MarketEye status {status}: {obj.GetDibMsg1()}"}
            
        cnt = obj.GetHeaderValue(2)
        
        result = []
        for i in range(cnt):
            item = {}
            for j, field in enumerate(sorted_fields):
                # Using index j to get data for specific field in MarketEye
                item[field] = obj.GetDataValue(j, i)
            result.append(item)
            
        return {"data": result}
    except Exception as e:
        logger.error(f"MarketEye 오류: {e}")
        return {"error": str(e)}

@app.post("/api/quotes_precision")
async def get_quotes_precision(req: PrecisionQuoteRequest):
    if not _cybos_conn:
        return {"error": "Cybos connection uninitialized"}
    try:
        obj = win32com.client.Dispatch("CpSysDib.MarketEye")
        
        # 필드: 0(코드), 8(매도호가), 9(매수호가), 15(최우선매도잔량), 16(최우선매수잔량), 136(IV), 137(Delta)
        fields = [0, 8, 9, 15, 16, 136, 137]
        obj.SetInputValue(0, fields)
        obj.SetInputValue(1, req.codes)
        
        _cybos_conn.wait_if_needed()
        obj.BlockRequest()
        
        status = obj.GetDibStatus()
        if status != 0:
            logger.error(f"MarketEye Batch 실패: {obj.GetDibMsg1()}")
            return {"data": {}}
            
        cnt = obj.GetHeaderValue(2)
        results = {}
        for i in range(cnt):
            code = obj.GetDataValue(0, i)
            results[code] = {
                "ask": obj.GetDataValue(1, i),
                "bid": obj.GetDataValue(2, i),
                "lp_ask_vol": obj.GetDataValue(3, i),
                "lp_bid_vol": obj.GetDataValue(4, i),
                "iv": obj.GetDataValue(5, i),
                "delta": obj.GetDataValue(6, i)
            }
            
        return {"data": results}
    except Exception as e:
        logger.error(f"MarketEye Batch(quotes_precision) 오류: {e}")
        return {"error": str(e)}

def get_elw_underlying(code: str):
    """CpSysDib.Elw를 사용하여 ELW의 기초자산 코드와 명칭을 가져옵니다."""
    try:
        obj = win32com.client.Dispatch("CpSysDib.Elw")
        obj.SetInputValue(0, code)
        if _cybos_conn: _cybos_conn.wait_if_needed()
        obj.BlockRequest()
        
        status = obj.GetDibStatus()
        if status != 0:
            logger.error(f"Elw Info 조회 실패: {obj.GetDibMsg1()}")
            return None, None
            
        underlying_code = obj.GetHeaderValue(29)
        underlying_name = obj.GetHeaderValue(30)
        return underlying_code, underlying_name
    except Exception as e:
        logger.error(f"get_elw_underlying error: {e}")
        return None, None

@app.get("/api/elw_detail/{code}")
async def get_elw_detail(code: str):
    """CpSysDib.Elw 및 CpSysDib.ElwAll을 사용하여 상세 Greeks 및 정보를 가져옵니다."""
    if not _cybos_conn:
        return {"error": "Cybos connection uninitialized"}
    try:
        # 1. 기본 정보 및 그리스 (CpSysDib.Elw)
        obj = win32com.client.Dispatch("CpSysDib.Elw")
        obj.SetInputValue(0, code)
        if _cybos_conn: _cybos_conn.wait_if_needed()
        obj.BlockRequest()
        
        if obj.GetDibStatus() != 0:
            return {"error": f"Elw Info failed: {obj.GetDibMsg1()}"}
            
        # 주요 헤더값 추출 (사용자 제공 명세서 준수)
        res = {
            "code": code,
            "name": obj.GetHeaderValue(1),
            "price": obj.GetHeaderValue(14),
            "diff": obj.GetHeaderValue(15),
            "vol": obj.GetHeaderValue(19),
            "theory_price": obj.GetHeaderValue(53),
            "iv": obj.GetHeaderValue(52),
            "delta": obj.GetHeaderValue(59),
            "gamma": obj.GetHeaderValue(60),
            "theta": obj.GetHeaderValue(61),
            "vega": obj.GetHeaderValue(62),
            "rho": obj.GetHeaderValue(63),
            "parity": obj.GetHeaderValue(40),
            "premium": obj.GetHeaderValue(41),
            "gearing": obj.GetHeaderValue(42),
            "e_gearing": obj.GetHeaderValue(65),
            "underlying_code": obj.GetHeaderValue(29),
            "underlying_name": obj.GetHeaderValue(30),
            "underlying_price": obj.GetHeaderValue(67),
            "strike_price": obj.GetHeaderValue(7),
            "conversion_ratio": obj.GetHeaderValue(6),
            "maturity_date": obj.GetHeaderValue(10),
            "last_trading_day": obj.GetHeaderValue(9),
            "days_to_maturity": obj.GetHeaderValue(11),
            "issuer_name": obj.GetHeaderValue(21),
            "lp_inventory": obj.GetHeaderValue(34),
            "parity": obj.GetHeaderValue(40),
        }
        return {"data": res}
    except Exception as e:
        logger.error(f"ELW Detail 조회 에러: {e}")
        return {"error": str(e)}

@app.get("/api/elw_invest/{code}")
async def get_elw_invest(code: str):
    """CpSysDib.ElwInvest를 사용하여 IV 추이 등의 투자 지표를 가져옵니다."""
    if not _cybos_conn:
        return {"error": "Cybos connection uninitialized"}
    try:
        obj = win32com.client.Dispatch("CpSysDib.ElwInvest")
        
        # 1. 원본 코드로 시도
        obj.SetInputValue(0, code)
        if _cybos_conn: _cybos_conn.wait_if_needed()
        obj.BlockRequest()
        
        status = obj.GetDibStatus()
        count = obj.GetHeaderValue(1)
        
        # 만약 실패했거나 데이터가 0건이면 재시도 로직
        if status != 0 or count == 0:
            # ELW(J 시작)가 아닌 일반 주식형 코드일 때만 'A' 접두사 시도
            if not code.startswith('A') and not code.startswith('J'):
                alt_code = f"A{code}"
                logger.info(f"ElwInvest 재시도 (A 접두사 추가): {code} -> {alt_code}")
                obj.SetInputValue(0, alt_code)
                if _cybos_conn: _cybos_conn.wait_if_needed()
                obj.BlockRequest()
                status = obj.GetDibStatus()
                count = obj.GetHeaderValue(1)
        
        if status != 0:
            msg = obj.GetDibMsg1()
            logger.error(f"ElwInvest DIB 에러 ({code}): {msg}")
            return {"error": f"DIB status {status}: {msg}"}
            
        if count == 0:
            logger.warning(f"ElwInvest 데이터 없음 ({code})")
            return {"error": "No data returned from Cybos", "data": []}
            
        result = []
        for i in range(count):
            # 0:일자, 1:현재가, 8:거래량, 10:IV, 11:델타, 12:감마, 13:세타, 14:베가, 15:로
            result.append({
                "date": obj.GetDataValue(0, i),
                "close": obj.GetDataValue(1, i),
                "vol": obj.GetDataValue(8, i),
                "iv": obj.GetDataValue(10, i),
                "delta": obj.GetDataValue(11, i),
                "gamma": obj.GetDataValue(12, i),
                "theta": obj.GetDataValue(13, i),
                "vega": obj.GetDataValue(14, i)
            })
        return {"data": result}
    except Exception as e:
        logger.error(f"ELW Invest 조회 예외 ({code}): {e}")
        return {"error": str(e)}

def get_stock_chart(code: str, count: int = 100):
    """CpSysDib.StockChart를 사용하여 일봉 데이터를 가져옵니다."""
    try:
        obj = win32com.client.Dispatch("CpSysDib.StockChart")
        obj.SetInputValue(0, code)
        obj.SetInputValue(1, ord('2'))  # 갯수 기준
        obj.SetInputValue(4, count)      # 최근 count개
        # 0:날짜, 2:시가, 3:고가, 4:저가, 5:종가, 8:거래량
        obj.SetInputValue(5, [0, 2, 3, 4, 5, 8])
        obj.SetInputValue(6, ord('D'))  # 일봉
        obj.SetInputValue(9, ord('1'))  # 수정주가
        
        if _cybos_conn: _cybos_conn.wait_if_needed()
        obj.BlockRequest()
        
        status = obj.GetDibStatus()
        if status != 0:
            logger.error(f"StockChart 조회 실패 ({code}): {obj.GetDibMsg1()}")
            return []
            
        cnt = obj.GetHeaderValue(3)
        data = []
        for i in range(cnt):
            # 날짜 형식을 YYYY-MM-DD로 변환 (YYYYMMDD -> YYYY-MM-DD)
            date_val = str(obj.GetDataValue(0, i))
            formatted_date = f"{date_val[:4]}-{date_val[4:6]}-{date_val[6:8]}"
            
            data.append({
                "time": formatted_date,
                "open": float(obj.GetDataValue(1, i)),
                "high": float(obj.GetDataValue(2, i)),
                "low": float(obj.GetDataValue(3, i)),
                "close": float(obj.GetDataValue(4, i)),
                "volume": int(obj.GetDataValue(5, i))
            })
        
        # 최신 데이터가 인덱스 0이므로 역순으로 정렬 (차트 라이브러리 요구사항)
        return data[::-1]
    except Exception as e:
        logger.error(f"get_stock_chart error ({code}): {e}")
        return []

@app.get("/api/chart/daily/{code}")
async def get_daily_chart(code: str):
    if not _cybos_conn:
        return {"error": "Cybos connection uninitialized"}
    
    try:
        # 1. ELW 차트 데이터
        elw_data = get_stock_chart(code)
        
        # 2. 기초자산 정보 및 차트 데이터
        under_code, under_name = get_elw_underlying(code)
        under_data = []
        if under_code:
            under_data = get_stock_chart(under_code)
            
        return {
            "elw_code": code,
            "elw_data": elw_data,
            "underlying_code": under_code,
            "underlying_name": under_name,
            "underlying_data": under_data
        }
    except Exception as e:
        logger.error(f"Daily Chart API Error: {e}")
        return {"error": str(e)}

@app.get("/api/chart/stock/daily/{code}")
async def get_stock_daily_chart(code: str, count: int = 3000):
    """CpSysDib.StockChart를 사용하여 일봉 데이터를 가져옵니다 (지수 포함)."""
    if not _cybos_conn:
        return {"error": "Cybos connection uninitialized"}
    try:
        data = get_stock_chart(code, count)
        return {"code": code, "data": data}
    except Exception as e:
        logger.error(f"Stock Daily Chart API Error: {e}")
        return {"error": str(e)}

# WebSocket Endpoint
@app.websocket("/ws/realtime")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    logger.info("클라이언트 WebSocket 접속 성공")
    try:
        while True:
            text_data = await websocket.receive_text()
            try:
                cmd = json.loads(text_data)
                action = cmd.get("action")
                code = cmd.get("code")

                if action == "subscribe" and code:
                    if code not in _active_monitors:
                        monitor = ATSRealtimeMonitor(code, ws_manager.broadcast)
                        monitor.subscribe()
                        _active_monitors[code] = monitor
                        await websocket.send_json({"status": "success", "message": f"Subscribed to {code}"})
                elif action == "unsubscribe" and code:
                    if code in _active_monitors:
                        _active_monitors[code].unsubscribe()
                        del _active_monitors[code]
                        await websocket.send_json({"status": "success", "message": f"Unsubscribed from {code}"})
            except json.JSONDecodeError:
                await websocket.send_json({"error": "Invalid JSON format"})
                
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
        logger.info("클라이언트 WebSocket 접속 해제")

if __name__ == '__main__':
    logger.info("\n" + "="*55)
    logger.info("🚀 [32-bit Bridge] FastAPI + WebSocket Server 구동 🚀")
    logger.info("="*55)
    uvicorn.run("cybos_server:app", host="0.0.0.0", port=5050, reload=False)
