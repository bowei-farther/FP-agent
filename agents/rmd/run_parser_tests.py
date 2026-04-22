"""NL parser test runner (Task 9).

Runs NL fixtures through parser.parse() and checks extracted fields.
Each call is traced as a span to Phoenix so extraction quality is visible
in the ontology-eval project.

Usage:
  make test-parser              # correctness only
  make test-parser-trace        # correctness + Phoenix traces
  uv run python agents/rmd/run_parser_tests.py [--trace]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from statistics import mean, quantiles

PROMPTS_DIR = Path(__file__).parent / "prompts"

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"


def _setup_tracing() -> bool:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent / ".env")
        load_dotenv(Path(__file__).parent / "../../.env")

        import os
        endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT")
        if not endpoint:
            print("[trace] PHOENIX_COLLECTOR_ENDPOINT not set — tracing disabled")
            return False

        from phoenix.otel import register
        api_key = os.environ.get("PHOENIX_API_KEY") or None
        tracer_provider = register(
            project_name="ontology-eval",
            endpoint=endpoint.rstrip("/") + "/v1/traces",
            api_key=api_key,
            auto_instrument=False,
            batch=False,
            verbose=False,
        )
        # Instrument boto3 bedrock-runtime so LLM calls appear with input/output/tokens
        from openinference.instrumentation.bedrock import BedrockInstrumentor
        BedrockInstrumentor().instrument(tracer_provider=tracer_provider)
        print(f"[trace] Phoenix tracing active → {endpoint} (project: ontology-eval)")
        return True
    except ImportError:
        print("[trace] phoenix-otel not installed — tracing disabled")
        return False
    except Exception as e:
        print(f"[trace] tracing setup failed: {e}")
        return False


def _check_field(errors: list, field: str, expected, actual_dict: dict) -> None:
    """Check one extracted field against expected value."""
    if expected is None:
        # Expected absent — pass if key missing OR value is None/falsy
        actual = actual_dict.get(field)
        if actual is not None and actual != "" and actual != 0:
            errors.append(f"{field}: expected null/absent, got {actual!r}")
    else:
        actual = actual_dict.get(field)
        if actual is None:
            errors.append(f"{field}: expected {expected!r}, got absent")
        elif isinstance(expected, float) or isinstance(expected, int):
            if float(actual) != float(expected):
                errors.append(f"{field}: expected {expected}, got {actual!r}")
        elif str(actual).strip() != str(expected).strip():
            errors.append(f"{field}: expected {expected!r}, got {actual!r}")


def run_nl_fixture(path: Path, tracer=None, measure_latency: bool = False) -> tuple[bool, float]:
    """Run a single NL parser fixture. Returns (passed, latency_s)."""
    fixture = json.loads(path.read_text())
    fid = fixture["id"]
    desc = fixture["description"]
    text = fixture["input"]
    expected_fields = fixture["expected_fields"]

    from agents.rmd.core.parser import parse

    t0 = time.monotonic()
    if tracer:
        with tracer.start_as_current_span(f"nl-parse:{fid}") as span:
            span.set_attribute("fixture.id", fid)
            span.set_attribute("fixture.input", text)
            result = parse(text)
            span.set_attribute("parser.output", json.dumps(result))
    else:
        result = parse(text)
    latency_s = time.monotonic() - t0

    errors = []
    for field, expected in expected_fields.items():
        _check_field(errors, field, expected, result)

    passed = not errors
    status = PASS if passed else FAIL
    latency_str = f"  {latency_s:.2f}s" if measure_latency else ""
    print(f"  {status}  [{fid}] {desc}{latency_str}")
    for e in errors:
        print(f"         {e}")
    if not errors:
        print(f"         extracted: {json.dumps(result, ensure_ascii=False)}")

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
    threshold = 30.0
    if p95 > threshold:
        print(f"\n  \033[91mWARN\033[0m  p95 {p95:.2f}s exceeds threshold {threshold}s")
    else:
        print(f"\n  \033[92mOK\033[0m    p95 {p95:.2f}s within threshold {threshold}s")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", action="store_true", help="Send traces to Phoenix ontology-eval project")
    parser.add_argument("--latency", action="store_true", help="Show per-fixture latency and p95 report")
    args = parser.parse_args()

    tracer = None
    if args.trace:
        if _setup_tracing():
            from opentelemetry import trace
            tracer = trace.get_tracer("rmd-parser-tests")

    fixtures = sorted(PROMPTS_DIR.glob("nl-*.json"))
    if not fixtures:
        print("No NL fixtures found (expected nl-*.json in prompts/)")
        sys.exit(1)

    print(f"\nRunning {len(fixtures)} NL parser fixtures\n")
    results = [run_nl_fixture(f, tracer=tracer, measure_latency=args.latency) for f in fixtures]

    passed = sum(r[0] for r in results)
    latencies = [r[1] for r in results]
    total = len(results)
    print(f"\n{passed}/{total} passed")

    if args.latency:
        _print_latency_report(latencies)

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
