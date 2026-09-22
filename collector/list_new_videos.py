"""YouTube Data API 로 채널의 새 영상 목록을 받아 video_metadata.json 에 덧붙인다.

- 채널 업로드 재생목록(UU...)을 최신순으로 넘기다가 기준 시각 이전 영상이 나오면 멈춘다.
- 기존 목록(yt-dlp 로 채널 '동영상' 탭 수집)과 기준을 맞추려고 쇼츠(180초 이하)와 라이브 다시보기를 뺀다.
- 이미 목록에 있는 영상은 건너뛴다. 새 항목에는 published_at 과 upload_date(KST)를 함께 쓴다.

    python -m collector.list_new_videos --after 2026-05-13 --dry-run
    python -m collector.list_new_videos --after 2026-05-13
"""
import argparse
import json
import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv

from collector.fill_dates_api import to_kst_date

BASE_DIR = Path(__file__).resolve().parent.parent
METADATA_PATH = BASE_DIR / "data" / "video_metadata.json"
API = "https://www.googleapis.com/youtube/v3"
HANDLES = ["@syukaworld", "@moneymoneycomics"]
SHORTS_MAX_SECONDS = 180


def get(endpoint: str, api_key: str, **params) -> dict:
    resp = requests.get(f"{API}/{endpoint}", params=params, headers={"X-goog-api-key": api_key}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def iso_duration_seconds(d: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    days, h, mi, s = (int(x or 0) for x in m.groups())
    return days * 86400 + h * 3600 + mi * 60 + s


def uploads_after(handle: str, after_iso: str, api_key: str) -> list[dict]:
    ch = get("channels", api_key, part="contentDetails", forHandle=handle)["items"][0]
    playlist = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    found, token = [], None
    while True:
        page = get("playlistItems", api_key, part="contentDetails", playlistId=playlist,
                   maxResults=50, **({"pageToken": token} if token else {}))
        items = page.get("items", [])
        fresh = [it["contentDetails"] for it in items if it["contentDetails"].get("videoPublishedAt", "") > after_iso]
        found.extend(fresh)
        token = page.get("nextPageToken")
        if not token or len(fresh) < len(items):
            return found


def describe(ids: list[str], api_key: str) -> list[dict]:
    out = []
    for i in range(0, len(ids), 50):
        out.extend(get("videos", api_key, part="snippet,contentDetails,liveStreamingDetails",
                       id=",".join(ids[i:i + 50]))["items"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--after", required=True, help="이 날짜(UTC, YYYY-MM-DD) 이후 게시 영상")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    load_dotenv(BASE_DIR / ".env")
    api_key = os.environ["YOUTUBE_API_KEY"]
    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    known = {m["video_id"] for m in metadata}

    stats = {"listed": 0, "known": 0, "shorts": 0, "live": 0, "added": 0}
    new_items = []
    for handle in HANDLES:
        ids = [c["videoId"] for c in uploads_after(handle, f"{args.after}T00:00:00Z", api_key)]
        stats["listed"] += len(ids)
        for v in describe([i for i in ids if i not in known], api_key):
            if "liveStreamingDetails" in v:
                stats["live"] += 1
                continue
            if iso_duration_seconds(v["contentDetails"]["duration"]) <= SHORTS_MAX_SECONDS:
                stats["shorts"] += 1
                continue
            pub = v["snippet"]["publishedAt"]
            new_items.append({"video_id": v["id"], "title": v["snippet"]["title"],
                              "url": f"https://www.youtube.com/watch?v={v['id']}",
                              "channel": handle, "published_at": pub, "upload_date": to_kst_date(pub)})
        stats["known"] += len([i for i in ids if i in known])

    stats["added"] = len(new_items)
    if not args.dry_run and new_items:
        METADATA_PATH.write_text(json.dumps(metadata + new_items, ensure_ascii=False, indent=2), encoding="utf-8")
    dates = sorted(n["upload_date"] for n in new_items)
    print({**stats, "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
           "dry_run": args.dry_run})


if __name__ == "__main__":
    main()
