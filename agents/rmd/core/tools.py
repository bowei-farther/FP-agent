"""RMD Agent tools.

Two @tool functions used by the Strands agent:
  - get_client_data   : merges human input → database → returns what's available
  - compute_rmd       : applies IRS rules, returns RMD status

Priority for every field:
  1. Human input (client_input dict passed at evaluation time)
  2. Database (Farther ontology + daily API)
  3. If required fields still missing → returns _missing list so agent can ask back
"""

from __future__ import annotations

from datetime import date
from typing import Any

import logging

import requests
from strands import tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DISTRIBUTION_YEAR = 2026
RMD_MIN_AGE = 73

# IRS Uniform Lifetime Table (2022 revision, effective 2023+)
IRS_FACTORS: dict[int, float] = {
    73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9,
    78: 22.0, 79: 21.1, 80: 20.2, 81: 19.4, 82: 18.5,
    83: 17.7, 84: 16.8, 85: 16.0, 86: 15.2, 87: 14.4,
    88: 13.7, 89: 12.9, 90: 12.2, 91: 11.5, 92: 10.8,
    93: 10.1, 94: 9.5,  95: 8.9,  96: 8.4,  97: 7.8,
    98: 7.3,  99: 6.8, 100: 6.4,
}

RMD_ELIGIBLE_ACCOUNT_TYPES: frozenset[str] = frozenset({
    t.lower() for t in {
        # Farther ontology strings (confirmed via live query)
        "Traditional IRA",
        "SEP IRA",
        "Rollover IRA",
        "Employer Retirement Plan",   # ontology label for 401k/403b/457b plans
        # Common aliases
        "TRAD_IRA", "Trad IRA",
        "SEP-IRA", "SEP_IRA",
        "SIMPLE IRA", "SIMPLE_IRA",
        "401(k)", "401k",
        "403(b)", "403b",
        "457(b)", "457",
        "ROLLOVER_IRA",
    }
})

# Inherited IRAs have different rules (10-year rule for non-spouse beneficiaries).
# Listed separately so compute_rmd can return MANUAL_REVIEW rather than silently
# applying the standard Uniform Lifetime Table.
INHERITED_IRA_ACCOUNT_TYPES: frozenset[str] = frozenset({
    t.lower() for t in {"Inherited IRA", "INHERITED_IRA"}
})

ROTH_ACCOUNT_TYPES: frozenset[str] = frozenset({
    t.lower() for t in {
        "Roth IRA", "ROTH_IRA", "Roth",
        "Roth 401(k)", "Roth 401k",
        "Roth 403(b)", "Roth 403b",
    }
})

REQUIRED_FIELDS = ["date_of_birth", "account_type", "prior_year_end_balance"]

_BASE_URL = "https://ontology.dev.datalake.finops-data.na.farther.com"

# ---------------------------------------------------------------------------
# data_quality constants (P7 — system-facing provenance, never mixed with flags)
# Used by get_client_data() to record how each field was resolved.
# post_check() uses these to compute completeness.
# ---------------------------------------------------------------------------

DQ_USING_LATEST_BALANCE_AS_PROXY = "USING_LATEST_BALANCE_AS_PROXY"
DQ_USER_PROVIDED_BALANCE         = "USER_PROVIDED_BALANCE"
DQ_USER_PROVIDED_WITHDRAWAL_YTD  = "USER_PROVIDED_WITHDRAWAL_YTD"
DQ_DOB_FROM_DB                   = "DOB_FROM_DB"
DQ_DOB_FROM_INPUT                = "DOB_FROM_INPUT"
DQ_ACCOUNT_TYPE_FROM_DB          = "ACCOUNT_TYPE_FROM_DB"
DQ_ACCOUNT_TYPE_FROM_INPUT       = "ACCOUNT_TYPE_FROM_INPUT"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _age_as_of_dec31(dob: str, year: int = DISTRIBUTION_YEAR) -> int | None:
    """Return client age as of December 31 of the distribution year, or None if dob is invalid."""
    try:
        birth = date.fromisoformat(dob)
    except (ValueError, TypeError):
        return None
    return year - birth.year


def _irs_factor(age: int) -> float | None:
    return IRS_FACTORS.get(min(age, 100))


