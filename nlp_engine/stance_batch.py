"""T6: 후보 조각의 관련성·자산군·입장을 Gemini Batch API 로 판정한다.

흐름 (실행마다 data/t6/<tag>/ 에 산출물이 모인다)
  build   대상 조각을 골라 requests.jsonl 을 만든다 (key = video_id:chunk_idx)
  submit  requests.jsonl 을 업로드하고 배치 작업을 제출한다 → job.json
  status  배치 작업 상태를 본다
  fetch   결과 파일을 받아 results.jsonl(원본)과 parsed.csv(표)를 만들고 비용을 계산한다

대상 집합
  --set pilot   검수 표본 100조각(review/sample.json). 유사도와 무관하게 전부 넣어 T7 판정과 비교한다.
  --set full    2021-05-01 ~ 2026-05-13 영상 중 sim1 >= 0.20 조각 (T7 결정)

각 요청은 조각 하나다. 입력: 영상 제목, 조각 원문, 임베딩 1·2순위 자산군과 그 설명문.
출력(JSON 스키마 강제): relevant, asset_id(1순위|2순위|NONE), stance(-1|0|1), quote, confidence, reason.

    python -m nlp_engine.stance_batch build  --tag pilot-lite --set pilot --model gemini-3.1-flash-lite
    python -m nlp_engine.stance_batch submit --tag pilot-lite
    python -m nlp_engine.stance_batch status --tag pilot-lite
    python -m nlp_engine.stance_batch fetch  --tag pilot-lite
"""
import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
T6_DIR = BASE_DIR / "data" / "t6"
P24_PATH = BASE_DIR / "strategy" / "p24.yaml"
ASSIGN_PATH = BASE_DIR / "data" / "assignments.csv"
EMBED_DIR = BASE_DIR / "data" / "embeddings"
TRANSCRIPT_DIR = BASE_DIR / "data" / "transcripts"
REVIEW_SAMPLE = BASE_DIR / "review" / "sample.json"

WINDOW = ("20210501", "20260513")
SIM_MIN = 0.20
# 100만 토큰당 (입력, 출력) 달러. Batch API 는 이 값의 절반. 2026-09-22 공식 요금표 기준.
PRICES = {
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.1-pro-preview": (2.00, 12.00),
}

SYSTEM = """당신은 한국 경제 유튜브 '슈카월드' 방송 자막을 분석하는 금융 리서처입니다.
주어진 자막 조각이 후보 자산군에 대해 '투자 관점의 내용'을 담고 있는지, 담고 있다면 진행자 슈카의 입장이 무엇인지 판정합니다.

판정 규칙
1. relevant: 조각이 후보 자산군(산업·원자재)의 업황, 기업 실적, 가격, 수요·공급, 규제처럼 그 자산의 가치에 영향을 주는 내용을 다루면 true.
   방송 진행 멘트, 잡담, 역사·과학·사회·정치 일반, 코인, 국내외 증시 전반 잡담, 금리·환율 같은 거시 일반은 false.
   후보 자산군 이름이 우연히 스쳐 지나가기만 하면 false.
2. asset_id: relevant 가 true 면 두 후보 중 더 맞는 쪽. 둘 다 아니면 relevant=false, asset_id="NONE".
3. stance: 그 자산군의 앞날(가격·업황·실적 전망)에 대한 슈카의 입장.
   +1 = 좋아질 것/유망/수혜, -1 = 나빠질 것/위험/피해, 0 = 사실 전달·양면 소개·판단 없음.
   이야기의 분위기(웃긴지, 안타까운지)가 아니라 그 자산에 대한 방향성으로 판단한다. relevant=false 면 0.
4. quote: 판단 근거가 된 문장을 자막에서 그대로 옮긴다(80자 이내, 고치지 말 것). relevant=false 면 빈 문자열.
5. confidence: 0~1. reason: 한 문장."""


def load_assets() -> dict:
    spec = yaml.safe_load(P24_PATH.read_text(encoding="utf-8"))
    return {a["id"]: a for a in spec["assets"]}


