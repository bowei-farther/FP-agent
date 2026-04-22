"""RMD Agent test runner.

Runs all fixtures in prompts/ against the RMD agent and reports pass/fail.
Uses manual-input path — no database calls, no AWS credentials needed.

Usage (always run from repo root):
  make test                             # correctness only
  make test-latency                     # correctness + latency report
  make test-trace                       # correctness + Phoenix tracing (requires .env)
  uv run python agents/rmd/run_tests.py [--latency] [--trace]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path
from statistics import mean, quantiles

from agents.rmd.core import evaluate as rmd_evaluate

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Strands uses httpx async internally via asyncio.run() in a ThreadPoolExecutor.
# On Python 3.13, when the per-agent event loop closes, httpx connection cleanup
# emits "Task exception was never retrieved / RuntimeError: Event loop is closed"
# via asyncio's exception handler. All fixtures still pass — cosmetic noise from
# httpx cleanup ordering. Suppress it so CI output stays readable.
#
# asyncio.run() on Python 3.13 accepts loop_factory (via asyncio.Runner). We patch
# asyncio.run to install a custom exception handler on every loop it creates.
import asyncio

def _loop_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    exc = context.get("exception")
    if isinstance(exc, RuntimeError) and "Event loop is closed" in str(exc):
        return
    loop.default_exception_handler(context)

def _loop_factory() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    loop.set_exception_handler(_loop_exception_handler)
    return loop

_orig_asyncio_run = asyncio.run
def _patched_asyncio_run(coro, **kwargs):  # type: ignore[no-untyped-def]
    if "loop_factory" not in kwargs:
        kwargs["loop_factory"] = _loop_factory
    return _orig_asyncio_run(coro, **kwargs)
asyncio.run = _patched_asyncio_run  # type: ignore[assignment]
PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"


def _setup_tracing() -> bool:
    """Register Phoenix tracing if available. Returns True if tracing is active."""
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent / ".env")
        load_dotenv(Path(__file__).parent / "../../.env")  # repo root

        import os
        endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT")
        if not endpoint:
            print("[trace] PHOENIX_COLLECTOR_ENDPOINT not set — tracing disabled")
            return False

        from phoenix.otel import register
        api_key = os.environ.get("PHOENIX_API_KEY") or None
        register(
            project_name="rmd-agent",
            endpoint=endpoint.rstrip("/") + "/v1/traces",
            api_key=api_key,
            auto_instrument=True,
            batch=False,
            verbose=False,
        )
        print(f"[trace] Phoenix tracing active → {endpoint}")
        return True
    except ImportError:
        print("[trace] phoenix-otel not installed — tracing disabled")
        return False
    except Exception as e:
        print(f"[trace] tracing setup failed: {e}")
        return False


def run_fixture(path: Path, measure_latency: bool = False) -> tuple[bool, float]:
    """Run a single fixture. Returns (passed, latency_s)."""
    fixture = json.loads(path.read_text())
    fid = fixture["id"]
    desc = fixture["description"]
    account_id = fixture["account_id"]
    client_input = fixture.get("client_input", {})

    expected_eligible = fixture.get("expected_eligible")
    expected_status = fixture.get("expected_status")
    expected_decision = fixture.get("expected_decision")
    expected_completeness = fixture.get("expected_completeness")
    expected_data_quality = fixture.get("expected_data_quality")  # list or null

    # Optional: pin today's date so deadline-sensitive decisions are reproducible
    test_date_str = fixture.get("_test_date")
    test_date = date.fromisoformat(test_date_str) if test_date_str else None

    t0 = time.monotonic()
    result = rmd_evaluate("", account_id, client_input, _today=test_date)
    latency_s = time.monotonic() - t0

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

    if expected_completeness is not None:
        actual = result.get("completeness")
        if actual != expected_completeness:
            errors.append(f"completeness: expected={expected_completeness!r} got={actual!r}")

    if expected_data_quality is not None:
        actual = result.get("data_quality", [])
        if set(actual) != set(expected_data_quality):
            errors.append(f"data_quality: expected={sorted(expected_data_quality)} got={sorted(actual)}")

    passed = not errors
    status = PASS if passed else FAIL

    latency_str = f"  {latency_s:.2f}s" if measure_latency else ""
    print(f"  {status}  [{fid}] {desc}{latency_str}")
    for e in errors:
        print(f"         {e}")

    return passed, latency_s


def _print_latency_report(latencies: list[float]) -> None:
    if not latencies:
        return
    qs = quantiles(latencies, n=100)
    print(f"\n--- Latency report ({len(latencies)} fixtures) ---")
    print(f"  min    {min(latencies):.2f}s")
    print(f"  p50    {qs[49]:.2f}s")
    print(f"  p75    {qs[74]:.2f}s")
    print(f"  p95    {qs[94]:.2f}s")
    print(f"  max    {max(latencies):.2f}s")
    print(f"  mean   {mean(latencies):.2f}s")

    p95 = qs[94]
    threshold = 30.0  # Bedrock baseline: p50=5.6s, mean=5.6s, cold start outlier ~23s
    if p95 > threshold:
        print(f"\n  \033[91mWARN\033[0m  p95 {p95:.2f}s exceeds threshold {threshold}s")
    else:
        print(f"\n  \033[92mOK\033[0m    p95 {p95:.2f}s within threshold {threshold}s")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latency", action="store_true", help="Show per-fixture latency and p95 report")
    parser.add_argument("--trace", action="store_true", help="Send traces to Phoenix (requires PHOENIX_COLLECTOR_ENDPOINT)")
    args = parser.parse_args()

    if args.trace:
        _setup_tracing()

    fixtures = sorted(f for f in PROMPTS_DIR.glob("*.json") if not f.name.startswith("nl-"))
    if not fixtures:
        print("No fixtures found in prompts/")
        sys.exit(1)

    print(f"\nRunning {len(fixtures)} RMD test fixtures\n")
    results = [run_fixture(f, measure_latency=args.latency) for f in fixtures]

    passed_list = [r[0] for r in results]
    latencies   = [r[1] for r in results]

    passed = sum(passed_list)
    total  = len(passed_list)
    print(f"\n{passed}/{total} passed")

    if args.latency:
        _print_latency_report(latencies)

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
