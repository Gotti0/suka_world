"""Playwright 로 실제 Chrome 을 띄워 자막을 받는다.

youtube-transcript-api 가 IP 차단(IpBlocked)을 맞았을 때 쓰는 경로다. 요청을 파이썬이 만들지 않고,
영상 페이지의 "스크립트 표시" 버튼을 눌러 페이지가 스스로 호출하는 get_transcript 응답을 가로챈다.
그래서 인증 토큰과 쿠키는 페이지가 붙인다. 응답을 못 잡으면 화면에 그려진 자막 조각을 읽는다.

- 프로필: data/.browser_profile (영구 프로필). 로그인은 --login 으로 한 번 직접 한다(선택).
- 대상: video_metadata.json 중 upload_date >= --since 이고 자막 파일이 없으며 자막 없음으로 기록되지 않은 영상.
- 영상 사이 20~35초 무작위 대기. 차단 신호(429/403 응답, google.com/sorry 이동)가 연속 3건이면 멈춘다.
  한 영상만 차단 신호를 받으면 건너뛰고 다음 실행에서 다시 시도한다.
- 저장 형식은 collector.fetch_new_transcripts 와 같다.

    python -m collector.browser_fetcher --login
    python -m collector.browser_fetcher --since 20260513 --limit 3
    python -m collector.browser_fetcher --since 20260513
"""
import argparse
import json
import random
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = BASE_DIR / "data" / ".browser_profile"
METADATA_PATH = BASE_DIR / "data" / "video_metadata.json"
TRANSCRIPT_DIR = BASE_DIR / "data" / "transcripts"
UNAVAILABLE_PATH = BASE_DIR / "data" / "transcripts_unavailable.json"
# 구버전 스크립트 패널(get_transcript 400, 무한 로딩)이라 브라우저로 못 받은 영상.
# 영구 실패가 아니므로 unavailable 과 분리한다. 나중에 --include-retry --use-player 로 다시 시도한다
# (플레이어 자막 경로는 IP 요청 제한이 풀린 뒤에만 의미가 있다). youtube-transcript-api 경로는 쓰지 않는다.
RETRY_PATH = BASE_DIR / "data" / "transcripts_retry.json"
RETRY_REASONS = {"legacy_get_transcript_400"}

TRANSCRIPT_BUTTON = "ytd-video-description-transcript-section-renderer button"
SEGMENT_SELECTOR = "transcript-segment-view-model, ytd-transcript-segment-renderer, ytw-transcript-segment-view-model"
MAX_CONSECUTIVE_BLOCKED = 3


class Blocked(Exception):
    pass


def segments_from_json(data) -> list[str]:
    """get_transcript 응답에서 자막 조각 텍스트를 순서대로 모은다(구조 변경에 대비해 재귀 탐색)."""
    out: list[str] = []

    def walk(obj):
        if isinstance(obj, dict):
            modern = obj.get("transcriptSegmentViewModel")  # get_panel(새 타임라인 패널) 형식
            if isinstance(modern, dict):
                text = (modern.get("simpleText") or "").strip()
                if text:
                    out.append(text)
                return
            seg = obj.get("transcriptSegmentRenderer")  # get_transcript(예전 패널) 형식
            if isinstance(seg, dict):
                text = "".join(r.get("text", "") for r in seg.get("snippet", {}).get("runs", []))
                if text.strip():
                    out.append(text.strip())
                return
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(data)
    return out


