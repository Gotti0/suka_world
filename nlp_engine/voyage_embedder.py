import os
import logging
from typing import List, Optional
import voyageai
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# 로깅 설정
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class VoyageEmbedder:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("VOYAGE_API_KEY")
        if not self.api_key:
            logger.warning("VOYAGE_API_KEY가 설정되지 않았습니다.")
        
        if self.api_key:
            self.client = voyageai.Client(api_key=self.api_key)
        else:
            self.client = None
            
        self.model_id = "voyage-4-large"

    def get_embeddings(self, texts: List[str], input_type: str = "document") -> List[List[float]]:
        """텍스트 리스트를 임베딩 벡터로 변환합니다."""
        if not self.client:
            logger.error("Voyage 클라이언트가 초기화되지 않았습니다. API Key를 확인하세요.")
            return []

        try:
            # voyage-3 모델부터는 input_type(document, query) 지정 권장
            result = self.client.embed(
                texts, 
                model=self.model_id, 
                input_type=input_type
            )
            return result.embeddings
        except Exception as e:
            logger.error(f"Voyage 임베딩 중 오류 발생: {e}")
            return []

if __name__ == "__main__":
    # embedder = VoyageEmbedder()
    # vectors = embedder.get_embeddings(["반도체", "물적분할"])
    # print(len(vectors[0]) if vectors else "No vectors")
    print("VoyageEmbedder 클래스가 로드되었습니다.")
