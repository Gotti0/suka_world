"""YouTube Data API v3 videos.list 로 모든 자막 파일의 게시 시각을 채운다.

- 50개씩 묶어 호출한다(호출당 할당량 1 단위, 2,892건 ≈ 58 단위).
- API 키는 .env 의 YOUTUBE_API_KEY 에서 읽고 URL 이 아니라 X-goog-api-key 헤더로 보낸다.
- 각 파일에 published_at(UTC ISO 8601)을 쓰고, upload_date 는 한국시간(KST) 기준 YYYYMMDD 로 통일한다.
  기존 upload_date 는 형식이 섞여 있어(YYYYMMDD, 태평양시 ISO) API 값으로 덮어쓴다.
- API 가 돌려주지 않은 영상(삭제·비공개)은 data/dates_missing.json 에 기록한다.

    python -m collector.fill_dates_api          # 실제 기록
    python -m collector.fill_dates_api --dry-run
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
TRANSCRIPT_DIR = BASE_DIR / "data" / "transcripts"
MISSING_PATH = BASE_DIR / "data" / "dates_missing.json"
API_URL = "https://www.googleapis.com/youtube/v3/videos"
KST = timezone(timedelta(hours=9))
BATCH = 50


def fetch_published(ids: list[str], api_key: str) -> dict[str, str]:
    resp = requests.get(
        API_URL,
        params={"part": "snippet", "id": ",".join(ids), "maxResults": BATCH},
        headers={"X-goog-api-key": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    return {it["id"]: it["snippet"]["publishedAt"] for it in resp.json().get("items", [])}


def to_kst_date(published_at: str) -> str:
    dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    return dt.astimezone(KST).strftime("%Y%m%d")


def main(dry_run: bool) -> None:
    load_dotenv(BASE_DIR / ".env")
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        sys.exit(".env 에 YOUTUBE_API_KEY 가 없습니다.")

    files = sorted(TRANSCRIPT_DIR.glob("*.json"))
    ids = [f.stem for f in files]
    published: dict[str, str] = {}
    for i in range(0, len(ids), BATCH):
        published.update(fetch_published(ids[i:i + BATCH], api_key))

    missing = [vid for vid in ids if vid not in published]
    updated = 0
    for f in files:
        vid = f.stem
        if vid not in published:
            continue
        record = json.loads(f.read_text(encoding="utf-8"))
        record["published_at"] = published[vid]
        record["upload_date"] = to_kst_date(published[vid])
        if not dry_run:
            f.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        updated += 1

    if not dry_run:
        MISSING_PATH.write_text(json.dumps(missing, ensure_ascii=False, indent=2), encoding="utf-8")
    dates = sorted(to_kst_date(p) for p in published.values())
    print({"files": len(files), "updated": updated, "missing": len(missing),
           "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
           "dry_run": dry_run})


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
