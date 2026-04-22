"""RMD sub-agent — deterministic Python pipeline (P15).

No LLM in the main path. Pipeline:
    pre_check → get_client_data → compute_rmd → post_check

LLM lives at the integration layer only (Step 2).
"""

from __future__ import annotations

import logging

from .rules import OUTPUT_SCHEMA, post_check, pre_check
from .tools import DISTRIBUTION_YEAR, compute_rmd, get_client_data

logger = logging.getLogger(__name__)


def evaluate(auth_token: str, account_id: str, client_input: dict | None = None) -> dict:
    """Full RMD pipeline: pre_check → get_client_data → compute_rmd → post_check.

    Args:
        auth_token: Farther API auth token.
        account_id: farther_virtual_account_id or 'manual-input'.
        client_input: Human-supplied field overrides — highest priority.

    Returns:
        dict matching OUTPUT_SCHEMA with all keys always present.
    """
    client_input = client_input or {}

    # Pre-check: only runs on manual-input path (no DB lookup possible)
    if not account_id or account_id == "manual-input":
        early_exit = pre_check(client_input)
        if early_exit:
            return early_exit

    # Step 1: fetch and merge client data (pure Python, no LLM)
    data = get_client_data(auth_token, account_id, client_input)

    # Missing required fields — surface as INSUFFICIENT_DATA
    if data.get("_missing"):
        return {
            **OUTPUT_SCHEMA,
            "decision": "INSUFFICIENT_DATA",
            "missing_fields": data["_missing"],
            "reason": f"I need one piece of information to continue: {data['_missing'][0]}.",
            "data_quality": data.get("data_quality", []),
            "client_name": data.get("client_name"),
            "advisor_name": data.get("advisor_name"),
            "_source": "pre_check:missing_fields",
            "completeness": "minimal",
        }

    # Step 2: compute RMD (pure Python, no LLM)
    result = compute_rmd(
        date_of_birth=data["date_of_birth"],
        account_type=data["account_type"],
        prior_year_end_balance=data["prior_year_end_balance"],
        withdrawal_amount_ytd=data.get("withdrawal_amount_ytd") or 0.0,
        market_value=data.get("market_value"),
        available_cash=data.get("available_cash"),
    )

    # Merge data provenance into result
    result["data_quality"] = data.get("data_quality", [])
    result["client_name"] = data.get("client_name")
    result["advisor_name"] = data.get("advisor_name")
    result["account_id"] = account_id
    if "_source" not in result or result.get("_source") is None:
        result["_source"] = data.get("_source", "unknown")

    # Step 3: enforce output schema and coherence guards
    return post_check(result)
