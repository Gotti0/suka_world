import os
import json
from pathlib import Path

DATA_DIR = Path(r"c:\Users\Hyunwoo_Room\Downloads\Utility\suka_world\data")
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
ANALYSIS_DIR = DATA_DIR / "analysis"
METADATA_FILE = DATA_DIR / "video_metadata.json"
OUTPUT_FILE = DATA_DIR / "pending_queue.json"

def create_pending_queue():
    # 이미 처리된 비디오 ID 수집
    processed_ids = set()
    
    if TRANSCRIPTS_DIR.exists():
        for f in TRANSCRIPTS_DIR.glob("*.json"):
            processed_ids.add(f.stem)
            
    if ANALYSIS_DIR.exists():
        for f in ANALYSIS_DIR.glob("analysis_*.json"):
            video_id = f.stem.replace("analysis_", "")
            processed_ids.add(video_id)
            
    print(f"기존에 수집 완료된 비디오 개수: {len(processed_ids)}개")
    
    # 메타데이터 로드
    with open(METADATA_FILE, "r", encoding="utf-8") as f:
        metadata = json.load(f)
        
    # 필터링
    pending_queue = []
    LIMIT_DATE = "20210514"
    
    for item in metadata:
        video_id = item["video_id"]
        upload_date = item.get("upload_date", "")
        
        # 1. 2021년 5월 14일 이전 영상 제외
        if upload_date and upload_date < LIMIT_DATE:
            continue
            
        # 2. 이미 수집된 영상 제외
        if video_id in processed_ids:
            continue
            
        pending_queue.append(item)
        
    # 결과 저장
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(pending_queue, f, ensure_ascii=False, indent=2)
        
    print(f"새로 수집해야 할 비디오 개수: {len(pending_queue)}개")
    print(f"[{OUTPUT_FILE.name}] 파일이 생성되었습니다. 이 파일을 유저스크립트에 업로드하세요!")

if __name__ == "__main__":
    create_pending_queue()
