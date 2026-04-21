"""RMD Agent test runner.

Runs all fixtures in prompts/ against the RMD agent and reports pass/fail.
Uses manual-input path — no database calls, no AWS credentials needed.

Usage:
  uv run python run_tests.py
  make test
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add the agent root to sys.path so `import rmd` resolves to ./rmd/
sys.path.insert(0, str(Path(__file__).parent))

import rmd

PROMPTS_DIR = Path(__file__).parent / "prompts"
PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"


def run_fixture(path: Path) -> bool:
    fixture = json.loads(path.read_text())
    fid = fixture["id"]
    desc = fixture["description"]
    account_id = fixture["account_id"]
    client_input = fixture.get("client_input", {})

    expected_eligible = fixture.get("expected_eligible")
    expected_status = fixture.get("expected_status")
    expected_decision = fixture.get("expected_decision")

    result = rmd.evaluate("", account_id, client_input)

    errors = []

    # Check decision (missing_data / insufficient_data path)
    if expected_decision is not None:
        actual_decision = result.get("decision")
        if actual_decision != expected_decision:
            errors.append(f"decision: expected={expected_decision!r} got={actual_decision!r}")

    # Check eligibility
    if expected_eligible is not None:
        actual_eligible = result.get("eligible")
        if actual_eligible != expected_eligible:
            errors.append(f"eligible: expected={expected_eligible} got={actual_eligible}")

    # Check withdrawal status
    if expected_status is not None:
        actual_status = result.get("withdrawal_status")
        if actual_status != expected_status:
            errors.append(f"withdrawal_status: expected={expected_status!r} got={actual_status!r}")

    status = PASS if not errors else FAIL
    print(f"  {status}  [{fid}] {desc}")
    for e in errors:
        print(f"         {e}")

    return not errors


def main() -> None:
    fixtures = sorted(PROMPTS_DIR.glob("*.json"))
    if not fixtures:
        print("No fixtures found in prompts/")
        sys.exit(1)

    print(f"\nRunning {len(fixtures)} RMD test fixtures\n")
    results = [run_fixture(f) for f in fixtures]

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} passed")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
