"""Real-data RMD test runner.

Runs real-*.json fixtures from prompts/real/ against live ontology data.
Requires AWS_PROFILE=data-lake-dev and a valid auth token.

Usage:
  make test-real
  make test-real-trace   # also sends traces to Phoenix
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from agents.rmd.core import evaluate as rmd_evaluate

PROMPTS_DIR = Path(__file__).parent / "prompts" / "real"

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"

# Tracer — set up when --trace is passed, otherwise a no-op
_tracer = None


def _setup_tracing() -> bool:
    global _tracer
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent / "../../.env")

        endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT")
        if not endpoint:
            print("[trace] PHOENIX_COLLECTOR_ENDPOINT not set — tracing disabled")
            return False

        from phoenix.otel import register
        from opentelemetry import trace

        register(
            project_name="rmd-agent",
            endpoint=endpoint.rstrip("/") + "/v1/traces",
            api_key=os.environ.get("PHOENIX_API_KEY") or None,
            auto_instrument=True,
            batch=False,
            verbose=False,
        )
        _tracer = trace.get_tracer("rmd-real-tests")
        print(f"[trace] Phoenix tracing active → {endpoint}")
        return True
    except ImportError:
        print("[trace] phoenix-otel not installed — tracing disabled")
        return False
    except Exception as e:
        print(f"[trace] tracing setup failed: {e}")
        return False


def _get_auth_token() -> str:
    env_file = Path(__file__).parent.parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    token = os.environ.get("FARTHER_AUTH_TOKEN", "")
    if not token:
        print("ERROR: FARTHER_AUTH_TOKEN not set. Run: make token", file=sys.stderr)
        sys.exit(1)
    return token


def _check_result(result: dict, expected_decision: str | None, expected_eligible,
                  expected_status: str | None, expected_rmd: float | None,
                  expected_remaining: float | None) -> list[str]:
    errors = []
    if expected_decision is not None:
        actual = result.get("decision")
        if actual != expected_decision:
            errors.append(f"decision: expected={expected_decision!r} got={actual!r}")
    if expected_eligible is not None:
        actual = result.get("eligible")
        if actual != expected_eligible:
            errors.append(f"eligible: expected={expected_eligible} got={actual}")
    if expected_status is not None:
        actual = result.get("withdrawal_status")
        if actual != expected_status:
            errors.append(f"withdrawal_status: expected={expected_status!r} got={actual!r}")
    if expected_rmd is not None:
        actual = result.get("rmd_required_amount")
        if actual != round(expected_rmd, 2):
            errors.append(f"rmd_required_amount: expected={expected_rmd} got={actual}")
    if expected_remaining is not None:
        actual = result.get("remaining_rmd")
        if actual != round(expected_remaining, 2):
            errors.append(f"remaining_rmd: expected={expected_remaining} got={actual}")
    return errors


def _print_result(tag: str, label: str, result: dict, errors: list[str]) -> None:
    source = result.get("_source", "?")
    completeness = result.get("completeness", "?")
    rmd_amt = result.get("rmd_required_amount")
    remaining = result.get("remaining_rmd")
    print(f"  {tag}  {label}  [{source}, {completeness}]")
    if rmd_amt is not None:
        print(f"         rmd=${rmd_amt:,.2f}  remaining=${remaining:,.2f}")
    for e in errors:
        print(f"         {e}")


def run_fixture(path: Path, token: str) -> bool:
    fixture = json.loads(path.read_text())
    fid = fixture["id"]
    desc = fixture["description"]
    account_id = fixture["account_id"]
    client_input = fixture.get("client_input", {})
    label = f"[{fid}] {desc}"

    span_name = f"rmd.evaluate [{fid}]"

    def _run() -> dict:
        return rmd_evaluate(token, account_id, client_input)

    if _tracer is not None:
        from opentelemetry.trace import StatusCode
        with _tracer.start_as_current_span(span_name) as span:
            span.set_attribute("fixture.id", fid)
            span.set_attribute("fixture.description", desc)
            span.set_attribute("account_id", account_id)
            span.set_attribute("input.value", json.dumps({"account_id": account_id, "client_input": client_input}))
            span.set_attribute("input.mime_type", "application/json")
            try:
                result = _run()
            except Exception as e:
                span.set_status(StatusCode.ERROR, str(e))
                span.set_attribute("error", str(e))
                print(f"  {FAIL}  {label}\n         exception: {e}")
                return False
            span.set_attribute("output.value", json.dumps(result))
            span.set_attribute("output.mime_type", "application/json")
            span.set_attribute("decision", result.get("decision", ""))
            span.set_attribute("completeness", result.get("completeness", ""))
            span.set_attribute("eligible", str(result.get("eligible")))
    else:
        try:
            result = _run()
        except Exception as e:
            print(f"  {FAIL}  {label}\n         exception: {e}")
            return False

    if result.get("decision") == "INSUFFICIENT_DATA" and result.get("_source") in ("not_found", "pre_check:missing_fields"):
        print(f"  {SKIP}  {label}")
        print(f"         missing: {result.get('missing_fields', [])} — check token: make token")
        return True

    errors = _check_result(
        result,
        fixture.get("expected_decision"),
        fixture.get("expected_eligible"),
        fixture.get("expected_status"),
        fixture.get("expected_rmd_amount"),
        fixture.get("expected_remaining"),
    )
    tag = PASS if not errors else FAIL
    _print_result(tag, label, result, errors)

    if _tracer is not None:
        from opentelemetry import trace as _trace
        current = _trace.get_current_span()
        if errors:
            current.set_attribute("test.passed", False)
            current.set_attribute("test.errors", json.dumps(errors))
        else:
            current.set_attribute("test.passed", True)

    return not errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", action="store_true", help="Send traces to Phoenix")
    args = parser.parse_args()

    if args.trace:
        _setup_tracing()

    token = _get_auth_token()

    fixtures = sorted(PROMPTS_DIR.glob("*.json"))
    if not fixtures:
        print("No real fixtures found (expected prompts/real/*.json)")
        sys.exit(1)

    print(f"\nRunning {len(fixtures)} real-data fixtures\n")
    results = [run_fixture(f, token) for f in fixtures]

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} passed")
    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
