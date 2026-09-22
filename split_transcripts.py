import json
import os

input_file = r"c:\Users\Hyunwoo_Room\Downloads\Utility\suka_world\docs\transcripts_1294건.json"
output_dir = r"c:\Users\Hyunwoo_Room\Downloads\Utility\suka_world\data\transcripts"

# 출력 폴더 생성
os.makedirs(output_dir, exist_ok=True)

try:
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    count = 0
    skipped = 0
    
    for item in data:
        video_id = item.get("video_id")
        if not video_id:
            skipped += 1
            continue
            
        out_path = os.path.join(output_dir, f"{video_id}.json")
        
        # 개별 JSON 파일로 저장
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(item, f, ensure_ascii=False, indent=2)
            
        count += 1

    print(f"작업 완료: 총 {count}개의 자막 파일이 {output_dir} 경로에 분할 저장되었습니다.")
    if skipped > 0:
        print(f"경고: video_id가 없는 {skipped}개의 항목은 건너뛰었습니다.")
        
except Exception as e:
    print(f"오류 발생: {str(e)}")