def load_assignments() -> dict:
    with ASSIGN_PATH.open(encoding="utf-8") as fh:
        return {f"{r['video_id']}:{r['chunk_idx']}": r for r in csv.DictReader(fh)}


def video_meta(video_id: str, cache: dict) -> dict:
    if video_id not in cache:
        cache[video_id] = json.loads((TRANSCRIPT_DIR / f"{video_id}.json").read_text(encoding="utf-8"))
    return cache[video_id]


def chunk_text(video_id: str, idx: int, cache: dict) -> str:
    if video_id not in cache:
        cache[video_id] = np.load(EMBED_DIR / f"{video_id}.npz", allow_pickle=True)["chunks"]
    return str(cache[video_id][idx])


def select_keys(which: str, assign: dict) -> list[str]:
    if which == "pilot":
        return [it["id"] for it in json.loads(REVIEW_SAMPLE.read_text(encoding="utf-8"))["items"]]
    meta: dict = {}
    keys = []
    for key, r in assign.items():
        if float(r["sim1"]) < SIM_MIN:
            continue
        v = video_meta(r["video_id"], meta)
        date = v.get("upload_date", "")
        if not (WINDOW[0] <= date <= WINDOW[1]):
            continue
        t = v.get("transcript", "").strip()
        if not t or t == "자막 없음":
            continue
        keys.append(key)
    return keys


def schema(top1: str, top2: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "relevant": {"type": "boolean"},
            "asset_id": {"type": "string", "enum": [top1, top2, "NONE"]},
            "stance": {"type": "integer", "enum": [-1, 0, 1]},
            "quote": {"type": "string"},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["relevant", "asset_id", "stance", "quote", "confidence", "reason"],
        "propertyOrdering": ["relevant", "asset_id", "stance", "quote", "confidence", "reason"],
    }


def build_request(key: str, r: dict, assets: dict, meta: dict, chunks: dict, thinking: str) -> dict:
    vid, idx = key.rsplit(":", 1)
    t1, t2 = r["top1"], r["top2"]
    v = video_meta(vid, meta)
    prompt = (
        f"[영상 제목] {v.get('title', '')}\n"
        f"[게시일] {v.get('upload_date', '')}\n\n"
        f"[후보 자산군 1] {t1} = {assets[t1]['name']}: {assets[t1]['description']}\n"
        f"[후보 자산군 2] {t2} = {assets[t2]['name']}: {assets[t2]['description']}\n\n"
        f"[자막 조각]\n{chunk_text(vid, int(idx), chunks)}"
    )
    gen = {"responseMimeType": "application/json", "responseJsonSchema": schema(t1, t2), "temperature": 0}
    if thinking != "none":
        gen["thinkingConfig"] = {"thinkingLevel": thinking}
    return {"key": key, "request": {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": gen,
    }}


def run_dir(tag: str) -> Path:
    d = T6_DIR / tag
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_build(args) -> None:
    assets, assign = load_assets(), load_assignments()
    keys = select_keys(args.set, assign)
    if args.limit:
        keys = keys[:args.limit]
    d = run_dir(args.tag)
    meta: dict = {}
    chunks: dict = {}
    with (d / "requests.jsonl").open("w", encoding="utf-8") as fh:
        for k in keys:
            fh.write(json.dumps(build_request(k, assign[k], assets, meta, chunks, args.thinking), ensure_ascii=False) + "\n")
    (d / "config.json").write_text(json.dumps({"model": args.model, "set": args.set, "thinking": args.thinking,
                                               "n": len(keys), "sim_min": SIM_MIN, "window": WINDOW},
                                              ensure_ascii=False, indent=2), encoding="utf-8")
    size = (d / "requests.jsonl").stat().st_size
    print({"tag": args.tag, "requests": len(keys), "bytes": size, "model": args.model})


