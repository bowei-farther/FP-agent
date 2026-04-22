"""RMD Agent safety rules.

Pre-check: runs before the Strands agent — blocks on missing required data.
Post-check: runs after the agent — enforces schema, overrides any unsafe result.

These are deterministic Python guards that cannot be bypassed by the model.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Output schema (P3) — every key always present in the return dict.
# post_check merges agent output over this so no key is ever missing.
# ---------------------------------------------------------------------------

OUTPUT_SCHEMA: dict = {
    "eligible":               None,
    "reason":                 None,
    "age":                    None,
    "rmd_required_amount":    None,
    "withdrawal_amount_ytd":  0.0,
    "remaining_rmd":          None,
    "withdrawal_status":      "Not Applicable",
    "available_cash":         None,
    "market_value":           None,
    "cash_covers_remaining":  None,
    "flags":                  [],
    "client_name":            None,
    "advisor_name":           None,
    "_source":                "unknown",
    "decision":               "INSUFFICIENT_DATA",
    "missing_fields":         [],
    "data_quality":           [],
    "completeness":           "minimal",
    "input_echo":             {},
}

# Fields that, when present in data_quality[], indicate imperfect provenance.
# Used by post_check to downgrade completeness from "full".
_DQ_IMPERFECT = frozenset({
    "USING_LATEST_BALANCE_AS_PROXY",  # latest balance used instead of Dec 31 snapshot
    "USER_PROVIDED_BALANCE",           # advisor provided balance — not from ontology
    "USER_PROVIDED_WITHDRAWAL_YTD",    # advisor provided YTD — no transaction history in ontology
})

REQUIRED_FIELDS = ["date_of_birth", "account_type", "prior_year_end_balance"]

VALID_WITHDRAWAL_STATUSES = {
    "Not Started", "In Progress", "Completed", "Not Applicable",
    "Manual Review Required",
    "Not Required",   # inherited IRA 10-year rule — no annual RMD
    "Final Year",     # inherited IRA 10-year rule — last year, must empty
    "Overdue",        # inherited IRA 10-year rule — deadline passed
}

VALID_DECISIONS = frozenset({
    "TAKE_RMD_NOW", "RMD_IN_PROGRESS", "RMD_PENDING", "RMD_COMPLETE",
    "NO_ACTION", "MANUAL_REVIEW", "INSUFFICIENT_DATA", "INVALID_INPUT", "ERROR",
})

# Input fields captured in input_echo for auditability (P4).
_INPUT_ECHO_FIELDS = [
    "date_of_birth", "account_type", "prior_year_end_balance",
    "withdrawal_amount_ytd", "market_value", "available_cash",
    "age", "rmd_required_amount",
]


# ---------------------------------------------------------------------------
# Pre-check
# ---------------------------------------------------------------------------

def pre_check(client: dict) -> dict | None:
    """Run before the agent pipeline.

    Returns an INSUFFICIENT_DATA result dict if required fields are missing,
    else None. Never blocks on optional fields.
    """
    missing = [f for f in REQUIRED_FIELDS if client.get(f) is None]
    if missing:
        return {
            **OUTPUT_SCHEMA,
            "decision": "INSUFFICIENT_DATA",
            "reason": (
                f"Required fields are missing: {', '.join(missing)}. "
                "Cannot determine RMD status without date of birth, account type, "
                "and prior year-end balance."
            ),
            "missing_fields": missing,
            "_source": "pre_check:missing_required_fields",
        }
    return None


# ---------------------------------------------------------------------------
# Post-check
# ---------------------------------------------------------------------------

def post_check(agent_result: dict) -> dict:
    """Run after the agent pipeline.

    Steps (in order):
    1. Merge over OUTPUT_SCHEMA so all keys are always present.
    2. Override decision to ERROR for structurally incoherent results.
    3. Compute completeness from data_quality[].
    4. Build input_echo from fields used in the calculation.
    5. Pass through valid MANUAL_REVIEW and INSUFFICIENT_DATA without further checks.
    """
    # Step 1: guarantee all keys present (P3)
    if not isinstance(agent_result, dict) or not agent_result:
        return {
            **OUTPUT_SCHEMA,
            "decision": "ERROR",
            "reason": "Agent returned an empty or malformed result.",
            "_source": "post_check:malformed_result",
        }

    result = {**OUTPUT_SCHEMA, **agent_result}

    eligible = result.get("eligible")
    status   = result.get("withdrawal_status")
    rmd_amt  = result.get("rmd_required_amount")
    decision = result.get("decision", "INSUFFICIENT_DATA")
    remaining = result.get("remaining_rmd")

    # Step 2: structural coherence guards
    # Pass-through: terminal decisions that are always valid as-is
    if decision in ("MANUAL_REVIEW", "INSUFFICIENT_DATA", "INVALID_INPUT"):
        _fill_computed(result)
        return result

    # Guard: unrecognised decision value
    if decision not in VALID_DECISIONS:
        result["decision"] = "ERROR"
        result["reason"] = f"Unrecognised decision value '{decision}'."
        result["_source"] = "post_check:invalid_decision"

    # Guard: eligible account but RMD amount missing (None means compute failed)
    # rmd_amount=0.0 is valid — zero balance produces zero RMD
    # rmd_amount=None is valid for inherited IRA 10-year rule (no annual amount required)
    elif eligible is True and rmd_amt is None and result.get("_inherited_rule") != "10-year":
        result["decision"] = "ERROR"
        result["reason"] = "Account is RMD-eligible but no required amount was computed. Review prior year-end balance."
        result["_source"] = "post_check:missing_rmd_amount"

    # Guard: unrecognised withdrawal status
    elif status not in VALID_WITHDRAWAL_STATUSES:
        result["decision"] = "ERROR"
        result["reason"] = f"Unrecognised withdrawal status '{status}'."
        result["_source"] = "post_check:invalid_status"

    # Guard: ineligible account should not have an RMD amount
    elif eligible is False and rmd_amt is not None:
        result["decision"] = "ERROR"
        result["reason"] = "Account is not RMD-eligible but an RMD amount was returned."
        result["_source"] = "post_check:ineligible_with_amount"

    # Guard: completed withdrawal should have zero remaining
    elif status == "Completed" and remaining is not None and remaining > 0:
        result["decision"] = "ERROR"
        result["reason"] = f"Withdrawal status is 'Completed' but remaining_rmd is {remaining}."
        result["_source"] = "post_check:completed_with_remaining"

    _fill_computed(result)
    return result


def _fill_computed(result: dict) -> None:
    """Compute completeness and input_echo in-place (P4, Task 4 and 6)."""
    dq: list[str] = result.get("data_quality") or []

    # completeness: full if all required fields resolved without imperfect proxies
    # partial if a proxy was used or optional fields missing
    # minimal if required fields are missing
    missing = result.get("missing_fields") or []
    if missing:
        result["completeness"] = "minimal"
    elif any(flag in _DQ_IMPERFECT for flag in dq):
        result["completeness"] = "partial"
    else:
        result["completeness"] = "full"

    # input_echo: exact field values used in the computation
    result["input_echo"] = {
        k: result[k]
        for k in _INPUT_ECHO_FIELDS
        if result.get(k) is not None
    }
