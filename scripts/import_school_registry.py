from __future__ import annotations

import argparse
import json

from app.main import run_registry_sync, run_survey_sync


def main() -> None:
    parser = argparse.ArgumentParser(description="Import school facts for the four supported city datasets from Skolverket.")
    parser.add_argument("--skip-surveys", action="store_true", help="Import school registry facts only.")
    args = parser.parse_args()
    registry = run_registry_sync(import_surveys=False)
    result = {"registry": registry}
    if not args.skip_surveys:
        result["surveys"] = run_survey_sync()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if registry.get("status") != "complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
