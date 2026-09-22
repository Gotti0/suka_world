import os
import json
import logging
import time
import random
import yt_dlp
import http.cookiejar
import requests
from pathlib import Path
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TRANSCRIPT_DIR = DATA_DIR / "transcripts"
TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)

def fetch_upload_date(video_url: str) -> str:
    """yt-dlp를 사용하여 개별 영상의 정확한 업로드 날짜(YYYYMMDD)를 추출합니다. (재시도 로직 포함)"""
    ydl_opts = {'quiet': True, 'extract_flat': False, 'skip_download': True}
    max_retries = 3
    base_delay = 5

    for attempt in range(max_retries):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                return info.get('upload_date', '') # YYYYMMDD 형태 반환
        except Exception as e:
            delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
            logger.warning(f"업로드 날짜 추출 실패 (시도 {attempt+1}/{max_retries}) {video_url}: {e}. {delay:.1f}초 후 재시도...")
            time.sleep(delay)
            
    return ""

def fetch_transcripts():
    """video_metadata.json을 읽어 각 영상의 자막과 업로드 날짜를 저장합니다."""
    metadata_file = DATA_DIR / "video_metadata.json"
    if not metadata_file.exists():
        logger.error(f"메타데이터 파일이 없습니다. 먼저 youtube_scraper.py를 실행하세요: {metadata_file}")
        return
        
    with open(metadata_file, "r", encoding="utf-8") as f:
        metadata_list = json.load(f)
        
    formatter = TextFormatter()
    success_count = 0
    
    logger.info(f"총 {len(metadata_list)}개의 비디오 자막 추출 대기 중...")
    
    LIMIT_DATE = "20210514" # 5년 전 기준일 (YYYYMMDD)
    
    for item in metadata_list:
        video_id = item["video_id"]
        title = item["title"]
        video_url = item["url"]
        
        output_file = TRANSCRIPT_DIR / f"{video_id}.json"
        if output_file.exists():
            logger.info(f"이미 존재하는 자막 스킵: {title} ({video_id})")
            continue
            
        # 0. 업로드 날짜 확인 및 5년 제한 필터링
        upload_date = item.get("upload_date")
        if not upload_date:
            upload_date = fetch_upload_date(video_url)
        
        if upload_date and upload_date < LIMIT_DATE:
            logger.info(f"5년 이전 영상이므로 수집 제외: {title} ({upload_date})")
            continue

        logger.info(f"자막 다운로드 중: {title} ({video_id})")
        
        max_retries = 3
        base_delay = 30
        success = False
        
        for attempt in range(max_retries):
            try:
                # 1. 자막 다운로드 (한국어)
                cookie_path = DATA_DIR.parent / "docs" / "temp_cookies.md"
                cookie_jar = http.cookiejar.MozillaCookieJar(cookie_path)
                cookie_jar.load(ignore_discard=True, ignore_expires=True)
                
                session = requests.Session()
                session.cookies = cookie_jar
                
                api = YouTubeTranscriptApi(http_client=session)
                transcript_obj = api.fetch(video_id, languages=['ko'])
                text_data = " ".join([snippet.text for snippet in transcript_obj.snippets])
                
                # 2. 결과물 저장
                result = {
                    "video_id": video_id,
                    "title": title,
                    "upload_date": upload_date,
                    "transcript": text_data
                }
                
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                    
                success_count += 1
                success = True
                
                # 성공 시에도 기본 지연 (IP 차단 예방)
                time.sleep(random.uniform(15, 30))
                break
                
            except Exception as e:
                if "RequestBlocked" in str(e) or "IP" in str(e):
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 5)
                    logger.error(f"IP 차단 감지됨 (시도 {attempt+1}/{max_retries}) [{video_id}]: {delay:.1f}초 후 지수 백오프 재시도...")
                    time.sleep(delay)
                else:
                    logger.error(f"자막 다운로드 실패 [{video_id}]: {e}")
                    break # IP 차단 외의 일반 에러(자막 없음 등)는 재시도하지 않음
            
    logger.info(f"작업 완료: 총 {success_count}개의 신규 자막 다운로드 및 저장 성공.")

if __name__ == "__main__":
    fetch_transcripts()
