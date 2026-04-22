"""CLI entry point for the RMD agent.

Usage (via Makefile):
  make run ACCOUNT_ID=38279295
  make run ACCOUNT_ID=38279295 BALANCE=320000 YTD=10000
  make run-manual DOB=1950-03-15 TYPE="Traditional IRA" BALANCE=320000 YTD=10000
  make run-nl TEXT="John Smith, DOB March 15 1950, Traditional IRA, balance 320k, took out 10k"

Direct usage:
  uv run python agent.py <account_id> [--balance N] [--ytd N] [--cash N]
  uv run python agent.py --manual --dob YYYY-MM-DD --account-type TYPE --balance N --ytd N
  uv run python agent.py --nl "free text advisor input"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import core as rmd
from core.parser import parse


def _get_auth_token() -> str:
    return os.environ.get("FARTHER_AUTH_TOKEN", "")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RMD agent for a client account.")
    parser.add_argument("account_id", nargs="?", default="manual-input", help="Farther virtual account ID")
    parser.add_argument("--balance", type=float, default=None, help="Prior year-end balance override")
    parser.add_argument("--ytd", type=float, default=None, help="YTD withdrawals override")
    parser.add_argument("--cash", type=float, default=None, help="Available cash override")
    # Manual input flags
    parser.add_argument("--manual", action="store_true", help="Use manual input only — no DB lookup")
    parser.add_argument("--dob", default=None, help="Date of birth (YYYY-MM-DD)")
    parser.add_argument("--account-type", default=None, help="Account type (e.g. 'Traditional IRA')")
    # NL input flag
    parser.add_argument("--nl", default=None, help="Free-text advisor input — parsed by LLM before evaluate()")

    args = parser.parse_args()

    auth_token = _get_auth_token()

    # NL path: parse free text → client_input → evaluate()
    if args.nl:
        print(f"[parser] extracting fields from: {args.nl!r}")
        client_input = parse(args.nl)
        print(f"[parser] extracted: {json.dumps(client_input, indent=2)}")
        result = rmd.evaluate(auth_token, "manual-input", client_input)
        print(json.dumps(result, indent=2))
        return

    client_input: dict = {}
    if args.balance is not None:
        client_input["prior_year_end_balance"] = args.balance
    if args.ytd is not None:
        client_input["withdrawal_amount_ytd"] = args.ytd
    if args.cash is not None:
        client_input["available_cash"] = args.cash

    account_id = "manual-input" if args.manual else args.account_id

    if args.manual:
        if not args.dob:
            print("Error: --dob required for --manual mode", file=sys.stderr)
            sys.exit(1)
        if not args.account_type:
            print("Error: --account-type required for --manual mode", file=sys.stderr)
            sys.exit(1)
        client_input["date_of_birth"] = args.dob
        client_input["account_type"] = args.account_type

    result = rmd.evaluate(auth_token, account_id, client_input)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
