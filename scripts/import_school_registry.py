from __future__ import annotations

import argparse
import json

from app.main import run_registry_sync


def main() -> None:
    parser = argparse.ArgumentParser(description="Import school facts for the four supported city datasets from Skolverket.")
    parser.add_argument("--skip-surveys", action="store_true", help="Import school registry facts only.")
    args = parser.parse_args()
    result = run_registry_sync(import_surveys=not args.skip_surveys)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") != "complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
