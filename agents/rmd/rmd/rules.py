"""RMD Agent safety rules.

Pre-check: runs before the Strands agent — blocks on missing required data.
Post-check: runs after the agent — overrides any unsafe or incoherent result.

These are deterministic Python guards that cannot be bypassed by the model.
The pattern mirrors the existing roth/ and tlh/ agents in Documents/agent/.
"""

from __future__ import annotations

REQUIRED_FIELDS = ["date_of_birth", "account_type", "prior_year_end_balance"]

VALID_WITHDRAWAL_STATUSES = {"Not Started", "In Progress", "Completed", "Not Applicable", "Manual Review Required"}


# ---------------------------------------------------------------------------
# Pre-check
# ---------------------------------------------------------------------------

def pre_check(client: dict) -> dict | None:
    """Run before the agent pipeline.

    Returns a no_action result dict if data is insufficient, else None.
    Never blocks on optional fields (market_value, available_cash, rmd_amount).
    """
    missing = [f for f in REQUIRED_FIELDS if client.get(f) is None]
    if missing:
        return {
            "decision": "insufficient_data",
            "reason": (
                f"Required fields are missing: {', '.join(missing)}. "
                "Cannot determine RMD status without date of birth, account type, "
                "and prior year-end balance."
            ),
            "missing_fields": missing,
            "confidence": "high",
            "_source": "pre_check:missing_required_fields",
        }
    return None


# ---------------------------------------------------------------------------
# Post-check
# ---------------------------------------------------------------------------

def post_check(agent_result: dict) -> dict:
    """Run after the agent pipeline.

    Overrides the result if:
    - withdrawal_status is not a recognised value
    - eligible=True but rmd_required_amount is missing or zero
    - eligible=True but withdrawal_status is missing
    - The result dict is malformed / empty

    These are safety nets; well-formed agent output passes straight through.
    """
    # Guard: malformed result
    if not isinstance(agent_result, dict) or not agent_result:
        return {
            "decision": "error",
            "reason": "Agent returned an empty or malformed result. Defaulting to insufficient data.",
            "confidence": "low",
            "_source": "post_check:malformed_result",
        }

    eligible = agent_result.get("eligible")
    status = agent_result.get("withdrawal_status")
    rmd_amount = agent_result.get("rmd_required_amount")
    decision = agent_result.get("decision")

    # Pass-through: manual_review decisions (e.g. Inherited IRA) are valid as-is
    if decision == "manual_review":
        return agent_result

    # Guard: eligible account but no RMD amount computed
    if eligible is True and (rmd_amount is None or rmd_amount == 0):
        return {
            **agent_result,
            "decision": "error",
            "reason": "Account is RMD-eligible but no required amount was computed. Review prior year-end balance.",
            "confidence": "low",
            "_source": "post_check:missing_rmd_amount",
        }

    # Guard: unrecognised withdrawal status
    if status not in VALID_WITHDRAWAL_STATUSES:
        return {
            **agent_result,
            "decision": "error",
            "reason": f"Unrecognised withdrawal status '{status}'. Cannot report RMD status safely.",
            "confidence": "low",
            "_source": "post_check:invalid_status",
        }

    # Guard: ineligible account should not have an RMD amount
    if eligible is False and rmd_amount is not None:
        return {
            **agent_result,
            "decision": "error",
            "reason": "Account is not RMD-eligible but an RMD amount was returned. Review account type and eligibility logic.",
            "confidence": "low",
            "_source": "post_check:ineligible_with_amount",
        }

    # Guard: completed withdrawal should have zero remaining
    remaining = agent_result.get("remaining_rmd")
    if status == "Completed" and remaining is not None and remaining > 0:
        return {
            **agent_result,
            "decision": "error",
            "reason": f"Withdrawal status is 'Completed' but remaining_rmd is {remaining}. Review withdrawal amounts.",
            "confidence": "low",
            "_source": "post_check:completed_with_remaining",
        }

    agent_result["_source"] = agent_result.get("_source", "agent")
    return agent_result
