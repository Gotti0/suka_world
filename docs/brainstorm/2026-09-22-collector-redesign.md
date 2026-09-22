# 자막 수집기 재설계 — Playwright 단일 경로

> **보류 (2026-09-22)**: 프로젝트 목표가 일회성 개념글("슈카 말만 듣고 2021~2026")로 재정의되어 우선순위에서 밀렸다. 2021~2026 자막은 이미 확보되어 있다. 글을 주기적으로 갱신하기로 하면 재개한다. 현행 브리프는 `2026-09-22-suka-etf-p24.md`.

## 한 줄 요약
수동 파일 이동과 IP 차단에 막힌 다단계 자막 수집 경로를, Python이 전용 Chrome 프로필을 조종해 자막과 게시일을 한 번에 받아 영상별 파일로 바로 쓰는 단일 경로로 바꾼다.

## 배경
현재 경로는 `youtube_scraper.py`(목록) → `create_pending_queue.py`(대기열 JSON) → 유저스크립트에 수동 업로드 → 브라우저 수집 → 결과 JSON 수동 다운로드 → `split_transcripts.py`(입력 경로 하드코딩) → `update_queue.py` → `fill_missing_dates.py`(영상별 날짜 스크래핑)다.
Python 직접 수집(`transcript_fetcher.py`, youtube-transcript-api + 쿠키)이 IP 차단을 맞아 브라우저 유저스크립트로 옮겨가면서 사람이 파일을 나르는 단계가 생겼다.
목록을 `extract_flat`으로 받아 `upload_date`가 비어 있으므로 대기열 단계의 5년 필터(`LIMIT_DATE`)는 사실상 동작하지 않는다.
2026-06-06에 받은 `data/transcripts_1137건.json`이 분할·대기열 반영되지 않은 채 남아 있는 것이 이 구조의 비용을 보여준다.

## 목표
- 사람이 파일을 옮기거나 스크립트를 순서대로 실행하는 단계를 없앤다.
- 자막과 게시일을 같은 페이지 방문에서 함께 얻어 날짜 보완 단계를 없앤다.
- 같은 수집기로 기존 백로그 마무리와 신규 영상 증분 수집을 모두 처리한다.
- 메인 구글 계정이 제한되지 않도록 보수적인 속도로 돈다.

## 비목표
- 수집 기간 확장(2021-05-14 이전) — 최신 영상만으로 양이 충분하다.
- YouTube Data API, 유료 프록시, Whisper 음성인식 — 허용 의존성 밖이다.
- 청킹·NLP 단계 변경 — 수집 출력 형식만 기존 `data/transcripts/<video_id>.json`과 호환되게 유지한다.
- 저장소를 SQLite로 옮기는 일 — 이번 병목이 아니다.

## 제약
- 브라우저 자동화만 허용된다.
- 메인 구글 계정 세션을 쓴다. 따라서 요청 간격과 일일 상한을 보수적으로 둔다.
- 출력은 기존 영상별 JSON 스키마(`video_id`, `title`, `upload_date`, `transcript`)를 유지해 하류가 깨지지 않게 한다.

## 성공 기준
- 명령 하나(`python -m collector.run`)로 신규 영상 감지부터 파일 저장까지 사람 개입 없이 끝난다.
- 새로 저장되는 모든 파일에 `upload_date`가 채워져 있다.
- 백로그 반영 후 `video_metadata.json` 대상 영상 중 자막 파일이 없는 것은 "자막 없음"으로 기록된 영상뿐이다.
- 429/403 등 차단 신호를 받으면 그날 실행을 즉시 멈추고 로그에 남기며, 다음 실행에서 이어간다.

## 검토한 접근

| 접근 | 장점 | 단점 | 판정 |
| --- | --- | --- | --- |
| A. Playwright 전용 프로필 | 수동 단계 전부 제거, 날짜 동시 획득, 실제 브라우저 세션이라 유저스크립트와 같은 요청 경로 | 유저스크립트 추출 로직을 이식해야 함, 자동화 브라우저 로그인 차단 가능성 | 선택 |
| B. 유저스크립트 + `userscript_server.py` | 기존 코드 재사용, 수정량 최소 | 사람이 브라우저를 열어둬야 해 자동 증분 불가 | 버림 — 자동 증분 요구를 충족하지 못함 |
| C. yt-dlp `--cookies-from-browser` | 가장 단순 | 과거 차단된 것과 같은 비브라우저 요청 경로, Windows Chrome 쿠키 암호화로 추출 불안정 | 버림 — 차단 문제를 풀지 못함 |

