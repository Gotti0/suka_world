"""video_metadata.json 중 자막 파일이 없는 영상의 한국어 자막을 받아 영상별 파일로 쓴다.

- 목록의 published_at/upload_date 를 그대로 옮긴다(list_new_videos.py 가 채움).
- 영상 사이 20~35초 무작위 대기. 차단 신호(RequestBlocked, IpBlocked, 429)를 받으면 즉시 멈춘다.
- 자막이 없는 영상은 data/transcripts_unavailable.json 에 사유와 함께 남겨 다음 실행에서 건너뛴다.

    python -m collector.fetch_new_transcripts --since 20260513
"""
import argparse
import json
import random
import time
from pathlib import Path

from youtube_transcript_api import YouTubeTranscriptApi

BASE_DIR = Path(__file__).resolve().parent.parent
METADATA_PATH = BASE_DIR / "data" / "video_metadata.json"
TRANSCRIPT_DIR = BASE_DIR / "data" / "transcripts"
UNAVAILABLE_PATH = BASE_DIR / "data" / "transcripts_unavailable.json"
BLOCK_MARKERS = ("RequestBlocked", "IpBlocked", "429", "Too Many Requests")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="upload_date(KST, YYYYMMDD) 이상만")
    args = ap.parse_args()

    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    unavailable = json.loads(UNAVAILABLE_PATH.read_text(encoding="utf-8")) if UNAVAILABLE_PATH.exists() else {}
    todo = [m for m in metadata
            if m.get("upload_date", "") >= args.since
            and not (TRANSCRIPT_DIR / f"{m['video_id']}.json").exists()
            and m["video_id"] not in unavailable]
    print(f"대상 {len(todo)}건", flush=True)

    api = YouTubeTranscriptApi()
    stats = {"saved": 0, "unavailable": 0}
    for n, m in enumerate(todo, 1):
        vid = m["video_id"]
        try:
            fetched = api.fetch(vid, languages=["ko"])
        except Exception as e:  # 라이브러리 예외 종류가 버전마다 달라 이름으로 구분한다
            reason = f"{type(e).__name__}: {str(e)[:200]}"
            if any(k in reason for k in BLOCK_MARKERS):
                print(f"차단 신호로 중단 ({n}/{len(todo)}): {reason}", flush=True)
                break
            unavailable[vid] = reason
            UNAVAILABLE_PATH.write_text(json.dumps(unavailable, ensure_ascii=False, indent=2), encoding="utf-8")
            stats["unavailable"] += 1
        else:
            record = {"video_id": vid, "title": m.get("title", ""), "upload_date": m.get("upload_date", ""),
                      "published_at": m.get("published_at", ""),
                      "transcript": " ".join(s.text for s in fetched.snippets)}
            (TRANSCRIPT_DIR / f"{vid}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2),
                                                        encoding="utf-8")
            stats["saved"] += 1
        print(n, len(todo), vid, stats, flush=True)
        if n < len(todo):
            time.sleep(random.uniform(20, 35))
    print({"todo": len(todo), **stats}, flush=True)


if __name__ == "__main__":
    main()
