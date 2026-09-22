"""대용량 자막 JSON(리스트)을 영상별 파일 data/transcripts/<video_id>.json 으로 나눈다.

이미 있는 파일은 덮어쓰지 않는다. split_transcripts.py 와 달리 입력 경로를 인자로 받는다.

    python -m collector.import_backlog data/transcripts_1137건.json
"""
import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TRANSCRIPT_DIR = DATA_DIR / "transcripts"
FIELDS = ("video_id", "title", "upload_date", "transcript")


def import_backlog(src: Path) -> dict:
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    items = json.loads(src.read_text(encoding="utf-8"))
    stats = {"written": 0, "skipped_existing": 0, "skipped_no_id": 0}
    for item in items:
        video_id = item.get("video_id")
        if not video_id:
            stats["skipped_no_id"] += 1
            continue
        out = TRANSCRIPT_DIR / f"{video_id}.json"
        if out.exists():
            stats["skipped_existing"] += 1
            continue
        record = {k: item.get(k, "") or "" for k in FIELDS}
        out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        stats["written"] += 1
    return stats


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m collector.import_backlog <backlog.json>")
    print(import_backlog(Path(sys.argv[1])))