def segments_from_dom(page) -> list[str]:
    """화면에 그려진 자막 조각을 스크롤하며 모은다(가상 스크롤 대비)."""
    seen: dict[str, str] = {}
    last_height, unchanged = -1, 0
    for _ in range(200):
        for key, text in page.evaluate(
            """sel => Array.from(document.querySelectorAll(sel)).map(el => {
                const ts = el.querySelector('[class*="imestamp"], .segment-timestamp');
                const tx = el.querySelector('.ytAttributedStringHost, .yt-core-attributed-string, .segment-text');
                const t = ts ? ts.textContent.trim() : '';
                return [t + '|' + (tx ? tx.textContent.trim() : ''), tx ? tx.textContent.trim() : ''];
            })""",
            SEGMENT_SELECTOR,
        ):
            if text and key not in seen:
                seen[key] = text
        height = page.evaluate(
            """sel => { const el = document.querySelector(sel);
                if (!el) return -1;
                const box = el.closest('#segments-container, #content, ytd-transcript-segment-list-renderer') || el.parentElement;
                box.scrollTo(0, box.scrollHeight); return box.scrollHeight; }""",
            SEGMENT_SELECTOR,
        )
        page.wait_for_timeout(350)
        unchanged = unchanged + 1 if height == last_height else 0
        if height < 0 or unchanged >= 3:
            break
        last_height = height
    return list(seen.values())


def segments_from_timedtext(body: str) -> list[str]:
    """플레이어가 받은 자막 파일(json3 또는 srv3/XML)을 텍스트 조각으로 바꾼다."""
    body = body.strip()
    if body.startswith("{"):
        data = json.loads(body)
        out = []
        for ev in data.get("events", []):
            text = "".join(s.get("utf8", "") for s in ev.get("segs", []) or []).strip()
            if text:
                out.append(" ".join(text.split()))
        return out
    import html
    import re
    return [" ".join(html.unescape(re.sub(r"<[^>]+>", "", t)).split())
            for t in re.findall(r"<(?:p|text)\b[^>]*>(.*?)</(?:p|text)>", body, flags=re.S)
            if re.sub(r"<[^>]+>", "", t).strip()]


def segments_from_player(page) -> list[str]:
    """플레이어 자막(CC)을 한국어로 켜고, 플레이어가 스스로 받는 timedtext 응답을 가로챈다."""
    has_ko = page.evaluate("""() => {
        const pr = window.ytInitialPlayerResponse;
        const tracks = pr && pr.captions && pr.captions.playerCaptionsTracklistRenderer
            && pr.captions.playerCaptionsTracklistRenderer.captionTracks || [];
        return tracks.some(t => (t.languageCode || '').startsWith('ko'));
    }""")
    if not has_ko:
        return []
    # 플레이어가 자막을 미리 받아 두었을 수도 있어 expect_response 대신 들어오는 응답을 모두 모은다.
    responses = []
    handler = lambda r: responses.append(r) if "/api/timedtext" in r.url and "lang=ko" in r.url else None
    page.on("response", handler)
    try:
        page.evaluate("""() => {
            const p = document.getElementById('movie_player');
            if (!p) return;
            p.loadModule && p.loadModule('captions');
            p.setOption && p.setOption('captions', 'track', {languageCode: 'ko'});
            p.playVideo && p.playVideo();
        }""")
        for _ in range(40):
            if responses:
                break
            page.wait_for_timeout(500)
    finally:
        page.remove_listener("response", handler)
        page.evaluate("() => { const v = document.querySelector('video'); if (v) v.pause(); }")
    if any(r.status in (403, 429) for r in responses):
        raise Blocked(f"timedtext HTTP {[r.status for r in responses]}")
    for r in responses:
        if r.ok:
            texts = segments_from_timedtext(r.text())
            if texts:
                return texts
    return []


def check_blocked(page) -> None:
    if "google.com/sorry" in page.url or page.locator("text=unusual traffic").count():
        raise Blocked(f"차단 페이지: {page.url}")