## 선택한 접근과 근거
A를 택한다. 차단을 피해 온 유일한 경로가 "로그인된 실제 브라우저 안에서의 요청"이었으므로 그 경로는 유지하고, 사람이 하던 운반만 Playwright로 대체하는 것이 가장 작은 변화로 두 병목을 함께 없앤다.

구성 요소:
- **신규 감지**: 채널 RSS `https://www.youtube.com/feeds/videos.xml?channel_id=<id>`. 키·로그인 불필요, 최신 15건과 게시일 제공. 일 1회 실행이면 누락이 없다.
- **추출**: 유저스크립트의 3단 폴백을 이식한다 — ① `ytInitialData`의 transcript 패널 파라미터로 innertube `get_transcript` 직접 호출, ② 자막 패널 DOM 스크래핑, ③ `ytInitialPlayerResponse.captions`의 `baseUrl` 호출. 게시일은 `ytInitialPlayerResponse.microformat.playerMicroformatRenderer.publishDate`에서 읽는다.
- **상태**: 대기열 JSON을 없애고 "파일이 있으면 완료"로 판단한다. 자막이 없는 영상은 `data/transcripts_missing.json`에 사유와 함께 기록해 재시도를 막는다.
- **속도·안전**: 영상 간 20~30초 무작위 대기, 일일 상한(초기값 150건), 차단 신호 시 즉시 종료.
- **실행**: Windows 작업 스케줄러로 일 1회. PC가 켜져 있다는 가정.

## 미결정
- 구글이 자동화 브라우저 로그인을 막는 경우의 대응. `channel="chrome"` + `launch_persistent_context`로 한 번 수동 로그인해 보고, 막히면 평소 Chrome 프로필 사본을 쓰는 방안을 T2에서 판단한다.
- 일일 상한 150건의 적정성. 첫 백로그 실행 결과(차단 여부)를 보고 조정한다.
- 서브 채널 `@moneymoneycomics`의 증분 수집 포함 여부. 현재 목록 수집 대상에는 들어 있으므로 기본은 포함으로 둔다.

## 작업 목록

- [ ] T1. 백로그 반영 — `data/transcripts_1137건.json`을 영상별 파일로 쓰는 1회성 import(기존 파일 덮어쓰지 않음) — 산출물: `data/transcripts/` 파일 수 증가, 반영 건수 로그 — 선행: 없음
- [ ] T2. Playwright 설치와 전용 프로필 로그인 확인 — 산출물: `data/.browser_profile/`(gitignore 대상)에 로그인된 세션, 로그인 차단 여부 판정 — 선행: 없음
- [ ] T3. 단일 영상 추출기 작성 — `collector/browser_fetcher.py`에 `fetch(video_id) -> dict` (3단 폴백 + 게시일) — 산출물: 샘플 영상 3개로 기존 파일과 같은 스키마 JSON 생성 — 선행: T2
- [ ] T4. 채널 RSS 감지기 작성 — 두 채널 `channel_id` 확인 후 최신 영상 목록 반환 — 산출물: `collector/feed.py`, 목록 출력 확인 — 선행: 없음
- [ ] T5. 러너 작성 — RSS 결과와 `video_metadata.json` 중 파일 없는 영상을 대상으로 T3 호출, 대기·상한·차단 시 종료, 자막 없음 기록 — 산출물: `collector/run.py` — 선행: T3, T4
- [ ] T6. 기존 날짜 결측 보완 — 러너가 `upload_date` 빈 기존 파일도 게시일만 채우도록 옵션 추가 — 산출물: 날짜 빈 파일 0건 — 선행: T5
- [ ] T7. 작업 스케줄러 등록 — 일 1회 실행, 로그 파일 경로 지정 — 산출물: 등록된 작업과 첫 자동 실행 로그 — 선행: T5
- [ ] T8. 구 경로 정리 — `split_transcripts.py`, `update_queue.py`, `fill_missing_dates.py`, `create_pending_queue.py`, `userscript_server.py`, 쿠키 문서를 `legacy/`로 이동하고 README 갱신 — 산출물: 수집 경로 설명이 새 구조와 일치 — 선행: T7
