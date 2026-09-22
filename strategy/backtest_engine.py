import os
import json
import sqlite3
import pandas as pd
import numpy as np
import logging
from pathlib import Path
from datetime import datetime
# vectorbt는 필요 시 설치하여 사용 (현재는 pandas 기반 기본 로직)
# import vectorbt as vbt

from strategy.mapper import ThemeMapper
from strategy.time_controller import TimeController
from strategy.modeling import XGBMetaModel, FractionalKellySizer

# 로깅 설정
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class SukaBacktester:
    def __init__(self):
        self.mapper = ThemeMapper()
        self.time_ctrl = TimeController()
        self.meta_model = XGBMetaModel()
        self.sizer = FractionalKellySizer()
        
        self.db_path = Path("data/deshin_backtester.db")
        self.analysis_dir = Path("data/analysis")

    def load_signals(self) -> pd.DataFrame:
        """분석된 자막 데이터(Signals)를 로드합니다."""
        signals = []
        if not self.analysis_dir.exists():
            return pd.DataFrame()

        for file_path in self.analysis_dir.glob("*.json"):
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            # 매매 집행일 계산 (Lookahead Bias 방지)
            trade_date = self.time_ctrl.get_trade_execution_date(data["date"])
            
            signals.append({
                "video_id": data["video_id"],
                "upload_date": data["date"],
                "trade_date": trade_date,
                "theme": data["theme"],
                "sentiment": data["sentiment"]
            })
        
        df = pd.DataFrame(signals)
        if not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
        return df

    def get_price_data(self, ticker: str, start_date: str, end_date: str, market_type: str) -> pd.DataFrame:
        """DB에서 특정 종목의 시계열 가격 데이터(OHLCV)를 가져옵니다."""
        conn = sqlite3.connect(self.db_path)
        # DB 스키마에 맞게 테이블 및 컬럼명 조정
        if market_type == "domestic":
            table = "daily_ohlcv"
            code_col = "stock_code"
        else:
            table = "overseas_daily_ohlcv"
            code_col = "us_code"
        
        query = f"SELECT date, close FROM {table} WHERE {code_col} = ? AND date BETWEEN ? AND ?"
        df = pd.read_sql(query, conn, params=(ticker, start_date, end_date))
        conn.close()
        
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
        return df

    def run_backtest(self):
        """전체 백테스트 프로세스를 실행합니다."""
        logger.info("백테스트 시작...")
        signals_df = self.load_signals()
        if signals_df.empty:
            logger.warning("신호 데이터가 없습니다. 먼저 nlp_engine/gemini_analyzer.py를 실행하여 분석을 진행하세요.")
            return

        all_returns = []
        
        for _, sig in signals_df.iterrows():
            # 1. 테마 -> 종목 매핑 (최신 Voyage-4-Large 사용)
            matches = self.mapper.map_theme_to_tickers(sig["theme"], top_k=3)
            if not matches: continue
            
            # 2. 가장 유사도가 높은 종목 선정
            best_match = matches[0]
            ticker = best_match["code"]
            m_type = best_match["type"]
            
            # 3. 가격 데이터 로드 (매매일로부터 1개월 보유 가정)
            start_dt = sig["trade_date"]
            end_dt = start_dt + pd.Timedelta(days=30)
            
            prices = self.get_price_data(ticker, start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"), m_type)
            if prices.empty: 
                logger.debug(f"데이터 누락: {ticker} ({start_dt} ~ {end_dt})")
                continue
            
            # 4. 수익률 계산
            ret = (prices["close"].iloc[-1] / prices["close"].iloc[0]) - 1
            
            # 5. 메타 레이블링 및 사이즈 결정 (우선 기본값 사용)
            prob = self.meta_model.predict_success_prob(pd.DataFrame()) # Placeholder
            weight = self.sizer.calculate_size(prob)
            
            all_returns.append({
                "trade_date": sig["trade_date"],
                "ticker": ticker,
                "name": best_match["name"],
                "theme": sig["theme"],
                "return": ret,
                "weight": weight,
                "weighted_return": ret * weight
            })
            
        results_df = pd.DataFrame(all_returns)
        if results_df.empty:
            logger.warning("유효한 매매 결과가 없습니다. DB의 가격 데이터 기간을 확인하세요.")
            return

        logger.info(f"백테스트 완료. 총 {len(results_df)}건의 매매 시뮬레이션됨.")
        print("\n[백테스트 결과 요약]")
        print(results_df[["trade_date", "name", "theme", "return", "weighted_return"]])
        
        # 성과 요약
        total_ret = results_df["weighted_return"].sum()
        print(f"\n총 가중 수익률: {total_ret*100:.2f}%")

if __name__ == "__main__":
    backtester = SukaBacktester()
    backtester.run_backtest()
