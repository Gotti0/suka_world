import os
import json
import sqlite3
import logging
import numpy as np
from pathlib import Path
from typing import List, Dict, Any
from scipy.spatial.distance import cosine
from nlp_engine.voyage_embedder import VoyageEmbedder

# 로깅 설정
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 경로 설정
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "deshin_backtester.db"
EMBED_CACHE_PATH = BASE_DIR / "data" / "stock_embeddings.json"

class ThemeMapper:
    def __init__(self):
        self.embedder = VoyageEmbedder()
        self.stock_data = []
        self.stock_embeddings = {}
        self._load_stock_master()

    def _load_stock_master(self):
        """DB에서 국내 및 해외 종목 마스터 정보를 로드합니다."""
        if not DB_PATH.exists():
            logger.error(f"DB 파일을 찾을 수 없습니다: {DB_PATH}")
            return

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            # 1. 국내 종목 로드
            cursor.execute("SELECT stock_code, stock_name, market FROM stock_master WHERE is_active = 1")
            domestic_rows = cursor.fetchall()
            for r in domestic_rows:
                self.stock_data.append({
                    "code": r[0],
                    "name": r[1],
                    "market": r[2],
                    "text": f"{r[1]} ({r[2]})",
                    "type": "domestic"
                })
            
            # 2. 해외 종목 로드
            cursor.execute("SELECT us_code, us_name, category FROM overseas_master")
            overseas_rows = cursor.fetchall()
            for r in overseas_rows:
                self.stock_data.append({
                    "code": r[0],
                    "name": r[1],
                    "market": "OVERSEAS",
                    "text": f"{r[1]} ({r[2]})" if r[2] else r[1],
                    "type": "overseas"
                })
                
            logger.info(f"총 {len(self.stock_data)}개의 국내외 종목 정보를 로드했습니다.")
        except Exception as e:
            logger.error(f"주식 마스터 로드 중 오류: {e}")
        finally:
            conn.close()

    def build_embedding_cache(self, force=False):
        """모든 종목의 임베딩을 생성하여 캐시합니다. (비용 주의!)"""
        if EMBED_CACHE_PATH.exists() and not force:
            logger.info("기존 임베딩 캐시를 로드합니다.")
            try:
                with open(EMBED_CACHE_PATH, "r", encoding="utf-8") as f:
                    self.stock_embeddings = json.load(f)
                return
            except Exception as e:
                logger.warning(f"캐시 로드 실패, 새로 생성합니다: {e}")

        logger.info("신규 임베딩 캐시 구축 시작... (Voyage-4-Large API 호출)")
        # 대량 호출을 위해 배치 처리
        batch_size = 100
        for i in range(0, len(self.stock_data), batch_size):
            batch = self.stock_data[i : i + batch_size]
            texts = [item["text"] for item in batch]
            codes = [item["code"] for item in batch]
            
            embeddings = self.embedder.get_embeddings(texts)
            for code, emb in zip(codes, embeddings):
                self.stock_embeddings[code] = emb
            
            if (i + batch_size) % 1000 == 0:
                logger.info(f"진행 중... ({i + batch_size}/{len(self.stock_data)})")

        with open(EMBED_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(self.stock_embeddings, f)
        logger.info("임베딩 캐시 저장 완료.")

    def map_theme_to_tickers(self, theme: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """테마와 가장 유사한 종목들을 반환합니다."""
        if not self.stock_embeddings:
            self.build_embedding_cache()

        theme_vec = self.embedder.get_embeddings([theme], input_type="query")[0]
        
        similarities = []
        for stock in self.stock_data:
            code = stock["code"]
            if code not in self.stock_embeddings:
                continue
            
            # 코사인 유사도 계산 (1 - distance)
            sim = 1 - cosine(theme_vec, self.stock_embeddings[code])
            similarities.append({
                "code": stock["code"],
                "name": stock["name"],
                "market": stock["market"],
                "type": stock["type"],
                "similarity": float(sim)
            })
        
        # 유사도 순 정렬
        sorted_sims = sorted(similarities, key=lambda x: x["similarity"], reverse=True)
        return sorted_sims[:top_k]

if __name__ == "__main__":
    mapper = ThemeMapper()
    # 주의: 처음 실행 시 build_embedding_cache()가 실행되어 비용이 발생할 수 있습니다.
    test_theme = "미용 의료기기 및 클래시스"
    matches = mapper.map_theme_to_tickers(test_theme)
    
    print(f"\n[테마: {test_theme} 매핑 결과]")
    for m in matches:
        print(f"- {m['name']} ({m['market']}/{m['type']}): {m['similarity']:.4f}")
