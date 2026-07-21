from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.skolenkaten_importer import (
    build_skolenkaten_import,
    ensure_source_files,
    load_baseline,
    write_import_json,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Skolinspektionen Skolenkäten survey ratings into Gothenburg School Guide JSON format.")
    parser.add_argument("--year", type=int, default=2026, help="Skolenkäten year to import. Currently configured for 2026.")
    parser.add_argument("--baseline", type=Path, default=ROOT / "data" / "schools-2026.json")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data" / "source_cache" / "skolenkaten")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--no-download", action="store_true", help="Use cached Excel files only; do not download from Skolinspektionen.")
    args = parser.parse_args()

    output = args.output or (ROOT / "data" / "imports" / f"schools-{args.year}-skolenkaten.json")
    baseline = load_baseline(args.baseline)
    files = ensure_source_files(args.year, args.cache_dir, use_network=not args.no_download)
    records, metadata = build_skolenkaten_import(baseline, files, year=args.year)
    write_import_json(records, output, metadata)
    print(json.dumps({"ok": True, "output": str(output), **metadata}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
