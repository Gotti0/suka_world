import json
import os

queue_file = r"c:\Users\Hyunwoo_Room\Downloads\Utility\suka_world\data\pending_queue.json"
transcripts_dir = r"c:\Users\Hyunwoo_Room\Downloads\Utility\suka_world\data\transcripts"

try:
    # 1. 완료된 파일들의 video_id 목록 수집
    completed_ids = set()
    for filename in os.listdir(transcripts_dir):
        if filename.endswith(".json"):
            video_id = filename[:-5]
            completed_ids.add(video_id)
            
    print(f"완료된 자막 파일 개수: {len(completed_ids)}")

    # 2. 현재 큐 읽기
    with open(queue_file, 'r', encoding='utf-8') as f:
        queue = json.load(f)
        
    initial_count = len(queue)
    print(f"현재 큐에 남은 항목 수: {initial_count}")

    # 3. 완료된 항목 필터링
    new_queue = [item for item in queue if item.get("video_id") not in completed_ids]
    
    final_count = len(new_queue)
    removed_count = initial_count - final_count
    
    # 4. 갱신된 큐 저장
    with open(queue_file, 'w', encoding='utf-8') as f:
        json.dump(new_queue, f, ensure_ascii=False, indent=2)
        
    print(f"큐 갱신 완료: {removed_count}개의 항목이 대기열에서 제거되었습니다.")
    print(f"앞으로 남은 스크래핑 대상 항목 수: {final_count}")
    
except Exception as e:
    print(f"오류 발생: {str(e)}")
