import os
import json
import urllib.request
import re
import time

TRANSCRIPTS_DIR = os.path.join("data", "transcripts")

def get_upload_date(video_id):
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        # 봇 차단을 피하기 위해 User-Agent 헤더 추가
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        html = urllib.request.urlopen(req).read().decode('utf-8')
        
        # 정규표현식으로 uploadDate 또는 datePublished 추출
        match = re.search(r'<meta itemprop="uploadDate" content="([^"]+)">', html)
        if match:
            return match.group(1)
        
        match = re.search(r'<meta itemprop="datePublished" content="([^"]+)">', html)
        if match:
            return match.group(1)
            
    except Exception as e:
        print(f"[{video_id}] Fetch Error: {e}")
    return ""

def main():
    if not os.path.exists(TRANSCRIPTS_DIR):
        print(f"디렉토리를 찾을 수 없습니다: {TRANSCRIPTS_DIR}")
        return

    count = 0
    updated = 0

    print(f"[{TRANSCRIPTS_DIR}] 디렉토리 내의 JSON 파일을 검사합니다...")
    
    for filename in os.listdir(TRANSCRIPTS_DIR):
        if not filename.endswith(".json"):
            continue
        
        filepath = os.path.join(TRANSCRIPTS_DIR, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                print(f"JSON 파싱 에러: {filename}")
                continue
        
        # upload_date가 비어있거나 아예 없는 경우
        if not data.get("upload_date"):
            video_id = data.get("video_id")
            if not video_id:
                # 파일명에서 확장자를 제외한 부분을 video_id로 간주
                video_id = filename.replace(".json", "")
            
            print(f"[{video_id}] 업로드 날짜 결측치 발견. 스크래핑 시도 중...")
            date = get_upload_date(video_id)
            
            if date:
                data["upload_date"] = date
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f" -> 성공: {date} 로 업데이트 완료.")
                updated += 1
                
                # YouTube 서버에 무리를 주지 않기 위해 1초 대기
                time.sleep(1) 
            else:
                print(f" -> 실패: 날짜 정보를 찾을 수 없습니다.")
        
        count += 1

    print("-" * 30)
    print(f"총 검사한 파일: {count}개")
    print(f"업데이트된 파일: {updated}개")

if __name__ == "__main__":
    main()
