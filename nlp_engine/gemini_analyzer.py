import os
import json
import logging
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field
from google import genai
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# 로깅 설정
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 경로 설정
BASE_DIR = Path(__file__).resolve().parent.parent
TRANSCRIPT_DIR = BASE_DIR / "data" / "transcripts"
OUTPUT_DIR = BASE_DIR / "data" / "analysis"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 구조화된 응답을 위한 Pydantic 모델
class AnalysisResult(BaseModel):
    theme: str = Field(description="주요 투자 테마 또는 기업명 (예: 반도체, 삼성전자, 중복상장 규제)")
    sentiment: int = Field(description="테마에 대한 감성 (1: 긍정, 0: 중립, -1: 부정)")
    reason: str = Field(description="해당 테마와 감성을 도출한 핵심 근거 (한 문장)")

class GeminiAnalyzer:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            # 환경변수가 없을 경우 사용자에게 알림
            logger.warning("GEMINI_API_KEY가 설정되지 않았습니다. API 호출 시 오류가 발생할 수 있습니다.")
        
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None
            
        self.model_id = "gemini-3-flash-preview" 

    def analyze_transcript(self, transcript_text: str) -> Optional[AnalysisResult]:
        """Gemini를 호출하여 자막 텍스트를 분석합니다."""
        if not self.client:
            logger.error("Gemini 클라이언트가 초기화되지 않았습니다. API Key를 확인하세요.")
            return None

        prompt = f"""
        당신은 금융 전문 AI 분석가입니다. 다음 유튜브 방송 자막을 읽고, 투자 관점에서 가장 핵심적인 '테마'나 '종목/섹터'를 하나만 추출하세요.
        또한 그에 대한 방송의 전반적인 감성을 판별하세요.
        
        [자막 내용]
        {transcript_text[:15000]}
        
        [지침]
        1. theme: 가장 비중 있게 다뤄진 핵심 키워드 (예: "물적분할 금지", "AI 반도체", "저PBR")
        2. sentiment: 호재/긍정적 전망이면 1, 악재/부정적이면 -1, 단순 정보 전달이면 0
        3. reason: 왜 그렇게 판단했는지 요약
        
        응답은 반드시 순수 JSON 포맷으로 작성하세요.
        """
        
        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config={
                    'response_mime_type': 'application/json',
                    'response_schema': AnalysisResult,
                }
            )
            
            # 응답 파싱
            if response.text:
                return AnalysisResult.model_validate_json(response.text)
            return None
            
        except Exception as e:
            logger.error(f"Gemini 분석 중 오류 발생: {e}")
            return None

    def process_all_samples(self):
        """저장된 자막 파일들을 순회하며 분석을 수행합니다."""
        transcript_files = list(TRANSCRIPT_DIR.glob("*.json"))
        logger.info(f"총 {len(transcript_files)}개의 자막 파일 분석 시작...")
        
        results = []
        for file_path in transcript_files:
            logger.info(f"분석 중: {file_path.name}")
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            analysis = self.analyze_transcript(data["transcript"])
            if analysis:
                result_entry = {
                    "video_id": data["video_id"],
                    "date": data["upload_date"],
                    "title": data["title"],
                    **analysis.model_dump()
                }
                results.append(result_entry)
                
                # 개별 파일 저장
                with open(OUTPUT_DIR / f"analysis_{data['video_id']}.json", "w", encoding="utf-8") as f:
                    json.dump(result_entry, f, ensure_ascii=False, indent=2)
            
        # 통합 결과 저장
        with open(BASE_DIR / "data" / "suka_factors_sample.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
            
        logger.info(f"분석 완료. 총 {len(results)}개의 팩터 생성됨.")

if __name__ == "__main__":
    # 실행을 위해서는 GEMINI_API_KEY 환경변수가 필요합니다.
    try:
        analyzer = GeminiAnalyzer()
        analyzer.process_all_samples()
    except Exception as e:
        print(f"테스트 중 오류 발생: {e}")