def _fetch_object(auth_token: str, account_id: str) -> dict:
    """Fetch account object fields from the ontology API.

    Tries farther_virtual_account_id first, then custodian_account_id as fallback.
    """
    fields = [
        "account_type", "date_of_birth",
        "farther_virtual_account_id", "id_object",
        "first_name", "last_name", "manager",
    ]
    for filter_key in ("farther_virtual_account_id", "custodian_account_id"):
        try:
            resp = requests.post(
                _BASE_URL + "/v1/object/search-by-fields",
                headers={"Authorization": auth_token},
                json={
                    "filters": {
                        "object_type": "Account",
                        filter_key: account_id,
                    },
                    "fields": fields,
                },
                params={"page": 1, "page_size": 1},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if data:
                return data[0]
        except requests.HTTPError as e:
            logger.warning("[object fetch] HTTP %s for account_id=%s (%s): %s", e.response.status_code, account_id, filter_key, e)
        except Exception as e:
            logger.warning("[object fetch] Unexpected error for account_id=%s (%s): %s", account_id, filter_key, e)
    return {}


def _fetch_daily(auth_token: str, id_object: int) -> dict:
    """Fetch latest daily values from the ontology.

    Fields that exist in the ontology daily data:
      account_balance       (277)  → prior_year_end_balance_db (best available proxy for Dec 31 value)
      account_market_value  (1303) → market_value (positions only, excl. cash)
      account_available_cash (1301) → available_cash
      account_settled_cash  (1302) → settled_cash
      account_sweep_balance (1436) → sweep_balance

    Fields NOT in the ontology (must come from human input):
      prior_year_end_balance as of exactly Dec 31 — account_balance is latest, not Dec 31 snapshot
      withdrawal_amount_ytd  — no transaction history in ontology
    """
    fields = {
        "account_balance":        "prior_year_end_balance_db",
        "account_market_value":   "market_value",
        "account_available_cash": "available_cash",
        "account_settled_cash":   "settled_cash",
        "account_sweep_balance":  "sweep_balance",
    }
    result: dict[str, Any] = {}
    for api_field, key in fields.items():
        try:
            r = requests.post(
                _BASE_URL + "/v1/daily/latest",
                headers={"Authorization": auth_token},
                json={"id_object": [id_object], "daily_field_identifier": [api_field]},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json().get("data", [])
            if data:
                result[key] = data[-1]["am_val"]
        except Exception as e:
            logger.warning("[daily fetch] %s failed for id_object=%s: %s", api_field, id_object, e)
    return result


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------

def build_tools(auth_token: str, account_id: str, client_input: dict) -> tuple[Any, Any]:
    """Return (get_client_data, compute_rmd) as Strands tools.

    client_input and auth_token are captured in closures — the agent
    never needs to supply credentials or remember prior input.
    """

    @tool
    def get_client_data() -> dict:
        """Retrieve client data needed for RMD analysis.

        Priority: human input first, then database.
        Returns all available fields plus a _missing list of any
        required fields (date_of_birth, account_type, prior_year_end_balance)
        that could not be resolved from either source.

        Also returns data_quality[] — named constants recording how each
        field was resolved. Used by post_check to compute completeness.

        Returns:
            dict with fields: account_id, account_type, date_of_birth,
            prior_year_end_balance, market_value, available_cash,
            settled_cash, sweep_balance, withdrawal_amount_ytd,
            data_quality, _source, _missing.
        """
        data_quality: list[str] = []

        # Start with human input
        data: dict[str, Any] = {
            "account_id":             account_id,
            "client_name":            client_input.get("client_name"),
            "advisor_name":           client_input.get("advisor_name"),
            "account_type":           client_input.get("account_type"),
            "date_of_birth":          client_input.get("date_of_birth"),
            "prior_year_end_balance": client_input.get("prior_year_end_balance"),
            "market_value":           client_input.get("market_value"),
            "available_cash":         client_input.get("available_cash"),
            "settled_cash":           client_input.get("settled_cash"),
            "sweep_balance":          client_input.get("sweep_balance"),
            "withdrawal_amount_ytd":  client_input.get("withdrawal_amount_ytd"),
        }

        # Record provenance for human-provided fields
        if data["date_of_birth"] is not None:
            data_quality.append(DQ_DOB_FROM_INPUT)
        if data["account_type"] is not None:
            data_quality.append(DQ_ACCOUNT_TYPE_FROM_INPUT)
        if data["prior_year_end_balance"] is not None:
            data_quality.append(DQ_USER_PROVIDED_BALANCE)
        if data["withdrawal_amount_ytd"] is not None:
            data_quality.append(DQ_USER_PROVIDED_WITHDRAWAL_YTD)

        # Fill missing fields from database
        needs_db = any(data.get(f) is None for f in REQUIRED_FIELDS)
        db_source = "none"

        if needs_db and account_id and account_id != "manual-input":
            obj = _fetch_object(auth_token, account_id)
            if obj:
                db_source = "api"
                if data["account_type"] is None:
                    data["account_type"] = obj.get("account_type")
                    if data["account_type"] is not None:
                        data_quality.append(DQ_ACCOUNT_TYPE_FROM_DB)
                if data["date_of_birth"] is None:
                    data["date_of_birth"] = obj.get("date_of_birth")
                    if data["date_of_birth"] is not None:
                        data_quality.append(DQ_DOB_FROM_DB)
                if data["client_name"] is None:
                    first = obj.get("first_name", "")
                    last = obj.get("last_name", "")
                    full = f"{first} {last}".strip()
                    data["client_name"] = full if full else None
                if data["advisor_name"] is None:
                    data["advisor_name"] = obj.get("manager")

                id_object = obj.get("id_object")
                if id_object:
                    daily = _fetch_daily(auth_token, id_object)
                    # prior_year_end_balance: human input wins.
                    # DB value is latest balance, not the Dec 31 snapshot —
                    # use only as fallback and flag it explicitly (P2, P4).
                    if data["prior_year_end_balance"] is None and daily.get("prior_year_end_balance_db") is not None:
                        data["prior_year_end_balance"] = daily["prior_year_end_balance_db"]
                        data_quality.append(DQ_USING_LATEST_BALANCE_AS_PROXY)
                    for key in ["market_value", "available_cash", "settled_cash", "sweep_balance"]:
                        if data.get(key) is None:
                            data[key] = daily.get(key)

        # Determine _source label
        has_input = any(client_input.get(f) for f in REQUIRED_FIELDS)
        if has_input and db_source == "api":
            data["_source"] = "input+api"
        elif has_input:
            data["_source"] = "input"
        elif db_source == "api":
            data["_source"] = "api"
        else:
            data["_source"] = "not_found"

        data["data_quality"] = data_quality
        data["_missing"] = [f for f in REQUIRED_FIELDS if data.get(f) is None]
        return data

    @tool
    def compute_rmd(
        date_of_birth: str,
        account_type: str,
        prior_year_end_balance: float,
        withdrawal_amount_ytd: float = 0.0,
        rmd_amount_stored: float | None = None,
        market_value: float | None = None,
        available_cash: float | None = None,
    ) -> dict:
        """Apply IRS RMD rules and return withdrawal status.

        Uses the IRS Uniform Lifetime Table (2022 revision).
        Distribution year is 2026. Age is calculated as of Dec 31, 2026.

        The `decision` field is always set by this function — never by the LLM (P10).

        Args:
            date_of_birth: ISO date string e.g. '1950-03-15'.
            account_type: e.g. 'Traditional IRA'.
            prior_year_end_balance: Balance as of Dec 31 of prior year.
            withdrawal_amount_ytd: Year-to-date withdrawals (default 0).
            rmd_amount_stored: Pre-calculated RMD from database if available.
            market_value: Current portfolio value (informational).
            available_cash: Uninvested cash (informational).

        Returns:
            dict with: decision, eligible, reason, age, rmd_required_amount,
            withdrawal_amount_ytd, remaining_rmd, withdrawal_status,
            available_cash, market_value, cash_covers_remaining, flags.
        """
        if prior_year_end_balance < 0:
            return {
                "decision": "INSUFFICIENT_DATA",
                "missing_fields": ["prior_year_end_balance"],
                "reason": f"prior_year_end_balance cannot be negative (got {prior_year_end_balance}).",
            }

        age = _age_as_of_dec31(date_of_birth, DISTRIBUTION_YEAR)
        if age is None:
            return {
                "decision": "INSUFFICIENT_DATA",
                "missing_fields": ["date_of_birth"],
                "reason": f"date_of_birth '{date_of_birth}' is not a valid ISO date (expected YYYY-MM-DD).",
            }

        account_type_norm = account_type.strip().lower()

        # Inherited IRA — different RMD rules apply (10-year rule, Life Expectancy method).
        # Cannot be evaluated with the standard Uniform Lifetime Table.
        if account_type_norm in INHERITED_IRA_ACCOUNT_TYPES:
            return {
                "decision": "MANUAL_REVIEW",
                "eligible": None,
                "reason": (
                    "Inherited IRAs are subject to special RMD rules (e.g., 10-year rule for "
                    "non-spouse beneficiaries under SECURE 2.0). Standard Uniform Lifetime Table "
                    "does not apply. Manual review required."
                ),
                "age": age,
                "rmd_required_amount": None,
                "withdrawal_amount_ytd": withdrawal_amount_ytd,
                "remaining_rmd": None,
                "withdrawal_status": "Manual Review Required",
                "available_cash": available_cash,
                "market_value": market_value,
                "cash_covers_remaining": None,
                "flags": ["Inherited IRA — manual review required. Standard RMD table does not apply."],
            }

        # Not eligible: Roth
        if account_type_norm in ROTH_ACCOUNT_TYPES:
            return {
                "decision": "NO_ACTION",
                "eligible": False,
                "reason": "Roth IRA accounts are not subject to RMDs during the owner's lifetime.",
                "age": age,
                "rmd_required_amount": None,
                "withdrawal_amount_ytd": withdrawal_amount_ytd,
                "remaining_rmd": None,
                "withdrawal_status": "Not Applicable",
                "available_cash": available_cash,
                "market_value": market_value,
                "cash_covers_remaining": None,
                "flags": [],
            }

        # Not eligible: under age 73
        if age < RMD_MIN_AGE:
            return {
                "decision": "NO_ACTION",
                "eligible": False,
                "reason": f"Client is age {age} as of Dec 31, {DISTRIBUTION_YEAR}. RMDs begin at age {RMD_MIN_AGE}.",
                "age": age,
                "rmd_required_amount": None,
                "withdrawal_amount_ytd": withdrawal_amount_ytd,
                "remaining_rmd": None,
                "withdrawal_status": "Not Applicable",
                "available_cash": available_cash,
                "market_value": market_value,
                "cash_covers_remaining": None,
                "flags": [],
            }

        # Not eligible: unrecognised account type
        if account_type_norm not in RMD_ELIGIBLE_ACCOUNT_TYPES:
            return {
                "decision": "NO_ACTION",
                "eligible": False,
                "reason": f"Account type '{account_type}' is not subject to RMDs.",
                "age": age,
                "rmd_required_amount": None,
                "withdrawal_amount_ytd": withdrawal_amount_ytd,
                "remaining_rmd": None,
                "withdrawal_status": "Not Applicable",
                "available_cash": available_cash,
                "market_value": market_value,
                "cash_covers_remaining": None,
                "flags": [],
            }

        # RMD amount: use stored if available, else compute from IRS table
        if rmd_amount_stored is not None:
            rmd_amount = round(float(rmd_amount_stored), 2)
        else:
            factor = _irs_factor(age)
            rmd_amount = round(prior_year_end_balance / factor, 2) if factor else 0.0

        ytd = float(withdrawal_amount_ytd or 0)
        remaining = round(max(rmd_amount - ytd, 0.0), 2)

        # Withdrawal status
        if ytd <= 0:
            status = "Not Started"
        elif ytd < rmd_amount:
            status = "In Progress"
        else:
            status = "Completed"

        # Cash coverage
        cash = float(available_cash) if available_cash is not None else None
        cash_covers = (cash >= remaining) if cash is not None and remaining > 0 else None

        # Flags (advisor-facing — P7)
        flags: list[str] = []
        today = date.today()
        days_left = (date(DISTRIBUTION_YEAR, 12, 31) - today).days
        if status != "Completed":
            if days_left < 90:
                flags.append(f"Fewer than 90 days remaining in {DISTRIBUTION_YEAR} — penalty risk if RMD not completed by Dec 31.")
            elif days_left < 180 and status == "Not Started":
                flags.append(f"RMD not started with fewer than 6 months remaining in {DISTRIBUTION_YEAR}.")
        if cash is not None and remaining > 0 and cash < remaining:
            flags.append(f"Available cash (${cash:,.2f}) is insufficient to cover remaining RMD (${remaining:,.2f}) — liquidation may be required.")

        # decision: set by Python from verified field values — never by LLM (P10)
        if status == "Completed":
            decision = "RMD_COMPLETE"
        elif days_left < 90:
            decision = "TAKE_RMD_NOW"
        elif status == "In Progress":
            decision = "RMD_IN_PROGRESS"
        else:
            decision = "RMD_PENDING"

        return {
            "decision": decision,
            "eligible": True,
            "reason": f"Client is age {age} and holds a {account_type}.",
            "age": age,
            "rmd_required_amount": rmd_amount,
            "withdrawal_amount_ytd": ytd,
            "remaining_rmd": remaining,
            "withdrawal_status": status,
            "available_cash": cash,
            "market_value": float(market_value) if market_value is not None else None,
            "cash_covers_remaining": cash_covers,
            "flags": flags,
        }

    return get_client_data, compute_rmd
