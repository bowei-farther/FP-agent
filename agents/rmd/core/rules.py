"""RMD Agent safety rules.

Pre-check: runs before the pipeline — blocks on missing required data.
Post-check: runs after the pipeline — enforces schema, overrides any unsafe result.

These are deterministic Python guards that cannot be bypassed by the model.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Output schema — every key always present in the return dict.
# post_check merges agent output over this so no key is ever missing.
#
# Fields kept for the reasoning layer (LLM at integration layer):
#   decision, eligible, withdrawal_status, rmd_required_amount, remaining_rmd,
#   withdrawal_amount_ytd, age, flags, reason, missing_fields, completeness,
#   client_name, data_quality, available_cash, cash_covers_remaining,
#   inherited_rule
#
# Fields removed:
#   _source        — internal debug only
#   input_echo     — redundant, caller already has the input
#   market_value   — never used in reasoning or decisions
# ---------------------------------------------------------------------------

OUTPUT_SCHEMA: dict = {
    "decision":               "INSUFFICIENT_DATA",
    "eligible":               None,
    "reason":                 None,
    "age":                    None,
    "rmd_required_amount":    None,
    "withdrawal_amount_ytd":  0.0,
    "remaining_rmd":          None,
    "withdrawal_status":      "Not Applicable",
    "available_cash":         None,
    "cash_covers_remaining":  None,
    "flags":                  [],
    "client_name":            None,
    "missing_fields":         [],
    "data_quality":           [],
    "completeness":           "minimal",
    "inherited_rule":         None,   # "10-year" | "stretch" | None
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


# ---------------------------------------------------------------------------
# Pre-check
# ---------------------------------------------------------------------------

def pre_check(client: dict) -> dict | None:
    """Run before the pipeline.

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
        }
    return None


# ---------------------------------------------------------------------------
# Post-check
# ---------------------------------------------------------------------------

def post_check(agent_result: dict) -> dict:
    """Run after the pipeline.

    Steps (in order):
    1. Merge over OUTPUT_SCHEMA so all keys are always present.
    2. Override decision to ERROR for structurally incoherent results.
    3. Compute completeness from data_quality[].
    4. Pass through valid MANUAL_REVIEW and INSUFFICIENT_DATA without further checks.
    """
    # Step 1: guarantee all keys present
    if not isinstance(agent_result, dict) or not agent_result:
        return {
            **OUTPUT_SCHEMA,
            "decision": "ERROR",
            "reason": "Agent returned an empty or malformed result.",
        }

    result = {**OUTPUT_SCHEMA, **agent_result}

    # Rename _inherited_rule → inherited_rule if coming from compute_rmd
    if "_inherited_rule" in result:
        result["inherited_rule"] = result.pop("_inherited_rule")

    # Remove fields that should never be in output
    for key in ("_source", "input_echo", "market_value", "account_id"):
        result.pop(key, None)

    eligible  = result.get("eligible")
    status    = result.get("withdrawal_status")
    rmd_amt   = result.get("rmd_required_amount")
    decision  = result.get("decision", "INSUFFICIENT_DATA")
    remaining = result.get("remaining_rmd")

    # Step 2: structural coherence guards
    # Pass-through: terminal decisions that are always valid as-is
    if decision in ("MANUAL_REVIEW", "INSUFFICIENT_DATA", "INVALID_INPUT"):
        _fill_completeness(result)
        return result

    # Guard: unrecognised decision value
    if decision not in VALID_DECISIONS:
        result["decision"] = "ERROR"
        result["reason"] = f"Unrecognised decision value '{decision}'."

    # Guard: eligible account but RMD amount missing
    # rmd_amount=0.0 is valid — zero balance produces zero RMD
    # rmd_amount=None is valid for inherited IRA 10-year rule (no annual amount)
    elif eligible is True and rmd_amt is None and result.get("inherited_rule") != "10-year":
        result["decision"] = "ERROR"
        result["reason"] = "Account is RMD-eligible but no required amount was computed. Review prior year-end balance."

    # Guard: unrecognised withdrawal status
    elif status not in VALID_WITHDRAWAL_STATUSES:
        result["decision"] = "ERROR"
        result["reason"] = f"Unrecognised withdrawal status '{status}'."

    # Guard: ineligible account should not have an RMD amount
    elif eligible is False and rmd_amt is not None:
        result["decision"] = "ERROR"
        result["reason"] = "Account is not RMD-eligible but an RMD amount was returned."

    # Guard: completed withdrawal should have zero remaining
    elif status == "Completed" and remaining is not None and remaining > 0:
        result["decision"] = "ERROR"
        result["reason"] = f"Withdrawal status is 'Completed' but remaining_rmd is {remaining}."

    _fill_completeness(result)
    return result


def _fill_completeness(result: dict) -> None:
    """Compute completeness in-place."""
    dq: list[str] = result.get("data_quality") or []
    missing = result.get("missing_fields") or []
    if missing:
        result["completeness"] = "minimal"
    elif any(flag in _DQ_IMPERFECT for flag in dq):
        result["completeness"] = "partial"
    else:
        result["completeness"] = "full"
