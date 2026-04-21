"""RMD Eligibility sub-agent (Strands Agents SDK).

Pipeline: get_client_data → compute_rmd → format result
          pre_check (Python) wraps the agent for safety
          post_check (Python) validates the result before returning

Same pattern as Documents/agent/roth/agent.py.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from strands import Agent
from strands.models.anthropic import AnthropicModel

from .rules import post_check, pre_check
from .tools import DISTRIBUTION_YEAR, build_tools

_PROMPT_FILE = Path(__file__).parent / "prompts" / "system_prompt.md"
SYSTEM_PROMPT = _PROMPT_FILE.read_text().replace("{distribution_year}", str(DISTRIBUTION_YEAR))


def _model() -> AnthropicModel:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set")
    return AnthropicModel(
        model_id="claude-haiku-4-5-20251001",
        max_tokens=1024,
        params={"temperature": 0},
        client_args={"api_key": api_key},
    )


def run_rmd_agent(auth_token: str, account_id: str, client_input: dict) -> dict:
    """Build and invoke the Strands agent. Returns raw result dict."""
    get_client_data, compute_rmd = build_tools(auth_token, account_id, client_input)

    agent = Agent(
        model=_model(),
        tools=[get_client_data, compute_rmd],
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,
    )

    result = agent(
        f"Retrieve the client data for account {account_id} and compute their RMD status for {DISTRIBUTION_YEAR}."
    )

    raw = str(result).strip()
    # Extract JSON from optional markdown code fence
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start:end + 1]
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {
            "eligible": None,
            "withdrawal_status": "Unknown",
            "reason": f"Agent returned unparseable output: {raw[:500]}",
            "flags": ["Agent output could not be parsed — manual review required."],
            "_source": "agent:parse_error",
        }


def evaluate(auth_token: str, account_id: str, client_input: dict | None = None) -> dict:
    """Full RMD pipeline: pre_check → agent → post_check.

    Args:
        auth_token: Farther API auth token.
        account_id: farther_virtual_account_id or 'manual-input'.
        client_input: Human-supplied field overrides — highest priority.

    Returns:
        dict with eligible, withdrawal_status, rmd_required_amount,
        remaining_rmd, flags, _source.
    """
    client_input = client_input or {}

    # Pre-check: only runs when all data is from human input (no DB lookup possible)
    # Skipped when an account_id is provided — required fields may come from the DB
    if not account_id or account_id == "manual-input":
        early_exit = pre_check(client_input)
        if early_exit:
            return early_exit

    raw = run_rmd_agent(auth_token, account_id, client_input)

    # Agent signalled missing data — surface it cleanly
    if raw.get("decision") == "missing_data":
        return {
            **raw,
            "_source": "agent:missing_data",
        }

    return post_check(raw)
