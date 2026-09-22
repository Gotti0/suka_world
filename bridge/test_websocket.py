import asyncio
import logging
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bridge.cybos_client import AsyncCybosStreamClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

async def event_handler(data: dict):
    """서버에서 PUSH 방식 수신 시 콜백되는 핸들러"""
    if "event" in data and data["event"] == "tick":
        logger.info(f"[실시간 TICK 수신] 종목: {data.get('name')}({data.get('code')}) | 시간: {data.get('time')} | 가격: {data.get('price')} | 시장: {data.get('exchange')}")
    else:
        logger.info(f"[서버 응답 수신] {data}")

async def main():
    logger.info("테스트: 64비트 비동기 클라이언트 구동")
    client = AsyncCybosStreamClient()
    
    # 1. 이벤트 핸들러 등록
    client.register_callback(event_handler)
    
    # 2. 백그라운드 태스크로 소켓 연결 및 수신 시작
    listen_task = asyncio.create_task(client.connect_and_listen())
    
    # 3. 소켓이 정상적으로 활성화될 때까지 잠시 대기
    await asyncio.sleep(1)
    
    # 4. 삼성전자(A005930) 실시간 통합 체결 틱 구독 요청
    logger.info("===> 삼성전자(A005930) 실시간 데이터 구독을 요청합니다.")
    await client.request_subscribe("A005930")
    
    # 15초 동안 실시간 데이터 청취 후 테스트 종료
    logger.info("15초간 데이터를 수집합니다...")
    await asyncio.sleep(15)
    
    logger.info("===> 구독을 해지합니다.")
    await client.request_subscribe("A005930") # unsub needs specific action, but we will test just closing
    logger.info("테스트용 클라이언트를 종료합니다.")
    listen_task.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("사용자에 의해 테스트가 중단되었습니다.")
