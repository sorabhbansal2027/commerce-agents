"""
Reads Apex test result JSON from sf apex run test --result-format json
and fails if any class is below the 75% coverage threshold.
"""
import json
import sys
from pathlib import Path

THRESHOLD = 75


def main(results_dir: str) -> None:
    results_path = Path(results_dir)
    json_files = list(results_path.glob("*.json"))

    if not json_files:
        print(f"No JSON test result files found in {results_dir}")
        sys.exit(1)

    report = json.loads(json_files[0].read_text())

    # sf CLI puts coverage under result.codecoverage
    coverage_records = (
        report.get("result", {}).get("codecoverage", [])
        or report.get("codecoverage", [])
    )

    if not coverage_records:
        print("No code coverage data found — skipping coverage gate")
        return

    failures = []
    for record in coverage_records:
        name = record.get("name", "Unknown")
        covered = record.get("numLinesCovered", 0)
        uncovered = record.get("numLinesUncovered", 0)
        total = covered + uncovered

        if total == 0:
            continue

        pct = round((covered / total) * 100, 1)
        status = "OK" if pct >= THRESHOLD else "FAIL"
        print(f"  [{status}] {name}: {pct}% ({covered}/{total} lines)")

        if pct < THRESHOLD:
            failures.append((name, pct))

    if failures:
        print(f"\n❌ {len(failures)} class(es) below {THRESHOLD}% coverage threshold:")
        for name, pct in failures:
            print(f"   - {name}: {pct}%")
        sys.exit(1)
    else:
        print(f"\n✅ All classes meet the {THRESHOLD}% coverage threshold")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <results-dir>")
        sys.exit(1)
    main(sys.argv[1])
