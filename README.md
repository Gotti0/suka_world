# Suka World (슈카월드) NLP Quant Pipeline

**Suka World** 프로젝트는 유명 경제/금융 유튜브 채널 '슈카월드'의 영상 자막(Transcript) 데이터를 수집하고, 이를 자연어 처리(NLP) 엔진으로 분석하여 주식 시장 및 매크로 경제의 인사이트를 도출한 뒤, 실제 금융 데이터(CYBOS, DART 등)와 결합하여 계량 투자(Quantitative Trading) 전략을 수립하고 백테스트하는 AI 기반 퀀트 트레이딩 파이프라인입니다.

## 🚀 주요 기능 (Key Features)

1. **데이터 수집 (Data Collection)**
   - YouTube Data API 및 `youtube-transcript-api`를 활용한 슈카월드 영상 자막 추출.
   - CYBOS API를 활용한 국내 주식, ELW, 해외 지수 데이터 수집.
   - DART Open API를 통한 기업 재무제표 스크래핑.
   
2. **NLP 엔진 (NLP Engine)**
   - **Google Gemini**: 방대한 분량의 유튜브 자막을 분석하여 핵심 경제 키워드, 센티멘트(Sentiment), 언급된 종목 및 매크로 동향을 추출합니다.
   - **Voyage AI**: 텍스트 데이터를 고품질 벡터 임베딩으로 변환하여 문맥 기반 유사도 검색 및 분석을 수행합니다.

3. **금융 데이터 브리지 (CYBOS Bridge)**
   - 32-bit 환경에서만 동작하는 CYBOS Plus API의 한계를 극복하기 위해 WebSocket 기반의 32-bit/64-bit 브리지 서버(`cybos_server.py`, `cybos_client.py`)를 운영하여 비동기 데이터 통신을 지원합니다.

4. **전략 및 백테스팅 (Strategy & Backtesting)**
   - 추출된 텍스트 인사이트를 실제 시장 자산(Tickers)과 매핑(`mapper.py`).
   - XGBoost, Scikit-learn 등을 활용한 예측 머신러닝 모델링(`modeling.py`).
   - `vectorbt`와 `quantstats`를 활용한 포트폴리오 성과 분석 및 백테스팅 엔진(`backtest_engine.py`).

## 📂 디렉토리 구조 (Directory Structure)

```text
suka_world/
├── bridge/                # CYBOS 32-bit API 연동용 WebSocket 서버/클라이언트
├── collector/             # 유튜브 자막, 증권사(CYBOS), DART 데이터 수집 모듈
├── nlp_engine/            # LLM(Gemini) 및 임베딩(Voyage AI) 처리 엔진
├── strategy/              # 트레이딩 로직, 자산 매핑, 백테스트 엔진
├── data/                  # 수집된 자막 JSON 파일 및 큐(Queue) 관리 폴더
├── docs/                  # 대량의 자막 원본 파일 보관 폴더
├── fill_missing_dates.py  # 자막 데이터 내 누락된 영상 업로드 일자 보완 스크립트
├── split_transcripts.py   # 대용량 자막 파일을 개별 영상(video_id) 단위로 분할하는 스크립트
├── update_queue.py        # 처리 완료된 자막을 대기열(Queue)에서 제거하는 유틸리티
└── requirements.txt       # 프로젝트 패키지 의존성
```

## 🛠️ 설치 및 설정 (Setup)

1. **가상환경 설정**
   ```bash
   cd suka_world
   python -m venv .venv
   .venv\Scripts\activate  # Windows 환경
   ```

2. **패키지 설치**
   ```bash
   pip install -r requirements.txt
   ```

3. **환경 변수 설정 (`.env`)**
   프로젝트 루트에 `.env` 파일을 생성하고 아래 API 키들을 입력합니다.
   ```ini
   GEMINI_API_KEY=your_gemini_api_key
   VOYAGE_API_KEY=your_voyage_api_key
   DART_API_KEY=your_dart_api_key
   # 필요한 경우 DB 연결 정보 등 추가
   ```

4. **CYBOS 연동 설정**
   CYBOS Plus 자동 로그인이 완료된 32-bit Python 환경에서 `bridge/run_bridge.bat` 또는 `bridge/cybos_server.py`를 실행하여 데이터 수신 서버를 가동해야 합니다.

## 💻 주요 사용 방법 (Usage)

- **자막 데이터 전처리**:
  하나의 큰 JSON 파일에 담긴 원본 자막을 개별 `video_id` 파일로 분할하려면 다음 스크립트를 실행합니다.
  ```bash
  python split_transcripts.py
  ```
- **업로드 일자 보완**:
  누락된 영상의 업로드 일자를 웹 스크래핑으로 가져옵니다. (봇 차단 방지를 위해 딜레이 적용됨)
  ```bash
  python fill_missing_dates.py
  ```

- **작업 대기열 업데이트**:
  데이터 처리가 완료된 비디오를 `pending_queue.json` 대기열에서 제외시킵니다.
  ```bash
  python update_queue.py
  ```

## ⚠️ 주의사항

- **CYBOS Plus 연동**: 시스템 특성상 CYBOS Plus 연동은 반드시 32-bit Python 환경을 요구합니다. 메인 AI 파이프라인(64-bit 권장)과 충돌을 막기 위해 32-bit 브리지 서버를 백그라운드에 구동하고 사용해 주세요.
- **크롤링 차단 주의**: YouTube나 기업 정보를 직접 스크래핑할 때에는 대상 서버에 무리를 주지 않도록 시간 지연(sleep)을 준수해 주세요.
