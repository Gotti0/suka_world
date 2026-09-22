// ==UserScript==
// @name         YouTube Transcript Scraper (Browser Standalone)
// @namespace    http://tampermonkey.net/
// @version      2.0
// @description  A fully browser-based YouTube transcript scraper with a premium UI and local storage.
// @author       suka_world
// @match        https://www.youtube.com/*
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_deleteValue
// ==/UserScript==

(function () {
    'use strict';

    // 저장소 키 모음
    const STATE_KEYS = {
        QUEUE: 'yts_queue',
        RESULTS: 'yts_results',
        IS_RUNNING: 'yts_is_running',
        RESUME_AT: 'yts_resume_at'
    };

    // 헬퍼: 대기 함수
    const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

    // 상태 관리 래퍼 (에러 방지 추가)
    const getState = (key, defaultValue) => {
        try {
            const val = GM_getValue(key);
            if (val === undefined || val === null || val === '') return defaultValue;
            return JSON.parse(val);
        } catch (e) {
            console.error(`[Scraper] 로컬 스토리지 파싱 에러 (${key}):`, e);
            return defaultValue;
        }
    };
    const setState = (key, value) => {
        GM_setValue(key, JSON.stringify(value));
    };

    // UI 생성 (프리미엄 글래스모피즘 디자인)
    function createUI() {
        if (document.getElementById('yts-container')) return; // 중복 생성 방지

        const container = document.createElement('div');
        container.id = 'yts-container';
        container.style.cssText = `
            position: fixed;
            bottom: 30px;
            right: 30px;
            width: 340px;
            background: rgba(24, 24, 27, 0.75);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            color: #f4f4f5;
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 20px;
            padding: 24px;
            z-index: 9999999;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.1);
            display: flex;
            flex-direction: column;
            gap: 16px;
            transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
        `;

        // 헤더
        const header = document.createElement('div');
        header.style.display = 'flex';
        header.style.justifyContent = 'space-between';
        header.style.alignItems = 'center';

        const title = document.createElement('h3');
        title.innerText = 'YouTube Scraper';
        title.style.margin = '0';
        title.style.fontSize = '18px';
        title.style.fontWeight = '700';
        title.style.letterSpacing = '-0.02em';
        title.style.background = 'linear-gradient(135deg, #38bdf8, #818cf8)';
        title.style.webkitBackgroundClip = 'text';
        title.style.webkitTextFillColor = 'transparent';

        const minimizeBtn = document.createElement('button');
        minimizeBtn.textContent = '▼'; // 아래 화살표
        minimizeBtn.style.cssText = `
            background: transparent; border: none; color: #a1a1aa; cursor: pointer; 
            font-size: 12px; padding: 4px; border-radius: 50%; transition: 0.2s;
        `;
        minimizeBtn.onmouseover = () => minimizeBtn.style.color = '#fff';
        minimizeBtn.onmouseout = () => minimizeBtn.style.color = '#a1a1aa';

        let isMinimized = false;
        const contentDiv = document.createElement('div');
        contentDiv.style.display = 'flex';
        contentDiv.style.flexDirection = 'column';
        contentDiv.style.gap = '16px';
        contentDiv.style.transition = 'all 0.3s';

        minimizeBtn.addEventListener('click', () => {
            isMinimized = !isMinimized;
            contentDiv.style.display = isMinimized ? 'none' : 'flex';
            minimizeBtn.style.transform = isMinimized ? 'rotate(180deg)' : 'rotate(0deg)';
        });

        header.append(title, minimizeBtn);

        // 상태 표시
        const statusBox = document.createElement('div');
        statusBox.style.cssText = `
            background: rgba(0, 0, 0, 0.4);
            border-radius: 12px;
            padding: 12px;
            font-size: 13px;
            color: #d4d4d8;
            line-height: 1.5;
            border: 1px solid rgba(255,255,255,0.05);
        `;
        statusBox.id = 'yts-status-box';

        const qLabel = document.createTextNode('대기: ');
        const qCnt = document.createElement('strong');
        qCnt.id = 'yts-q-cnt';
        qCnt.textContent = '0';

        const rLabel = document.createTextNode(' | 완료: ');
        const rCnt = document.createElement('strong');
        rCnt.id = 'yts-r-cnt';
        rCnt.textContent = '0';

        const br = document.createElement('br');

        const sLabel = document.createTextNode('상태: ');
        const sState = document.createElement('span');
        sState.id = 'yts-state';
        sState.style.color = '#fbbf24';
        sState.textContent = '준비됨';

        statusBox.append(qLabel, qCnt, rLabel, rCnt, br, sLabel, sState);

        // 파일 업로드
        const fileWrap = document.createElement('div');
        fileWrap.style.cssText = `
            position: relative; overflow: hidden; display: inline-block;
            background: rgba(255,255,255,0.05); border: 1px dashed rgba(255,255,255,0.2);
            border-radius: 12px; padding: 12px; text-align: center; cursor: pointer; transition: 0.2s;
        `;
        fileWrap.onmouseover = () => fileWrap.style.background = 'rgba(255,255,255,0.1)';
        fileWrap.onmouseout = () => fileWrap.style.background = 'rgba(255,255,255,0.05)';

        const fileLabel = document.createElement('span');
        fileLabel.innerText = '📁 video_metadata.json 업로드';
        fileLabel.style.fontSize = '13px';
        fileLabel.style.fontWeight = '500';

        const fileInput = document.createElement('input');
        fileInput.type = 'file';
        fileInput.accept = '.json';
        fileInput.style.cssText = `
            position: absolute; left: 0; top: 0; opacity: 0; width: 100%; height: 100%; cursor: pointer;
        `;
        fileWrap.append(fileLabel, fileInput);

        // 버튼 공통 스타일
        const btnStyle = (bg, hoverBg) => `
            flex: 1; padding: 10px 0; border: none; border-radius: 10px; color: white; 
            font-size: 13px; font-weight: 600; cursor: pointer; transition: all 0.2s;
            background: ${bg}; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), inset 0 1px 0 rgba(255,255,255,0.1);
        `;

        // 실행 버튼 영역
        const btnRow1 = document.createElement('div');
        btnRow1.style.cssText = `display: flex; gap: 10px;`;

        const btnStart = document.createElement('button');
        btnStart.innerText = '▶ 시작/재개';
        btnStart.style.cssText = btnStyle('#10b981', '#059669');
        btnStart.onmouseover = () => btnStart.style.background = '#059669';
        btnStart.onmouseout = () => btnStart.style.background = '#10b981';

        const btnStop = document.createElement('button');
        btnStop.innerText = '⏸ 일시정지';
        btnStop.style.cssText = btnStyle('#f59e0b', '#d97706');
        btnStop.onmouseover = () => btnStop.style.background = '#d97706';
        btnStop.onmouseout = () => btnStop.style.background = '#f59e0b';

        btnRow1.append(btnStart, btnStop);

        // 다운로드/초기화 버튼 영역
        const btnRow2 = document.createElement('div');
        btnRow2.style.cssText = `display: flex; gap: 10px;`;

        const btnDownload = document.createElement('button');
        btnDownload.innerText = '💾 결과 다운로드';
        btnDownload.style.cssText = btnStyle('#3b82f6', '#2563eb');
        btnDownload.onmouseover = () => btnDownload.style.background = '#2563eb';
        btnDownload.onmouseout = () => btnDownload.style.background = '#3b82f6';

        const btnClear = document.createElement('button');
        btnClear.innerText = '🗑 초기화';
        btnClear.style.cssText = btnStyle('#ef4444', '#dc2626');
        btnClear.onmouseover = () => btnClear.style.background = '#dc2626';
        btnClear.onmouseout = () => btnClear.style.background = '#ef4444';

        btnRow2.append(btnDownload, btnClear);

        contentDiv.append(statusBox, fileWrap, btnRow1, btnRow2);
        container.append(header, contentDiv);
        document.body.appendChild(container);

        // 이벤트 리스너 등록
        fileInput.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = (event) => {
                try {
                    const data = JSON.parse(event.target.result);
                    if (!Array.isArray(data)) throw new Error("JSON 배열이 아닙니다.");

                    const existingResults = getState(STATE_KEYS.RESULTS, []);
                    const downloadedIds = new Set(existingResults.map(r => r.video_id));

                    const queue = data.filter(item => {
                        const limitDate = "20210514";
                        if (item.upload_date && item.upload_date < limitDate) return false;
                        if (downloadedIds.has(item.video_id)) return false;
                        return true;
                    });

                    setState(STATE_KEYS.QUEUE, queue);
                    updateStateText(`파일 업로드 완료 (${queue.length}개)`, '#10b981');
                    updateCounts();
                } catch (err) {
                    alert("파일 파싱 에러: " + err.message);
                }
            };
            reader.readAsText(file);
        });

        btnStart.addEventListener('click', () => {
            const queue = getState(STATE_KEYS.QUEUE, []);
            if (queue.length === 0) {
                alert("대기 중인 큐가 없습니다. 파일을 업로드해 주세요.");
                return;
            }
            setState(STATE_KEYS.IS_RUNNING, true);
            setState(STATE_KEYS.RESUME_AT, 0); // 대기 시간 초기화
            updateStateText('수집 시작 중...', '#3b82f6');
            startScrapingLoop();
        });

        btnStop.addEventListener('click', () => {
            setState(STATE_KEYS.IS_RUNNING, false);
            updateStateText('일시 정지됨', '#f59e0b');
        });

        btnDownload.addEventListener('click', () => {
            const results = getState(STATE_KEYS.RESULTS, []);
            if (results.length === 0) {
                alert("다운로드할 결과가 없습니다.");
                return;
            }
            const blob = new Blob([JSON.stringify(results, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `transcripts_${results.length}건.json`;
            a.click();
            URL.revokeObjectURL(url);
        });

        btnClear.addEventListener('click', () => {
            if (confirm("모든 큐와 수집된 결과를 삭제하시겠습니까? (다운로드하지 않은 데이터는 영구 삭제됩니다)")) {
                setState(STATE_KEYS.QUEUE, []);
                setState(STATE_KEYS.RESULTS, []);
                setState(STATE_KEYS.IS_RUNNING, false);
                setState(STATE_KEYS.RESUME_AT, 0);
                updateCounts();
                updateStateText('초기화됨', '#ef4444');
            }
        });

        // UI 실시간 업데이트 루프
        setInterval(updateUIStatusLoop, 1000);
    }

    function updateStateText(text, color = '#f4f4f5') {
        const el = document.getElementById('yts-state');
        if (el) {
            el.innerText = text;
            el.style.color = color;
        }
    }

    function updateCounts() {
        const queue = getState(STATE_KEYS.QUEUE, []);
        const results = getState(STATE_KEYS.RESULTS, []);
        const qEl = document.getElementById('yts-q-cnt');
        const rEl = document.getElementById('yts-r-cnt');
        if (qEl) qEl.innerText = queue.length.toString();
        if (rEl) rEl.innerText = results.length.toString();
    }

    function updateUIStatusLoop() {
        updateCounts();
        const isRunning = getState(STATE_KEYS.IS_RUNNING, false);
        const resumeAt = getState(STATE_KEYS.RESUME_AT, 0);

        if (resumeAt > Date.now()) {
            const leftMin = Math.ceil((resumeAt - Date.now()) / 60000);
            updateStateText(`IP 차단 우회 대기 중 (${leftMin}분 남음)`, '#ef4444');
        } else if (isRunning) {
            const currentVideoIdOnPage = new URLSearchParams(window.location.search).get("v");
            const queue = getState(STATE_KEYS.QUEUE, []);
            if (queue.length > 0 && queue[0].video_id === currentVideoIdOnPage) {
                // Do nothing, extract logic updates the text
            } else {
                updateStateText('수집 이동 중...', '#10b981');
            }
        }
    }

    // --- Core Logic ---

    function parseTranscriptXml(xmlText) {
        const parser = new DOMParser();
        const xmlDoc = parser.parseFromString(xmlText, "text/xml");
        const texts = xmlDoc.getElementsByTagName("text");
        let transcript = [];
        for (let i = 0; i < texts.length; i++) {
            transcript.push(texts[i].textContent.replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&'));
        }
        return transcript.join(" ");
    }

    let isLoopActive = false;

    async function startScrapingLoop() {
        if (isLoopActive) {
            console.log("[Scraper] ⛔ 이미 실행 중인 스크래핑 루프가 존재합니다. 중복 실행을 차단합니다.");
            return;
        }
        isLoopActive = true;
        
        if (!getState(STATE_KEYS.IS_RUNNING, false)) {
            isLoopActive = false;
            return;
        }

        const resumeAt = getState(STATE_KEYS.RESUME_AT, 0);
        if (resumeAt > Date.now()) {
            const minutesLeft = Math.ceil((resumeAt - Date.now()) / 60000);
            console.log(`[Scraper] ⏳ (이전 차단 이력 감지) IP 차단 우회 대기 중이었습니다. 남은 시간: ${minutesLeft}분`);
            console.log(`[Scraper] 🚀 하지만 새로운 내부 API 방식을 테스트하기 위해 대기를 강제 무시하고 즉시 추출을 시도합니다!`);
            setState(STATE_KEYS.RESUME_AT, 0); // 상태 초기화
        }

        const queue = getState(STATE_KEYS.QUEUE, []);
        if (queue.length === 0) {
            setState(STATE_KEYS.IS_RUNNING, false);
            updateStateText('🎉 모든 수집 완료!', '#8b5cf6');
            isLoopActive = false;
            return;
        }

        const currentJob = queue[0];
        const currentVideoIdOnPage = new URLSearchParams(window.location.search).get("v");

        if (currentVideoIdOnPage !== currentJob.video_id) {
            updateStateText(`다음 영상으로 이동 중...`, '#3b82f6');
            window.location.href = `https://www.youtube.com/watch?v=${currentJob.video_id}`;
            return; // 페이지가 리로드되면서 다시 실행됨
        }

        // 현재 타겟 페이지에 도착함
        updateStateText(`자막 추출 중...`, '#f59e0b');

        try {
            const win = typeof unsafeWindow !== 'undefined' ? unsafeWindow : window;
            let transcriptText = "";

            // 1. 영상 재생을 일시정지 (배경음 방지)
            const video = document.querySelector('video');
            if (video && !video.paused) video.pause();

            // 2. 설명란 "더보기" 클릭
            const expandBtn = document.querySelector('tp-yt-paper-button#expand');
            if (expandBtn) {
                expandBtn.click();
                await sleep(500);
            }

            // 3. 자막 "스크립트 표시" 버튼 찾기 및 클릭
            updateStateText(`[DOM] 패널 열기 시도...`, '#f59e0b');
            console.log("[Scraper-DOM] '스크립트 표시' 버튼 검색 시작...");
            const transcriptSectionBtn = document.querySelector('ytd-video-description-transcript-section-renderer button');

            if (transcriptSectionBtn) {
                console.log("[Scraper-DOM] ytd-video-description-transcript-section-renderer 내부 버튼 발견. 클릭합니다.");
                transcriptSectionBtn.scrollIntoView({ block: 'center', behavior: 'instant' });
                transcriptSectionBtn.click();
                await sleep(1500); // 네트워크를 타고 자막 리스트가 렌더링될 시간 대기
            } else {
                console.log("[Scraper-DOM] 전용 렌더러 버튼을 찾을 수 없습니다. 전체 버튼 중 텍스트로 탐색합니다.");
                // 설명란 외부의 일반 버튼 중에서도 탐색
                const buttons = Array.from(document.querySelectorAll('button'));
                const tBtn = buttons.find(b => {
                    const text = b.textContent || b.innerText || "";
                    return text.includes('스크립트 표시') || text.includes('Show transcript');
                });

                if (tBtn) {
                    console.log("[Scraper-DOM] 텍스트 기반 버튼 발견. 클릭합니다.", tBtn);
                    tBtn.scrollIntoView({ block: 'center', behavior: 'instant' });
                    tBtn.click();
                    await sleep(1500);
                } else {
                    console.error("[Scraper-DOM] 🚨 '스크립트 표시' 버튼을 DOM에서 전혀 찾을 수 없습니다! 영상에 자막이 없거나 DOM 로딩이 완료되지 않았을 수 있습니다.");
                }
            }

            // 4. (NEW) 최우선: 내부 API를 통한 자막 직접 추출 (DOM 의존성 완화 및 가장 빠름)
            updateStateText(`[API] 내부 통신망 호출 시도...`, '#f59e0b');
            try {
                let retries = 5;
                while (!win.ytInitialData && retries > 0) {
                    await sleep(500);
                    retries--;
                }

                const panels = win.ytInitialData?.engagementPanels || [];
                const transcriptPanel = panels.find(p => p.engagementPanelSectionListRenderer?.targetId === 'engagement-panel-searchable-transcript');

                // 구조 변경 방어용 재귀 파라미터 검색
                function findParams(obj) {
                    if (!obj || typeof obj !== 'object') return null;
                    if (obj.getTranscriptEndpoint && obj.getTranscriptEndpoint.params) return obj.getTranscriptEndpoint.params;
                    for (const key of Object.keys(obj)) {
                        const res = findParams(obj[key]);
                        if (res) return res;
                    }
                    return null;
                }

                const params = findParams(transcriptPanel);

                if (params && win.yt && win.yt.config_) {
                    console.log("[Scraper-API] 내부 get_transcript API 파라미터 확보, 직접 호출 시도...");
                    const INNERTUBE_API_KEY = win.yt.config_.INNERTUBE_API_KEY;
                    const INNERTUBE_CONTEXT = win.yt.config_.INNERTUBE_CONTEXT;

                    const response = await fetch(`https://www.youtube.com/youtubei/v1/get_transcript?key=${INNERTUBE_API_KEY}&prettyPrint=false`, {
                        method: 'POST',
                        credentials: 'omit', // 봇 탐지를 우회하기 위해 쿠키 제외 시도
                        body: JSON.stringify({
                            context: INNERTUBE_CONTEXT,
                            params: params,
                            videoId: currentVideoIdOnPage || new URLSearchParams(window.location.search).get("v")
                        }),
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Origin': 'https://www.youtube.com'
                        }
                    });

                    if (response.ok) {
                        const data = await response.json();

                        function findInitialSegments(obj) {
                            if (!obj || typeof obj !== 'object') return null;
                            if (obj.initialSegments && Array.isArray(obj.initialSegments)) return obj.initialSegments;
                            for (const key of Object.keys(obj)) {
                                const res = findInitialSegments(obj[key]);
                                if (res) return res;
                            }
                            return null;
                        }

                        const segments = findInitialSegments(data);

                        if (segments && segments.length > 0) {
                            transcriptText = segments.map(s => {
                                const runs = s.transcriptSegmentRenderer?.snippet?.runs || [];
                                return runs.map(r => r.text).join("");
                            }).filter(t => t).join(" ");
                            console.log(`[Scraper-API] 내부 API 호출 성공! 텍스트 길이: ${transcriptText.length}`);
                        } else {
                            console.log("[Scraper-API] API 호출은 성공했으나 자막 조각(initialSegments)을 찾을 수 없습니다.");
                        }
                    } else {
                        console.error(`[Scraper-API] 내부 API 호출 실패 (HTTP ${response.status})`);
                    }
                } else {
                    console.log("[Scraper-API] ytInitialData에서 파라미터를 추출할 수 없습니다.");
                }
            } catch (err) {
                console.error("[Scraper-API] 내부 API 추출 중 에러:", err);
            }

            // 내부 API가 실패했을 경우에만 기존 DOM 스크롤 방식 실행
            if (!transcriptText) {
                // 5. 자막 렌더러 대기 및 가상 스크롤 우회 텍스트 수집 (신/구버전 완벽 대응)
                console.log("[Scraper-DOM] 자막 패널 및 렌더링 대기 시작...");
                let waitDOM = 15;
                let panelContent = null;
                let foundSegmentsCount = 0;

                while (waitDOM > 0) {
                    // 패널 스크롤 컨테이너 대기
                    // 먼저 페이지 전체에서 자막 조각을 찾습니다 (유튜브 구조 변경 대응)
                    const globalSegments = document.querySelectorAll('transcript-segment-view-model, ytd-transcript-segment-renderer, ytw-transcript-segment-view-model');
                    foundSegmentsCount = globalSegments.length;

                    if (foundSegmentsCount > 0) {
                        // 자막 조각이 발견되면, 그 조각의 부모 중 스크롤이 가능해 보이는 컨테이너를 panelContent로 역추적합니다.
                        panelContent = globalSegments[0].closest('#content, #segments-container, ytd-transcript-segment-list-renderer, ytd-engagement-panel-section-list-renderer');
                        if (!panelContent) panelContent = globalSegments[0].parentElement; // fallback

                        console.log(`[Scraper-DOM] 자막 렌더링 확인됨! (개수: ${foundSegmentsCount}) 컨테이너 역추적 성공. 루프 탈출.`);
                        break;
                    } else {
                        console.log(`[Scraper-DOM] 패널 내부 자막 렌더링 대기중... 남은 시간: ${waitDOM}초`);
                        // 만약 자막이 안떴지만 껍데기는 있는지 확인용
                        panelContent = document.querySelector('ytd-engagement-panel-section-list-renderer[target-id="engagement-panel-searchable-transcript"] #content') ||
                            document.querySelector('ytd-transcript-search-panel-renderer #content');
                    }

                    await sleep(1000);
                    waitDOM--;
                }

                console.log(`[Scraper-DOM] 대기 종료. panelContent 존재: ${!!panelContent}, 렌더링된 자막 수: ${foundSegmentsCount}`);

                // 컨테이너는 발견되었으나 내부에 자막이 안 떴을 경우를 방지 (foundSegmentsCount > 0 조건 추가)
                if (panelContent && foundSegmentsCount > 0) {
                    updateStateText(`[DOM] 자막 가상 스크롤 스캔 중...`, '#f59e0b');

                    // 구버전 Polymer 데이터 추출 시도
                    const segmentList = document.querySelector('ytd-transcript-segment-list-renderer');
                    if (segmentList) {
                        try {
                            console.log("[Scraper-DOM] 구버전 Polymer 데이터 접근 시도...");
                            const polymerData = segmentList.data || segmentList.__data;
                            if (polymerData && polymerData.initialSegments) {
                                transcriptText = polymerData.initialSegments.map(s => {
                                    return s.transcriptSegmentRenderer.snippet.runs.map(r => r.text).join("");
                                }).join(" ");
                                console.log(`[Scraper-DOM] Polymer 데이터에서 추출 성공! 텍스트 길이: ${transcriptText.length}`);
                            }
                        } catch (e) {
                            console.log("[Scraper-DOM] Polymer 데이터 추출 중 에러:", e);
                        }
                    }

                    // Polymer 추출 실패 시, 직접 스크롤하며 DOM에서 수집 (가상 스크롤 잘림 문제 해결)
                    if (!transcriptText) {
                        console.log("[Scraper-DOM] DOM 스크롤 스캔 방식 시작...");
                        let collectedSegments = [];
                        let lastScrollHeight = 0;
                        let scrollAttempts = 0;
                        let unchangedCount = 0;

                        while (scrollAttempts < 200) {
                            const segments = panelContent.querySelectorAll('transcript-segment-view-model, ytd-transcript-segment-renderer');
                            console.log(`[Scraper-DOM] 스크롤 시도 ${scrollAttempts + 1} - 현재 화면에 보이는 자막 조각 수: ${segments.length}`);

                            let currentBatchCount = 0;
                            Array.from(segments).forEach(el => {
                                // 고유성을 식별할 타임스탬프 추출 (대소문자 무시 속성 추가 및 최신 클래스 반영)
                                const timeEl = el.querySelector('.ytwTranscriptSegmentViewModelTimestamp, .segment-timestamp, [class*="timestamp" i], [class*="Timestamp"]');
                                const timestamp = timeEl ? timeEl.textContent.trim() : "";

                                // 텍스트 추출 (제공된 ytAttributedStringHost 클래스 최우선 반영)
                                const textEl = el.querySelector('.ytAttributedStringHost, .yt-core-attributed-string--link-inherit-color, .yt-core-attributed-string, yt-formatted-string.ytd-transcript-segment-renderer, .segment-text');

                                let t = "";
                                if (textEl) {
                                    t = textEl.textContent.trim();
                                } else {
                                    // 하위 요소가 명확하지 않을 때 직접 내용 파싱
                                    t = el.textContent.trim();
                                }

                                // 내용에서 타임스탬프 영역 분리 (DOM 전체 텍스트 fallback 시 혼합 방지)
                                if (timestamp && t.startsWith(timestamp)) {
                                    t = t.substring(timestamp.length).trim();
                                    // 접근성용 "16초" 같은 텍스트가 남아있을 경우 제거
                                    t = t.replace(/^\d+초\s*/, '');
                                }

                                if (t.length > 0) {
                                    const key = timestamp + "_" + t;
                                    if (!collectedSegments.some(item => item.key === key)) {
                                        collectedSegments.push({ key: key, text: t });
                                        currentBatchCount++;
                                    }
                                }
                            });
                            console.log(`[Scraper-DOM] 새로 추가된 고유 자막 개수: ${currentBatchCount} (누적: ${collectedSegments.length})`);

                            panelContent.scrollTo(0, panelContent.scrollHeight);
                            await sleep(350); // 렌더링 지연 대기

                            if (panelContent.scrollHeight === lastScrollHeight) {
                                unchangedCount++;
                                console.log(`[Scraper-DOM] 스크롤 변화 없음 (${unchangedCount}/3)`);
                                if (unchangedCount >= 3) {
                                    console.log("[Scraper-DOM] 더 이상 스크롤이 내려가지 않으므로 스캔을 종료합니다.");
                                    break;
                                }
                            } else {
                                unchangedCount = 0;
                            }

                            lastScrollHeight = panelContent.scrollHeight;
                            scrollAttempts++;
                        }

                        if (collectedSegments.length > 0) {
                            transcriptText = collectedSegments.map(item => item.text).join(" ");
                            console.log(`[Scraper-DOM] 수집 완료! 총 텍스트 길이: ${transcriptText.length}`);
                        } else {
                            console.error("[Scraper-DOM] 자막 조각을 찾았으나 텍스트를 파싱하는데 실패했습니다!");
                        }
                    }
                }
            } // 486번째 줄 if (!transcriptText) { 의 진정한 닫는 괄호입니다.

            // 6. DOM 방식 실패 시 기존 fetch API 방식 Fallback
            if (!transcriptText) {
                updateStateText(`[API] DOM 실패, API 우회 시도...`, '#f59e0b');
                let retries = 10;
                while (!win.ytInitialPlayerResponse && retries > 0) {
                    await sleep(1000);
                    retries--;
                }

                if (win.ytInitialPlayerResponse && win.ytInitialPlayerResponse.captions) {
                    const captionTracks = win.ytInitialPlayerResponse.captions.playerCaptionsTracklistRenderer.captionTracks;
                    if (captionTracks && captionTracks.length > 0) {
                        let track = captionTracks.find(t => t.languageCode === 'ko' || t.languageCode === 'ko-KR') || captionTracks[0];
                        const response = await fetch(track.baseUrl);

                        if (response.status === 429 || response.status === 403) {
                            console.error("[Scraper] 🚨 API IP 차단 감지 (429/403). 30분 대기.");
                            setState(STATE_KEYS.RESUME_AT, Date.now() + 30 * 60 * 1000);
                            setTimeout(startScrapingLoop, 10000);
                            return;
                        }

                        if (response.ok) {
                            transcriptText = parseTranscriptXml(await response.text());
                        }
                    }
                }
            }

            if (!transcriptText) transcriptText = "자막 없음";

            // 결과 저장 (동시성 방지를 위해 최신 State 불러오기)
            const latestResults = getState(STATE_KEYS.RESULTS, []);
            // 중복 저장 방지
            if (!latestResults.some(r => r.video_id === currentJob.video_id)) {
                latestResults.push({
                    video_id: currentJob.video_id,
                    title: currentJob.title,
                    upload_date: currentJob.upload_date || "",
                    transcript: transcriptText
                });
                setState(STATE_KEYS.RESULTS, latestResults);
            }

            // 큐에서 제거 (최신 큐를 기준으로 검증 후 제거)
            const latestQueue = getState(STATE_KEYS.QUEUE, []);
            if (latestQueue.length > 0 && latestQueue[0].video_id === currentJob.video_id) {
                latestQueue.shift();
                setState(STATE_KEYS.QUEUE, latestQueue);
            }

            // UI 업데이트
            const total = getState(STATE_KEYS.TOTAL_COUNT, 0);
            updateQueueCount(latestQueue.length, total);
            updateResultCount(latestResults.length);

            // 딜레이 후 다음 영상으로
            const delay = Math.floor(Math.random() * 4000) + 4000; // 4~8초
            updateStateText(`✅ 성공! ${delay / 1000}초 대기 중...`, '#10b981');
            await sleep(delay);

            setTimeout(startScrapingLoop, 0);

        } catch (error) {
            console.error(`[Scraper] ❌ 에러 발생:`, error);
            updateStateText(`오류 발생. 5초 뒤 재시도...`, '#ef4444');
            await sleep(5000);
            setTimeout(startScrapingLoop, 0);
        } finally {
            isLoopActive = false;
        }
    }

    // 초기화 및 진입점
    function init() {
        if (!document.body) {
            setTimeout(init, 100);
            return;
        }

        console.log("[Scraper] UI 초기화 시작...");
        createUI();

        // 페이지 로드 후 2초 뒤 스크래퍼가 실행 중이었다면 재개
        setTimeout(() => {
            if (getState(STATE_KEYS.IS_RUNNING, false)) {
                console.log("[Scraper] 상태가 실행 중이므로 작업을 이어갑니다.");
                startScrapingLoop();
            }
        }, 2000);
    }

    // Tampermonkey 스크립트는 문서 로드 완료 후에 주입될 수 있으므로 즉시 실행
    init();

    // 혹시라도 너무 일찍 주입된 경우를 대비한 안전 장치 (createUI에서 중복 방지 처리됨)
    window.addEventListener('DOMContentLoaded', init);
    window.addEventListener('load', init);

})();