def fetch_one(page, video_id: str, use_player: bool = False) -> tuple[str, str | None]:
    """(자막 텍스트, 받을 수 없는 사유)를 돌려준다. 차단 신호면 Blocked.

    스크립트 패널은 두 경로로 채워진다.
    - 챕터가 있는 영상: 새 타임라인 패널이 /youtubei/v1/get_panel 로 자막을 받는다.
    - 챕터가 없는 영상: 예전 패널이 /youtubei/v1/get_transcript 를 부르는데, 2026-09 현재 사람이 눌러도
      400 FAILED_PRECONDITION 으로 실패한다. 이 경우 사유 "legacy_get_transcript_400" 을 돌려준다.
    플레이어 자막(timedtext)은 페이지가 열릴 때 스스로도 요청해 429 가 섞이므로 use_player 일 때만 쓴다.
    """
    page.goto(f"https://www.youtube.com/watch?v={video_id}", wait_until="domcontentloaded")
    check_blocked(page)
    page.wait_for_selector("ytd-watch-flexy", timeout=30000)
    page.evaluate("() => { const v = document.querySelector('video'); if (v) v.pause(); }")
    try:
        page.locator(TRANSCRIPT_BUTTON).first.wait_for(state="attached", timeout=10000)
    except PWTimeout:
        return "", "no_transcript_button"
    # 설명란이 접혀 있으면 버튼이 보이지 않아 Playwright 클릭이 막힌다. 유저스크립트처럼 DOM 에서 직접 누른다.
    page.evaluate("""sel => {
        const exp = document.querySelector('tp-yt-paper-button#expand, #description-inline-expander #expand');
        if (exp) exp.click();
        const b = document.querySelector(sel);
        if (b) b.scrollIntoView({block: 'center'});
    }""", TRANSCRIPT_BUTTON)
    page.wait_for_timeout(500)

    responses = []
    grab = lambda r: responses.append(r) if ("/youtubei/v1/get_panel" in r.url
                                             or "/youtubei/v1/get_transcript" in r.url) else None
    page.on("response", grab)
    try:
        page.evaluate("sel => document.querySelector(sel).click()", TRANSCRIPT_BUTTON)
        for _ in range(40):  # 최대 20초
            if any(r.ok for r in responses) or any("get_transcript" in r.url for r in responses):
                break
            page.wait_for_timeout(500)
        page.wait_for_timeout(1000)
    finally:
        page.remove_listener("response", grab)

    if any(r.status in (403, 429) for r in responses):
        raise Blocked(f"transcript HTTP {[r.status for r in responses]}")
    texts: list[str] = []
    for r in responses:
        if r.ok:
            try:
                texts = segments_from_json(r.json())
            except Exception:
                texts = []
            if texts:
                break
    if not texts:
        try:
            page.wait_for_selector(SEGMENT_SELECTOR, timeout=5000)
            texts = segments_from_dom(page)
        except PWTimeout:
            pass
    if not texts and use_player:
        try:
            texts = segments_from_player(page)
        except PWTimeout:
            pass
    if texts:
        return " ".join(texts), None
    if any("get_transcript" in r.url and r.status == 400 for r in responses):
        return "", "legacy_get_transcript_400"
    return "", None


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_todo(since: str, ids: list[str] | None, include_retry: bool = False) -> list[dict]:
    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    if ids:
        return [m for m in metadata if m["video_id"] in set(ids)]
    skip = set(read_json(UNAVAILABLE_PATH)) | (set() if include_retry else set(read_json(RETRY_PATH)))
    return [m for m in metadata
            if m.get("upload_date", "") >= since
            and not (TRANSCRIPT_DIR / f"{m['video_id']}.json").exists()
            and m["video_id"] not in skip]


CHROME_EXE = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def launch(p):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(
        str(PROFILE_DIR), channel="chrome", headless=False, locale="ko-KR",
        args=["--mute-audio"], viewport={"width": 1280, "height": 900},
        # Playwright 기본 옵션 중 자동화 표시와 확장 프로그램 비활성화를 뺀다(프로필에 설치한 광고 차단기 사용).
        ignore_default_args=["--enable-automation", "--no-sandbox", "--disable-extensions"])


