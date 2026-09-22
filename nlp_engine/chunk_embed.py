"""자막을 조각으로 나누고 Voyage 임베딩을 영상별로 캐시한다 (T4).

- 조각: 글자 수 기준 창(기본 1,200자, 200자 겹침). 자동 생성 자막은 문장부호가 없어 문장 분할을 쓰지 않는다.
- 캐시: data/embeddings/<video_id>.npz (chunks, starts, vectors[float16], model, chunk_chars, overlap)
  이미 있으면 건너뛰므로 중단 후 다시 실행해도 된다.
- 대상: 기본은 upload_date 가 --since 이상인 영상만. 날짜가 비어 있는 영상은 --include-undated 일 때만.

    python -m nlp_engine.chunk_embed --ids VIDEO_ID [VIDEO_ID ...]
    python -m nlp_engine.chunk_embed --since 20210501
"""
import argparse
import json
from pathlib import Path

import numpy as np

from nlp_engine.voyage_embedder import VoyageEmbedder

BASE_DIR = Path(__file__).resolve().parent.parent
TRANSCRIPT_DIR = BASE_DIR / "data" / "transcripts"
EMBED_DIR = BASE_DIR / "data" / "embeddings"
CHUNK_CHARS = 1200
OVERLAP = 200
BATCH = 64


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP) -> tuple[list[str], list[int]]:
    text = " ".join(text.split())
    if not text:
        return [], []
    step = size - overlap
    starts = list(range(0, max(len(text) - overlap, 1), step))
    return [text[s:s + size] for s in starts], starts


def select_videos(ids: list[str] | None, since: str | None, include_undated: bool) -> list[Path]:
    if ids:
        return [TRANSCRIPT_DIR / f"{i}.json" for i in ids]
    out = []
    for f in sorted(TRANSCRIPT_DIR.glob("*.json")):
        date = json.loads(f.read_text(encoding="utf-8")).get("upload_date", "")
        if (date and date >= since) or (not date and include_undated):
            out.append(f)
    return out


def embed_video(path: Path, embedder: VoyageEmbedder) -> str:
    out = EMBED_DIR / f"{path.stem}.npz"
    if out.exists():
        cached = np.load(out, allow_pickle=True)
        if str(cached["model"]) == embedder.model_id and int(cached["chunk_chars"]) == CHUNK_CHARS:
            return "cached"
    record = json.loads(path.read_text(encoding="utf-8"))
    chunks, starts = chunk_text(record.get("transcript", ""))
    if not chunks:
        return "empty"
    vectors = []
    for i in range(0, len(chunks), BATCH):
        vecs = embedder.get_embeddings(chunks[i:i + BATCH], input_type="document")
        if len(vecs) != len(chunks[i:i + BATCH]):
            return "error"
        vectors.extend(vecs)
    np.savez_compressed(out, chunks=np.array(chunks, dtype=object), starts=np.array(starts),
                        vectors=np.asarray(vectors, dtype=np.float16), model=embedder.model_id,
                        chunk_chars=CHUNK_CHARS, overlap=OVERLAP)
    return "embedded"


def main() -> None:
    ap = argparse.ArgumentParser()
    # 대시로 시작하는 ID 는 --ids=-xxxx 로 넘긴다. 여러 번 써도 누적된다.
    ap.add_argument("--ids", nargs="*", action="extend")
    ap.add_argument("--since", default="20210501")
    ap.add_argument("--include-undated", action="store_true")
    args = ap.parse_args()

    EMBED_DIR.mkdir(parents=True, exist_ok=True)
    embedder = VoyageEmbedder()
    files = select_videos(args.ids, args.since, args.include_undated)
    stats: dict[str, int] = {}
    for n, f in enumerate(files, 1):
        status = embed_video(f, embedder)
        stats[status] = stats.get(status, 0) + 1
        if n % 50 == 0:
            print(n, len(files), stats, flush=True)
    print({"videos": len(files), **stats})


if __name__ == "__main__":
    main()
