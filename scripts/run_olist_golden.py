"""Execute the checked-in Olist BI regression queries through the safe DB layer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.tools.db_tools import execute_sql, get_db_path, validate_sql  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=PROJECT_ROOT / "data" / "olist_golden_queries.json",
    )
    return parser.parse_args()


def main() -> int:
    cases_path = parse_args().cases.resolve()
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    database = get_db_path()
    failures = 0

    for case in cases:
        validation = validate_sql(case["sql"], database)
        result = execute_sql(case["sql"], database, max_rows=500, timeout_seconds=10)
        expected_first_row = case.get("expected_first_row")
        first_row_matches = expected_first_row is None or (
            bool(result["data"])
            and all(
                result["data"][0].get(key) == value
                for key, value in expected_first_row.items()
            )
        )
        passed = (
            validation["valid"]
            and result["success"]
            and not result["truncated"]
            and result["row_count"] >= case.get("min_rows", 1)
            and first_row_matches
        )
        if not passed:
            failures += 1
        print(
            json.dumps(
                {
                    "name": case["name"],
                    "passed": passed,
                    "validation_error": validation["error"],
                    "execution_error": result["error"],
                    "row_count": result["row_count"],
                    "first_row_matches": first_row_matches,
                    "first_rows": (result["data"] or [])[:5],
                },
                ensure_ascii=False,
            )
        )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
