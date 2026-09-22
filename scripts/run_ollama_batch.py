"""Run a prepared T6 request JSONL through an Ollama model.

The output is compatible with ``compile_subagent_run.py``. Each record keeps
the request key alongside the structured stance fields returned by the model.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def norm(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def prompt_parts(request: dict) -> tuple[str, str, dict]:
    system = request["systemInstruction"]["parts"][0]["text"]
    user = request["contents"][0]["parts"][0]["text"]
    schema = request["generationConfig"]["responseJsonSchema"]
    return system, user, schema


def post_json(url: str, body: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def parse_model_output(content: str) -> dict:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Some Ollama cloud models ignore `format` and return a Markdown key/value list.
    parsed: dict = {}
    for line in text.splitlines():
        match = re.match(r"^\s*(?:[-*]\s*)?([a-z_]+)\s*:\s*(.*)\s*$", line)
        if not match:
            continue
        key, value = match.groups()
        value = value.strip()
        if value.lower() in ("true", "false"):
            parsed[key] = value.lower() == "true"
        elif key == "stance":
            parsed[key] = int(value)
        elif key == "confidence":
            parsed[key] = float(value)
        else:
            parsed[key] = value.strip('"')
    required = {"relevant", "asset_id", "stance", "quote", "confidence", "reason"}
    if not required.issubset(parsed):
        raise ValueError(f"unparseable response: {text[:300]}")
    return parsed


def run_one(item: dict, args: argparse.Namespace) -> dict:
    system, user, schema = prompt_parts(item["request"])
    last_error: Exception | None = None
    repair_note = ""
    for attempt in range(1, args.retries + 1):
        try:
            body = {
                "model": args.model,
                "messages": [
                    {"role": "system", "content": system + "\n반드시 설명이나 마크다운 없이 JSON 객체 하나만 출력하세요."},
                    {"role": "user", "content": user + repair_note},
                ],
                "format": schema,
                "stream": False,
                "think": args.think,
                "options": {"temperature": 0},
            }
            response = post_json(args.url, body, args.timeout)
            result = parse_model_output(response["message"]["content"])
            result["key"] = item["key"]
            problems = validate(item, result)
            if problems:
                repair_note = (
                    "\n\n이전 응답에 다음 오류가 있었습니다: " + ", ".join(problems)
                    + ". 특히 quote는 위 자막 조각에 문자 그대로 존재하는 80자 이내 구절이어야 합니다. 다시 판정하세요."
                )
                raise ValueError("validation: " + ",".join(problems))
            result["_usage"] = {
                "prompt_tokens": response.get("prompt_eval_count", 0),
                "output_tokens": response.get("eval_count", 0),
            }
            return result
        except (OSError, KeyError, ValueError, json.JSONDecodeError, urllib.error.HTTPError) as exc:
            last_error = exc
            if attempt < args.retries:
                time.sleep(min(2 ** attempt, 10))
    raise RuntimeError(f"{item['key']}: {last_error}")


def validate(item: dict, result: dict) -> list[str]:
    problems = []
    user = item["request"]["contents"][0]["parts"][0]["text"]
    chunk_match = re.search(r"\[자막 조각\]\s*\n(.*)", user, re.DOTALL)
    chunk = chunk_match.group(1).strip() if chunk_match else ""
    allowed = set(re.findall(r"\[후보 자산군 [12]\]\s*([A-Z0-9_]+)", user))
    relevant = result.get("relevant") is True
    if relevant:
        if result.get("asset_id") not in allowed:
            problems.append("asset_id")
        quote = result.get("quote", "")
        if not quote or norm(quote) not in norm(chunk):
            problems.append("quote")
    elif result.get("asset_id") != "NONE" or result.get("stance") != 0 or result.get("quote"):
        problems.append("non_relevant_fields")
    if result.get("stance") not in (-1, 0, 1):
        problems.append("stance")
    confidence = result.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        problems.append("confidence")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model", default="glm-5.3-flash:cloud")
    parser.add_argument("--url", default="http://localhost:11434/api/chat")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--think", default="low")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()

    items = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_key = {item["key"]: item for item in items}
    checkpoint = args.output.with_suffix(args.output.suffix + ".checkpoint")
    results: list[dict] = []
    if checkpoint.exists():
        results = json.loads(checkpoint.read_text(encoding="utf-8"))
    completed_keys = {result["key"] for result in results}
    pending = [item for item in items if item["key"] not in completed_keys]
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, item, args): item["key"] for item in pending}
        for done, future in enumerate(as_completed(futures), start=1):
            key = futures[future]
            try:
                results.append(future.result())
                checkpoint.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:  # retain all failures for a targeted retry
                errors.append(f"{key}: {exc}")
            total_done = len(completed_keys) + done
            if total_done % 10 == 0 or done == len(pending):
                print(json.dumps({"completed": total_done, "total": len(items), "errors": len(errors)}), flush=True)

    order = {item["key"]: index for index, item in enumerate(items)}
    results.sort(key=lambda result: order[result["key"]])
    validation = {r["key"]: validate(by_key[r["key"]], r) for r in results}
    bad = {key: issues for key, issues in validation.items() if issues}
    if errors or bad:
        raise SystemExit(json.dumps({"request_errors": errors, "validation_errors": bad}, ensure_ascii=False, indent=2))

    clean = []
    prompt_tokens = output_tokens = 0
    for result in results:
        usage = result.pop("_usage")
        prompt_tokens += usage["prompt_tokens"]
        output_tokens += usage["output_tokens"]
        clean.append(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(args.output)
    checkpoint.unlink(missing_ok=True)
    summary = {
        "output": str(args.output),
        "rows": len(clean),
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "validation_errors": 0,
    }
    meta = args.output.with_suffix(args.output.suffix + ".meta.json")
    meta.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
