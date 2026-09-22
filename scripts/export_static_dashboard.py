"""
Export Static Site Bundle for GitHub Pages or Offline Serving.
Dumps index.html, static data JSON files (summary, equity, calls, assets, signals),
and creates .nojekyll for GitHub Pages compatibility.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dashboard.app import (
    get_assets_data,
    get_calls_data,
    get_equity_data,
    get_summary_data,
    get_asset_signals,
    index,
)


def export_static_site(out_dir: Path) -> None:
    print(f"📦 Exporting static dashboard to: {out_dir.resolve()}")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    data_dir = out_dir / "data"
    signals_dir = data_dir / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)

    # 1. index.html
    html_content = index()
    (out_dir / "index.html").write_text(html_content, encoding="utf-8")
    print("  ✓ Created index.html")

    # 2. .nojekyll (tells GitHub Pages not to process with Jekyll)
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    print("  ✓ Created .nojekyll")

    # 3. Summary
    summary = get_summary_data()
    (data_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("  ✓ Created data/summary.json")

    # 4. Equity & Drawdowns
    equity = get_equity_data()
    (data_dir / "equity.json").write_text(
        json.dumps(equity, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("  ✓ Created data/equity.json")

    # 5. Calls
    calls = get_calls_data()
    (data_dir / "calls.json").write_text(
        json.dumps(calls, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("  ✓ Created data/calls.json")

    # 6. Assets
    assets = get_assets_data()
    (data_dir / "assets.json").write_text(
        json.dumps(assets, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  ✓ Created data/assets.json ({len(assets)} assets)")

    # 7. Asset signals (24 assets)
    exported_signals = 0
    for a in assets:
        aid = a["id"]
        sig = get_asset_signals(aid)
        (signals_dir / f"{aid}.json").write_text(
            json.dumps(sig, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        exported_signals += 1
    print(f"  ✓ Created data/signals/*.json ({exported_signals} asset signals)")

    total_files = sum(1 for _ in out_dir.rglob("*") if _.is_file())
    total_bytes = sum(_.stat().st_size for _ in out_dir.rglob("*") if _.is_file())
    print(f"🎉 Successfully exported {total_files} files ({total_bytes / 1024:.1f} KB total)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export static site bundle for GitHub Pages")
    parser.add_argument("--out", type=str, default="dist", help="Output directory (default: dist)")
    args = parser.parse_args()

    export_static_site(Path(args.out))