def login() -> None:
    """Google 은 Playwright 가 띄운 창의 로그인을 거부한다. 같은 프로필로 평범한 Chrome 을 띄워 로그인한다.

    Chrome 이 이 프로필로 이미 실행 중이면 수집을 돌리기 전에 모든 창을 닫아야 한다.
    """
    import subprocess
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(CHROME_EXE), f"--user-data-dir={PROFILE_DIR}", "--no-first-run",
                    "https://accounts.google.com/ServiceLogin?service=youtube&continue=https://www.youtube.com/"])
    print("열린 Chrome 창에서 직접 로그인한 뒤 모든 창을 닫으세요.", flush=True)


def run(since: str, limit: int | None, ids: list[str] | None, use_player: bool = False,
        include_retry: bool = False) -> None:
    todo = load_todo(since, ids, include_retry)
    todo = todo[:limit] if limit else todo
    unavailable = json.loads(UNAVAILABLE_PATH.read_text(encoding="utf-8")) if UNAVAILABLE_PATH.exists() else {}
    print(f"대상 {len(todo)}건", flush=True)
    stats = {"saved": 0, "retry": 0, "unavailable": 0, "empty": 0, "blocked": 0}
    consecutive_blocked = 0
    with sync_playwright() as p:
        ctx = launch(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for n, m in enumerate(todo, 1):
            vid = m["video_id"]
            reason = None
            try:
                text, reason = fetch_one(page, vid, use_player)
                consecutive_blocked = 0
            except Blocked as e:
                # 특정 영상만 429 를 받는 경우가 있어(예: 1eLMwHduciU) 한 건으로는 IP 차단이라 단정하지 않는다.
                consecutive_blocked += 1
                stats["blocked"] += 1
                print(f"{vid} 차단 신호 ({consecutive_blocked}/{MAX_CONSECUTIVE_BLOCKED}): {e}", flush=True)
                if consecutive_blocked >= MAX_CONSECUTIVE_BLOCKED:
                    print(f"연속 차단으로 중단 ({n}/{len(todo)})", flush=True)
                    break
                time.sleep(random.uniform(20, 35))
                continue
            except PWTimeout as e:
                text = ""
                print(f"{vid} 시간 초과: {str(e)[:120]}", flush=True)
            if reason in RETRY_REASONS:
                retry = read_json(RETRY_PATH)
                retry[vid] = f"browser: {reason}"
                RETRY_PATH.write_text(json.dumps(retry, ensure_ascii=False, indent=2), encoding="utf-8")
                stats["retry"] += 1
            elif reason:
                # 사유가 남은 영상은 다음 실행에서 건너뛴다. 다시 시도하려면 transcripts_unavailable.json 에서 지운다.
                unavailable[vid] = f"browser: {reason}"
                UNAVAILABLE_PATH.write_text(json.dumps(unavailable, ensure_ascii=False, indent=2), encoding="utf-8")
                stats["unavailable"] += 1
            elif not text:
                stats["empty"] += 1  # 다음 실행에서 다시 시도한다
            else:
                record = {"video_id": vid, "title": m.get("title", ""), "upload_date": m.get("upload_date", ""),
                          "published_at": m.get("published_at", ""), "transcript": text}
                (TRANSCRIPT_DIR / f"{vid}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2),
                                                            encoding="utf-8")
                stats["saved"] += 1
            print(n, len(todo), vid, len(text or ""), reason or "", stats, flush=True)
            if n < len(todo):
                time.sleep(random.uniform(20, 35))
        ctx.close()
    print({"todo": len(todo), **stats}, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--since", default="20260513")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", nargs="*", action="extend")
    ap.add_argument("--use-player", action="store_true", help="플레이어 자막(timedtext) 경로도 시도")
    ap.add_argument("--include-retry", action="store_true", help="재시도 목록(transcripts_retry.json)도 대상에 포함")
    args = ap.parse_args()
    if args.login:
        login()
    else:
        run(args.since, args.limit, args.ids, args.use_player, args.include_retry)


if __name__ == "__main__":
    main()
