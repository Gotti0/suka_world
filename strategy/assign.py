"""자막 조각을 P24 자산군에 배정한다 (T5).

- 자산군 설명문(strategy/p24.yaml 의 description)을 input_type="query" 로 임베딩해 캐시한다.
- 조각 임베딩(data/embeddings/*.npz)과의 코사인 유사도로 1순위·2순위 자산군을 구한다.
- 임계값은 여기서 적용하지 않는다. T7 사람 검수로 정한 뒤 T8 에서 동결한다.

산출물: data/assignments.csv (video_id, chunk_idx, start, top1, sim1, top2, sim2)

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


def load_desc_vectors(embedder: VoyageEmbedder) -> tuple[list[str], np.ndarray]:
    assets = yaml.safe_load(P24_PATH.read_text(encoding="utf-8"))["assets"]
    ids = [a["id"] for a in assets]
    texts = [f"{a['name']}. {a['description']}" for a in assets]
    digest = hashlib.sha256(json.dumps([embedder.model_id, texts], ensure_ascii=False).encode()).hexdigest()
    if DESC_CACHE.exists():
        cached = np.load(DESC_CACHE, allow_pickle=True)
        if str(cached["digest"]) == digest:
            return ids, cached["vectors"].astype(np.float32)
    vecs = np.asarray(embedder.get_embeddings(texts, input_type="query"), dtype=np.float32)
    if vecs.shape[0] != len(ids):
        raise RuntimeError("자산군 설명문 임베딩 실패")
    np.savez(DESC_CACHE, ids=np.array(ids), vectors=vecs, digest=digest)
    return ids, vecs


def normalize(m: np.ndarray) -> np.ndarray:
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def main() -> None:
    ids, desc = load_desc_vectors(VoyageEmbedder())
    desc = normalize(desc)
    rows = []
    for f in sorted(EMBED_DIR.glob("*.npz")):
        z = np.load(f, allow_pickle=True)
        sims = normalize(z["vectors"].astype(np.float32)) @ desc.T
        order = np.argsort(-sims, axis=1)
        for i, (row, o) in enumerate(zip(sims, order)):
            rows.append([f.stem, i, int(z["starts"][i]), ids[o[0]], f"{row[o[0]]:.4f}", ids[o[1]], f"{row[o[1]]:.4f}"])
    with OUT_PATH.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["video_id", "chunk_idx", "start", "top1", "sim1", "top2", "sim2"])
        w.writerows(rows)
    print({"chunks": len(rows), "videos": len({r[0] for r in rows}), "out": str(OUT_PATH)})


if __name__ == "__main__":
    main()
