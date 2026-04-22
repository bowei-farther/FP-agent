"""RMD Eligibility sub-agent (Strands Agents SDK).

Pipeline: get_client_data → compute_rmd → format result
          pre_check (Python) wraps the agent for safety
          post_check (Python) validates the result before returning
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from strands import Agent
from strands.models.bedrock import BedrockModel

from .rules import OUTPUT_SCHEMA, post_check, pre_check
from .tools import DISTRIBUTION_YEAR, build_tools

logger = logging.getLogger(__name__)

_PROMPT_FILE = Path(__file__).parent / "prompts" / "system_prompt.md"
SYSTEM_PROMPT = _PROMPT_FILE.read_text().replace("{distribution_year}", str(DISTRIBUTION_YEAR))


def _model() -> BedrockModel:
    return BedrockModel(
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        max_tokens=1024,
        temperature=0,
    )


def _parse_json(raw: str) -> dict | None:
    """Extract and parse the first JSON object from a string.

    Handles optional markdown code fences (```json ... ```).
    Returns None if no valid JSON object is found.
    """
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(raw[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None


def run_rmd_agent(auth_token: str, account_id: str, client_input: dict) -> dict:
    """Build and invoke the Strands agent. Returns raw result dict.

    Retries JSON extraction up to 3 times on parse failure (Task 5).
    Returns an ERROR result dict if all attempts fail.
    """
    get_client_data, compute_rmd = build_tools(auth_token, account_id, client_input)

    agent = Agent(
        model=_model(),
        tools=[get_client_data, compute_rmd],
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,
    )

    prompt = (
        f"Retrieve the client data for account {account_id} "
        f"and compute their RMD status for {DISTRIBUTION_YEAR}."
    )

    last_raw = ""
    for attempt in range(1, 4):
        try:
            result = agent(prompt)
            raw = str(result).strip()
            parsed = _parse_json(raw)
            if parsed is not None:
                return parsed
            last_raw = raw
            logger.warning("[rmd_agent] parse attempt %d failed — retrying. Output: %.200s", attempt, raw)
        except Exception as exc:
            logger.warning("[rmd_agent] agent call attempt %d raised %s: %s", attempt, type(exc).__name__, exc)
            last_raw = str(exc)

    return {
        **OUTPUT_SCHEMA,
        "decision": "ERROR",
        "reason": f"Agent returned unparseable output after 3 attempts.",
        "flags": ["Agent output could not be parsed — manual review required."],
        "_source": "agent:parse_error",
        "input_echo": {"last_raw": last_raw[:500]},
    }


def evaluate(auth_token: str, account_id: str, client_input: dict | None = None) -> dict:
    """Full RMD pipeline: pre_check → agent → post_check.

    Args:
        auth_token: Farther API auth token.
        account_id: farther_virtual_account_id or 'manual-input'.
        client_input: Human-supplied field overrides — highest priority.

    Returns:
        dict matching OUTPUT_SCHEMA with all keys always present.
    """
    client_input = client_input or {}

    # Pre-check: only runs when no DB lookup is possible (manual input path)
    if not account_id or account_id == "manual-input":
        early_exit = pre_check(client_input)
        if early_exit:
            return early_exit

    raw = run_rmd_agent(auth_token, account_id, client_input)

    # Agent signalled INSUFFICIENT_DATA — surface it with full schema
    if raw.get("decision") == "INSUFFICIENT_DATA":
        return {**OUTPUT_SCHEMA, **raw, "_source": raw.get("_source", "agent:insufficient_data")}

    return post_check(raw)
