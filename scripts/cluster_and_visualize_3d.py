"""
Syuka World 3D Video Semantic Clustering & Interactive Visualizer
Loads Voyage-4-large 1,024D embeddings for all 1,920 videos, clusters them with K-Means,
projects them to 3D space with PCA + t-SNE, and exports an interactive Plotly 3D scatter plot.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE

# Path setup
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EMB_DIR = DATA_DIR / "embeddings"
REVIEW_DIR = BASE_DIR / "review"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


CLUSTER_NAMES = {
    0: "🌾 식량·곡물·바이오 (Food & Bio)",
    1: "💻 반도체·AI·빅테크 (Tech & Chips)",
    2: "👥 사회·인구·청년 이슈 (Society & Culture)",
    3: "🏦 기준금리·은행위기·금 (Banks & Macro)",
    4: "🛢️ 원유·에너지·지정학 (Oil & Energy)",
    5: "📺 방송·엔터·소비트렌드 (Media & Living)",
    6: "🏠 부동산·아파트·주택정책 (Real Estate)",
    7: "📈 증시·코스피·투자전략 (Stock & Strategy)",
}

CLUSTER_COLORS = {
    0: "#10b981",  # Emerald
    1: "#3b82f6",  # Blue
    2: "#8b5cf6",  # Purple
    3: "#f59e0b",  # Amber
    4: "#ef4444",  # Rose/Red
    5: "#06b6d4",  # Cyan
    6: "#ec4899",  # Pink
    7: "#6366f1",  # Indigo
}


def build_3d_cluster_pipeline(n_clusters: int = 8, random_state: int = 42) -> pd.DataFrame:
    print("🚀 [1/4] Loading video metadata and Voyage-4-large embeddings...")
    vmeta_path = DATA_DIR / "video_metadata.json"
    vmeta = {}
    if vmeta_path.exists():
        with open(vmeta_path, "r", encoding="utf-8") as f:
            vmeta = {v["video_id"]: v for v in json.load(f)}

    parsed_path = DATA_DIR / "t6" / "subagent_run" / "parsed.csv"
    if not parsed_path.exists():
        raise FileNotFoundError(f"Missing {parsed_path}")
    df_parsed = pd.read_csv(parsed_path)

    records = []
    vectors = []

    for vid, sub in df_parsed.groupby("video_id"):
        npz_file = EMB_DIR / f"{vid}.npz"
        if not npz_file.exists():
            continue

        try:
            d = np.load(npz_file, allow_pickle=True)
            v = np.mean(d["vectors"], axis=0)
        except Exception:
            continue

        vectors.append(v)

        m = vmeta.get(vid, {})
        title = m.get("title") or str(sub["title"].iloc[0])
        dt = str(sub["date"].iloc[0])
        if len(dt) == 8:
            dt = f"{dt[:4]}-{dt[4:6]}-{dt[6:]}"

        rel = sub[sub["relevant"] == 1]
        if not rel.empty and (rel["asset_id"] != "NONE").any():
            asset = rel[rel["asset_id"] != "NONE"]["asset_id"].mode()[0]
        elif "top1" in sub.columns:
            asset = sub["top1"].mode()[0]
        else:
            asset = "TECH"

        url = m.get("url", f"https://www.youtube.com/watch?v={vid}")

        records.append({
            "video_id": vid,
            "title": title,
            "date": dt,
            "asset_id": asset,
            "url": url,
        })

    print(f"  ✓ Processed {len(records)} videos with 1,024-dim vectors.")

    X = np.array(vectors, dtype=np.float32)
    df = pd.DataFrame(records)

    print("🧠 [2/4] Clustering vectors with K-Means (k=8)...")
    kmeans = KMeans(n_clusters=n_clusters, random_state=random_state, n_init=10)
    df["cluster_id"] = kmeans.fit_predict(X)
    df["cluster_name"] = df["cluster_id"].map(CLUSTER_NAMES)
    df["color"] = df["cluster_id"].map(CLUSTER_COLORS)

    print("🌐 [3/4] Reducing dimensions to 3D via PCA (50D) -> t-SNE (3D)...")
    pca = PCA(n_components=50, random_state=random_state)
    X_pca = pca.fit_transform(X)

    tsne = TSNE(n_components=3, perplexity=35, random_state=random_state, max_iter=1000)
    X_3d = tsne.fit_transform(X_pca)

    df["x"] = np.round(X_3d[:, 0], 2)
    df["y"] = np.round(X_3d[:, 1], 2)
    df["z"] = np.round(X_3d[:, 2], 2)

    return df


def generate_plotly_3d_html(df: pd.DataFrame, out_html: Path, out_json: Path) -> None:
    print("🎨 [4/4] Creating interactive Plotly 3D visualization...")
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    # Export compact JSON for web dashboard
    records_to_export = df[[
        "video_id", "title", "date", "asset_id", "cluster_id", "cluster_name", "color", "x", "y", "z", "url"
    ]].to_dict(orient="records")
    out_json.write_text(json.dumps(records_to_export, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ Saved 3D coordinates JSON: {out_json} ({len(records_to_export)} records)")

    fig = go.Figure()

    # Add a separate Scatter3d trace per cluster for interactive toggling
    for cid in sorted(df["cluster_id"].unique()):
        sub = df[df["cluster_id"] == cid]
        cname = CLUSTER_NAMES.get(cid, f"Cluster {cid}")
        color = CLUSTER_COLORS.get(cid, "#3b82f6")

        customdata = np.stack((
            sub["title"].values,
            sub["date"].values,
            sub["cluster_name"].values,
            sub["asset_id"].values,
            sub["url"].values,
        ), axis=-1)

        fig.add_trace(go.Scatter3d(
            x=sub["x"],
            y=sub["y"],
            z=sub["z"],
            mode="markers",
            name=f"{cname} ({len(sub)}개)",
            marker=dict(
                size=4.5,
                color=color,
                opacity=0.85,
                line=dict(width=0.5, color="#ffffff")
            ),
            customdata=customdata,
            hovertemplate=(
                "<b>%{customdata[0]}</b><br><br>"
                "📅 방송일: %{customdata[1]}<br>"
                "🏷️ 테마: %{customdata[2]}<br>"
                "🎯 P24 자산: %{customdata[3]}<br>"
                "<span style='color:#60a5fa;'>클릭 시 유튜브 영상 새 창 재생 ▶</span>"
                "<extra></extra>"
            ),
        ))

    fig.update_layout(
        title=dict(
            text="🪐 슈카월드 1,920개 영상 시맨틱 3D 군집화 맵 (Voyage-4-large + t-SNE 3D)",
            font=dict(size=18, color="#f8fafc"),
            x=0.03,
            y=0.96
        ),
        paper_bgcolor="#090d16",
        plot_bgcolor="#090d16",
        margin=dict(l=0, r=0, t=60, b=0),
        legend=dict(
            font=dict(color="#cbd5e1", size=11),
            bgcolor="rgba(19, 28, 46, 0.8)",
            bordercolor="#223048",
            borderwidth=1,
            x=0.02,
            y=0.92,
            itemsizing="constant"
        ),
        scene=dict(
            xaxis=dict(
                backgroundcolor="#090d16",
                gridcolor="#1e293b",
                showbackground=True,
                zerolinecolor="#334155",
                tickfont=dict(color="#64748b", size=9),
                title=dict(text="X", font=dict(color="#94a3b8"))
            ),
            yaxis=dict(
                backgroundcolor="#090d16",
                gridcolor="#1e293b",
                showbackground=True,
                zerolinecolor="#334155",
                tickfont=dict(color="#64748b", size=9),
                title=dict(text="Y", font=dict(color="#94a3b8"))
            ),
            zaxis=dict(
                backgroundcolor="#090d16",
                gridcolor="#1e293b",
                showbackground=True,
                zerolinecolor="#334155",
                tickfont=dict(color="#64748b", size=9),
                title=dict(text="Z", font=dict(color="#94a3b8"))
            ),
            camera=dict(
                eye=dict(x=1.35, y=1.35, z=1.1)
            )
        ),
        font=dict(family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Pretendard, sans-serif")
    )

    # Render HTML with interactive click-to-open script
    html_raw = fig.to_html(include_plotlyjs="cdn", full_html=True)

    # Inject sleek header bar and click-to-open JS
    custom_header = """
    <div style="position: absolute; top: 12px; right: 24px; z-index: 1000; display: flex; gap: 10px; align-items: center;">
        <span style="color: #94a3b8; font-size: 13px; background: rgba(15, 23, 42, 0.8); padding: 6px 12px; border-radius: 9999px; border: 1px solid #334155;">
            💡 점을 클릭하면 해당 유튜브 영상으로 바로 이동합니다
        </span>
        <a href="./" style="background: #3b82f6; color: #fff; text-decoration: none; padding: 7px 16px; border-radius: 8px; font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 6px;">
            ← 퀀트 대시보드로 돌아가기
        </a>
    </div>
    <script>
        document.addEventListener('DOMContentLoaded', () => {
            const plotDiv = document.getElementsByClassName('plotly-graph-div')[0];
            if (plotDiv) {
                plotDiv.on('plotly_click', function(data) {
                    if (data.points && data.points.length > 0) {
                        const pt = data.points[0];
                        const url = pt.customdata[4];
                        if (url && url.startsWith('http')) {
                            window.open(url, '_blank');
                        }
                    }
                });
            }
        });
    </script>
    """

    full_html = html_raw.replace("<body>", f"<body style='margin:0; background:#090d16;'>\n{custom_header}")
    out_html.write_text(full_html, encoding="utf-8")
    print(f"🎉 3D Interactive HTML generated successfully: {out_html}")


def main():
    parser = argparse.ArgumentParser(description="Cluster Syuka videos and generate 3D visualization")
    parser.add_argument("--clusters", type=int, default=8, help="Number of semantic clusters (default: 8)")
    parser.add_argument("--html", type=str, default=str(REVIEW_DIR / "syuka_videos_3d.html"))
    parser.add_argument("--json", type=str, default=str(DATA_DIR / "syuka_videos_3d.json"))
    args = parser.parse_args()

    df = build_3d_cluster_pipeline(n_clusters=args.clusters)
    generate_plotly_3d_html(df, Path(args.html), Path(args.json))


if __name__ == "__main__":
    main()
