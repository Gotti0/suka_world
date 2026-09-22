import os
import glob
import json
import csv
import re

DATA_DIR = "data/t6/subagent_run"
OUTPUT_CSV = os.path.join(DATA_DIR, "parsed.csv")
REQUESTS_FILE = "data/t6/full-dryrun/requests.jsonl"

def main():
    # 1. Load requests metadata for prompt / raw subtitle text
    req_meta = {}
    with open(REQUESTS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            key = item.get("key") or item.get("custom_id")
            # Extract prompt text, video metadata
            # custom_id format: video_id:chunk_idx
            parts = key.split(":")
            video_id = parts[0]
            chunk_idx = parts[1] if len(parts) > 1 else "0"
            
            prompt_text = ""
            req_obj = item.get("request", {}) or item.get("params", {})
            for part in req_obj.get("contents", [{}])[0].get("parts", []):
                if "text" in part:
                    prompt_text = part["text"]
                    break
            
            # extract title, date, top1, top2, sim1 from prompt_text or metadata
            title_match = re.search(r"\[영상 제목\]\s*(.*?)\n", prompt_text)
            date_match = re.search(r"\[게시일\]\s*(\d+)", prompt_text)
            top1_match = re.search(r"\[후보 자산군 1\]\s*([A-Z0-9_]+)", prompt_text)
            top2_match = re.search(r"\[후보 자산군 2\]\s*([A-Z0-9_]+)", prompt_text)
            sub_match = re.search(r"\[자막 조각\]\s*\n(.*)", prompt_text, re.DOTALL)
            
            req_meta[key] = {
                "video_id": video_id,
                "chunk_idx": chunk_idx,
                "title": title_match.group(1).strip() if title_match else "",
                "date": date_match.group(1).strip() if date_match else "",
                "top1": top1_match.group(1).strip() if top1_match else "",
                "top2": top2_match.group(1).strip() if top2_match else "",
                "sub_text": sub_match.group(1).strip() if sub_match else "",
            }

    # 2. Iterate batches dynamically
    batches = [("batch_1", "worker_{w}_res.json")]
    b_idx = 2
    while True:
        worker_candidate = os.path.join(DATA_DIR, f"worker_0_b{b_idx}_res.json")
        ollama_candidate = os.path.join(DATA_DIR, f"ollama_b{b_idx}_res.json")
        if os.path.exists(worker_candidate) or os.path.exists(ollama_candidate):
            batches.append((f"batch_{b_idx}", f"worker_{{w}}_b{b_idx}_res.json"))
            b_idx += 1
        else:
            break

    all_rows = []
    seen_keys = set()
    quote_fail_count = 0

    for b_idx, (b_name, file_tmpl) in enumerate(batches, start=1):
        ollama_file = os.path.join(DATA_DIR, f"ollama_b{b_idx}_res.json")
        if os.path.exists(ollama_file):
            sources = [("ollama", ollama_file)]
        else:
            sources = [(f"b{b_idx}_w{w}", os.path.join(DATA_DIR, file_tmpl.format(w=w))) for w in range(5)]

        for worker_name, fname in sources:
            if not os.path.exists(fname):
                print(f"Warning: {fname} does not exist!")
                continue
            with open(fname, "r", encoding="utf-8") as f:
                worker_data = json.load(f)
            
            for item in worker_data:
                key = item["key"]
                if key in seen_keys:
                    print(f"Duplicate key ignored: {key}")
                    continue
                seen_keys.add(key)
                
                meta = req_meta.get(key, {
                    "video_id": key.split(":")[0],
                    "chunk_idx": key.split(":")[1] if ":" in key else "0",
                    "title": "",
                    "date": "",
                    "top1": "",
                    "top2": "",
                    "sub_text": "",
                })
                
                relevant = 1 if item.get("relevant") else 0
                asset_id = item.get("asset_id", "NONE") if relevant else "NONE"
                stance = int(item.get("stance", 0)) if relevant else 0
                confidence = float(item.get("confidence", 0.0))
                quote = item.get("quote", "").strip() if relevant else ""
                reason = item.get("reason", "").strip()
                
                # Validate quote
                quote_ok = 1
                if relevant and quote:
                    # Clean quote for matching (ignore whitespace differences)
                    raw_sub = meta["sub_text"]
                    clean_quote = re.sub(r"\s+", "", quote)
                    clean_sub = re.sub(r"\s+", "", raw_sub)
                    if clean_quote not in clean_sub:
                        # Try partial or substring match
                        quote_ok = 0
                        quote_fail_count += 1
                        print(f"[{key}] Quote validation failed:\nQuote: {quote[:40]}...\nSub: {raw_sub[:60]}...")
                elif relevant and not quote:
                    quote_ok = 0
                
                all_rows.append({
                    "key": key,
                    "batch": b_name,
                    "worker": worker_name,
                    "video_id": meta["video_id"],
                    "chunk_idx": meta["chunk_idx"],
                    "date": meta["date"],
                    "title": meta["title"],
                    "top1": meta["top1"],
                    "top2": meta["top2"],
                    "sim1": "", # optional
                    "relevant": relevant,
                    "asset_id": asset_id,
                    "stance": stance,
                    "confidence": confidence,
                    "quote": quote,
                    "quote_ok": quote_ok,
                    "reason": reason,
                })

    # 3. Write CSV
    headers = [
        "key", "batch", "worker", "video_id", "chunk_idx", "date", "title",
        "top1", "top2", "sim1", "relevant", "asset_id", "stance", "confidence",
        "quote", "quote_ok", "reason"
    ]
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nSuccessfully compiled {len(all_rows)} rows to {OUTPUT_CSV}")
    print(f"Total quote failures: {quote_fail_count}")

    # 4. Summary Stats
    rel_rows = [r for r in all_rows if r["relevant"] == 1]
    non_rel_rows = [r for r in all_rows if r["relevant"] == 0]
    stance_p1 = [r for r in rel_rows if r["stance"] == 1]
    stance_0  = [r for r in rel_rows if r["stance"] == 0]
    stance_m1 = [r for r in rel_rows if r["stance"] == -1]

    print(f"\n=== Summary Statistics ({len(all_rows)} items) ===")
    print(f"Relevant items: {len(rel_rows)} ({len(rel_rows)/len(all_rows)*100:.1f}%)")
    print(f"Non-relevant items: {len(non_rel_rows)} ({len(non_rel_rows)/len(all_rows)*100:.1f}%)")
    print(f"Relevant Stance Distribution:")
    print(f"  +1 (Bullish): {len(stance_p1)} ({len(stance_p1)/len(rel_rows)*100:.1f}%)")
    print(f"   0 (Neutral): {len(stance_0)} ({len(stance_0)/len(rel_rows)*100:.1f}%)")
    print(f"  -1 (Bearish): {len(stance_m1)} ({len(stance_m1)/len(rel_rows)*100:.1f}%)")

    # Asset distribution
    from collections import Counter
    asset_counts = Counter(r["asset_id"] for r in rel_rows)
    print("\nTop 10 Asset Classes:")
    for asset, cnt in asset_counts.most_common(10):
        print(f"  {asset}: {cnt} ({cnt/len(rel_rows)*100:.1f}%)")

if __name__ == "__main__":
    main()
