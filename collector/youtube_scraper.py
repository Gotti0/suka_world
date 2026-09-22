import os
import json
import yt_dlp
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

def fetch_channel_videos(channel_url: str, max_downloads: int = None):
    """
    지정된 유튜브 채널의 비디오 메타데이터(ID, 제목, URL)를 추출하여 저장합니다.
    extract_flat 옵션을 사용하여 빠르게 목록만 수집합니다.
    """
    ydl_opts = {
        'extract_flat': True,
        'quiet': False,
        'playlistend': max_downloads,
    }
    
    metadata_list = []
    logger.info(f"채널 비디오 목록 추출 시작: {channel_url} (최대 {max_downloads}개)")
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)
        
        if 'entries' in info:
            entries = info['entries']
        else:
            entries = [info]
            
        for entry in entries:
            if not entry:
                continue
            
            video_id = entry.get('id')
            title = entry.get('title')
            url = entry.get('url') or f"https://www.youtube.com/watch?v={video_id}"
            
            # extract_flat 모드에서는 upload_date가 없는 경우가 많으므로 기본적으로 수집 항목만 저장
            metadata = {
                "video_id": video_id,
                "title": title,
                "url": url
            }
            metadata_list.append(metadata)
            
    logger.info(f"채널({channel_url})에서 {len(metadata_list)}개의 비디오 메타데이터 추출 완료")
    return metadata_list

if __name__ == "__main__":
    # 수집 대상 채널 목록 (메인 채널 및 서브 채널)
    TARGET_CHANNELS = [
        "https://www.youtube.com/@syukaworld/videos",
        "https://www.youtube.com/@moneymoneycomics/videos"
    ]
    
    all_metadata = []
    for channel in TARGET_CHANNELS:
        channel_metadata = fetch_channel_videos(channel, max_downloads=None)
        all_metadata.extend(channel_metadata)
        
    # 최종 결과 저장
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DATA_DIR / "video_metadata.json"
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_metadata, f, ensure_ascii=False, indent=2)
        
    logger.info(f"전체 채널 합계 {len(all_metadata)}개의 비디오 메타데이터 저장 완료: {output_path}")