def client():
    from google import genai
    load_dotenv(BASE_DIR / ".env")
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def cmd_submit(args) -> None:
    d = run_dir(args.tag)
    cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
    if (d / "job.json").exists() and not args.force:
        sys.exit(f"이미 제출됨: {json.loads((d / 'job.json').read_text())['name']} (다시 내려면 --force)")
    c = client()
    up = c.files.upload(file=str(d / "requests.jsonl"),
                        config={"display_name": f"suka-t6-{args.tag}", "mime_type": "jsonl"})
    job = c.batches.create(model=cfg["model"], src=up.name, config={"display_name": f"suka-t6-{args.tag}"})
    (d / "job.json").write_text(json.dumps({"name": job.name, "file": up.name, "model": cfg["model"]},
                                           indent=2), encoding="utf-8")
    print({"job": job.name, "state": str(job.state), "file": up.name})


def cmd_status(args) -> None:
    d = run_dir(args.tag)
    job = client().batches.get(name=json.loads((d / "job.json").read_text())["name"])
    print({"job": job.name, "state": str(job.state), "stats": str(job.completion_stats), "error": str(job.error)})


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def cmd_fetch(args) -> None:
    d = run_dir(args.tag)
    cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
    c = client()
    job = c.batches.get(name=json.loads((d / "job.json").read_text())["name"])
    if "SUCCEEDED" not in str(job.state):
        sys.exit(f"아직 끝나지 않음: {job.state}")
    raw = c.files.download(file=job.dest.file_name)
    (d / "results.jsonl").write_bytes(raw)

    assign, meta, chunks = load_assignments(), {}, {}
    rows, tok_in, tok_out, errors = [], 0, 0, 0
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        key = rec.get("key", "")
        vid, idx = key.rsplit(":", 1)
        row = {"key": key, "video_id": vid, "chunk_idx": int(idx),
               "date": video_meta(vid, meta).get("upload_date", ""),
               "top1": assign[key]["top1"], "top2": assign[key]["top2"], "sim1": assign[key]["sim1"]}
        resp = rec.get("response") or {}
        usage = resp.get("usageMetadata", {})
        tok_in += usage.get("promptTokenCount", 0)
        tok_out += usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
        try:
            text = resp["candidates"][0]["content"]["parts"][0]["text"]
            out = json.loads(text)
            row.update({k: out.get(k) for k in ("relevant", "asset_id", "stance", "quote", "confidence", "reason")})
            row["quote_ok"] = bool(out.get("quote")) and norm(out["quote"]) in norm(chunk_text(vid, int(idx), chunks))
            row["error"] = ""
        except Exception as e:  # 응답이 없거나 JSON 이 깨진 요청은 표에 남기고 재제출 대상으로 삼는다
            errors += 1
            row["error"] = str(rec.get("error") or e)[:300]
        row["thought_tokens"] = usage.get("thoughtsTokenCount", 0)
        rows.append(row)

    cols = ["key", "video_id", "chunk_idx", "date", "top1", "top2", "sim1", "relevant", "asset_id", "stance",
            "confidence", "quote", "quote_ok", "reason", "thought_tokens", "error"]
    with (d / "parsed.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    pin, pout = PRICES.get(cfg["model"], (float("nan"), float("nan")))
    cost = (tok_in * pin + tok_out * pout) / 1e6 / 2  # Batch API 50%
    print({"rows": len(rows), "errors": errors, "tokens_in": tok_in, "tokens_out_incl_thoughts": tok_out,
           "batch_cost_usd": round(cost, 4), "out": str(d / "parsed.csv")})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "submit", "status", "fetch"])
    ap.add_argument("--tag", required=True)
    ap.add_argument("--set", choices=["pilot", "full"], default="pilot")
    ap.add_argument("--model", default="gemini-3.1-flash-lite")
    ap.add_argument("--thinking", default="minimal", help="thinkingLevel(minimal|low|...) 또는 none")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    {"build": cmd_build, "submit": cmd_submit, "status": cmd_status, "fetch": cmd_fetch}[args.cmd](args)


if __name__ == "__main__":
    main()
