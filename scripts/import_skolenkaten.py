from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.main import bootstrap_database, database_baseline
from app.skolenkaten_importer import (
    build_skolenkaten_import,
    ensure_source_files,
    load_baseline,
    write_import_json,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Import official Skolenkäten survey ratings for all loaded city schools.")
    parser.add_argument("--year", type=int, default=2026, choices=[2025, 2026], help="Skolenkäten publication year.")
    parser.add_argument("--baseline", type=Path, default=None, help="Optional JSON baseline; defaults to the running four-city database baseline.")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data" / "source_cache" / "skolenkaten")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--no-download", action="store_true", help="Use cached Excel files only.")
    args = parser.parse_args()

    output = args.output or (ROOT / "data" / "imports" / f"schools-{args.year}-skolenkaten-four-cities.json")
    if args.baseline:
        baseline = load_baseline(args.baseline)
    else:
        bootstrap_database()
        baseline = database_baseline(args.year)
    files = ensure_source_files(args.year, args.cache_dir, use_network=not args.no_download)
    records, metadata = build_skolenkaten_import(baseline, files, year=args.year)
    write_import_json(records, output, metadata)
    print(json.dumps({"ok": True, "output": str(output), **metadata}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
