"""자막 조각을 P24 자산군에 배정한다 (T5).

- 자산군 설명문과 "해당 없음" 닻 설명문(strategy/p24.yaml)을 input_type="query" 로 임베딩해 캐시한다.
- 조각 임베딩(data/embeddings/*.npz)과의 코사인 유사도로 자산군 1·2순위와 가장 가까운 닻을 구한다.
- 가장 가까운 닻이 1순위 자산군보다 가까우면 null_win=1 이다(배정하지 않을 후보).
- 임계값은 여기서 적용하지 않는다. T7 사람 검수로 정한 뒤 T8 에서 동결한다.

산출물: data/assignments.csv
  (video_id, chunk_idx, start, top1, sim1, top2, sim2, null_top, null_sim, null_win)

    python -m strategy.assign
"""
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import yaml

from nlp_engine.voyage_embedder import VoyageEmbedder

BASE_DIR = Path(__file__).resolve().parent.parent
P24_PATH = BASE_DIR / "strategy" / "p24.yaml"
EMBED_DIR = BASE_DIR / "data" / "embeddings"
DESC_CACHE = BASE_DIR / "data" / "p24_desc_embeddings.npz"
OUT_PATH = BASE_DIR / "data" / "assignments.csv"


def load_desc_vectors(embedder: VoyageEmbedder) -> tuple[list[str], np.ndarray, list[str], np.ndarray]:
    spec = yaml.safe_load(P24_PATH.read_text(encoding="utf-8"))
    assets, anchors = spec["assets"], spec.get("null_anchors", [])
    ids = [a["id"] for a in assets] + [n["id"] for n in anchors]
    texts = [f"{a['name']}. {a['description']}" for a in assets] + [n["description"] for n in anchors]
    digest = hashlib.sha256(json.dumps([embedder.model_id, ids, texts], ensure_ascii=False).encode()).hexdigest()
    vecs = None
    if DESC_CACHE.exists():
        cached = np.load(DESC_CACHE, allow_pickle=True)
        if str(cached["digest"]) == digest:
            vecs = cached["vectors"].astype(np.float32)
    if vecs is None:
        vecs = np.asarray(embedder.get_embeddings(texts, input_type="query"), dtype=np.float32)
        if vecs.shape[0] != len(ids):
            raise RuntimeError("설명문 임베딩 실패")
        np.savez(DESC_CACHE, ids=np.array(ids), vectors=vecs, digest=digest)
    n = len(assets)
    return ids[:n], vecs[:n], ids[n:], vecs[n:]


def normalize(m: np.ndarray) -> np.ndarray:
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def main() -> None:
    asset_ids, asset_vecs, null_ids, null_vecs = load_desc_vectors(VoyageEmbedder())
    asset_vecs, null_vecs = normalize(asset_vecs), normalize(null_vecs)
    rows = []
    for f in sorted(EMBED_DIR.glob("*.npz")):
        z = np.load(f, allow_pickle=True)
        chunks = normalize(z["vectors"].astype(np.float32))
        sims = chunks @ asset_vecs.T
        order = np.argsort(-sims, axis=1)
        nsims = chunks @ null_vecs.T if len(null_ids) else np.zeros((len(chunks), 0))
        for i, (row, o) in enumerate(zip(sims, order)):
            j = int(np.argmax(nsims[i])) if len(null_ids) else -1
            nsim = float(nsims[i, j]) if j >= 0 else float("-inf")
            rows.append([f.stem, i, int(z["starts"][i]),
                         asset_ids[o[0]], f"{row[o[0]]:.4f}", asset_ids[o[1]], f"{row[o[1]]:.4f}",
                         null_ids[j] if j >= 0 else "", f"{nsim:.4f}" if j >= 0 else "",
                         int(nsim > row[o[0]])])
    with OUT_PATH.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["video_id", "chunk_idx", "start", "top1", "sim1", "top2", "sim2",
                    "null_top", "null_sim", "null_win"])
        w.writerows(rows)
    print({"chunks": len(rows), "videos": len({r[0] for r in rows}),
           "null_win": sum(r[-1] for r in rows), "out": str(OUT_PATH)})


if __name__ == "__main__":
    main()
