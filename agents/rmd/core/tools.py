"""RMD Agent tools.

Two functions called directly by evaluate() — no LLM involved:
  - get_client_data   : merges human input → database → returns what's available
  - compute_rmd       : applies IRS rules, returns RMD status

Priority for every field:
  1. Human input (client_input dict passed at evaluation time)
  2. Database (Farther ontology + daily API)
  3. If required fields still missing → returns _missing list so agent can ask back
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import logging

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DISTRIBUTION_YEAR = date.today().year  # always the current calendar year
RMD_MIN_AGE = 73

# IRS Single Life Expectancy Table (2022 revision) — used for inherited IRAs
# Beneficiary uses their age in the year after the original owner's death,
# then reduces the factor by 1.0 each subsequent year.
IRS_SINGLE_LIFE_FACTORS: dict[int, float] = {
    0: 84.1,  1: 83.1,  2: 82.1,  3: 81.1,  4: 80.2,  5: 79.2,
    6: 78.2,  7: 77.2,  8: 76.3,  9: 75.3, 10: 74.3, 11: 73.3,
   12: 72.3, 13: 71.3, 14: 70.4, 15: 69.4, 16: 68.4, 17: 67.4,
   18: 66.4, 19: 65.4, 20: 64.4, 21: 63.4, 22: 62.4, 23: 61.4,
   24: 60.4, 25: 59.4, 26: 58.4, 27: 57.4, 28: 56.4, 29: 55.4,
   30: 54.4, 31: 53.4, 32: 52.5, 33: 51.5, 34: 50.5, 35: 49.5,
   36: 48.5, 37: 47.5, 38: 46.5, 39: 45.5, 40: 44.6, 41: 43.6,
   42: 42.6, 43: 41.6, 44: 40.6, 45: 39.6, 46: 38.7, 47: 37.7,
   48: 36.7, 49: 35.7, 50: 34.7, 51: 33.8, 52: 32.8, 53: 31.8,
   54: 30.8, 55: 29.9, 56: 28.9, 57: 27.9, 58: 27.0, 59: 26.0,
   60: 25.0, 61: 24.0, 62: 23.1, 63: 22.1, 64: 21.1, 65: 20.2,
   66: 19.2, 67: 18.2, 68: 17.3, 69: 16.3, 70: 15.4, 71: 14.4,
   72: 13.5, 73: 12.5, 74: 11.6, 75: 10.6, 76:  9.7, 77:  8.8,
   78:  7.9, 79:  7.0, 80:  6.1, 81:  5.3, 82:  4.5, 83:  3.7,
   84:  3.0, 85:  2.3, 86:  1.7, 87:  1.1, 88:  0.6, 89:  0.2,
}

SECURE_ACT_DATE = date(2020, 1, 1)  # deaths on/after this date fall under SECURE 2.0

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
    t.lower() for t in {
        "Inherited IRA", "INHERITED_IRA",
        "Designated Beneficiary",   # Fidelity label for inherited IRA accounts
    }
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


def _single_life_factor(age: int) -> float | None:
    """IRS Single Life Expectancy factor for inherited IRA stretch rule."""
    return IRS_SINGLE_LIFE_FACTORS.get(min(age, 89))


def _compute_inherited_rmd(
    beneficiary_dob: str,
    owner_death_date: str,
    is_spouse: bool,
    prior_year_end_balance: float,
    withdrawal_amount_ytd: float,
    available_cash: float | None,
    market_value: float | None,
    _today: date | None,
) -> dict:
    """Compute RMD for inherited IRA when enough information is provided.

    Rules (post-SECURE 2.0, deaths on/after Jan 1 2020):
      - Spouse beneficiary       → stretch rule, Single Life Expectancy Table
      - EDB (minor child, disabled, chronically ill, ≤10 yrs younger than owner,
        or spouse)               → stretch rule, Single Life Expectancy Table
      - Non-EDB (most cases)     → 10-year rule, no annual RMD required,
                                   full balance due by Dec 31 of 10th year after death

    Pre-SECURE (deaths before Jan 1 2020):
      - Stretch rule always applies using Single Life Expectancy Table.

    Returns MANUAL_REVIEW if required fields are invalid or edge case detected.
    """
    today = _today or date.today()

    try:
        ben_birth = date.fromisoformat(beneficiary_dob)
        owner_death = date.fromisoformat(owner_death_date)
    except (ValueError, TypeError):
        return None  # caller will fall back to MANUAL_REVIEW

    ben_age_this_year = DISTRIBUTION_YEAR - ben_birth.year
    years_since_death = DISTRIBUTION_YEAR - owner_death.year
    post_secure = owner_death >= SECURE_ACT_DATE

    ytd = float(withdrawal_amount_ytd or 0)
    cash = float(available_cash) if available_cash is not None else None

    # --- Spouse or pre-SECURE: stretch rule ---
    if is_spouse or not post_secure:
        # Age to use: beneficiary's age in year after owner's death
        age_in_lookup_year = (owner_death.year + 1) - ben_birth.year
        base_factor = _single_life_factor(age_in_lookup_year)
        if base_factor is None:
            return None  # too old for table, caller falls back

        # Reduce factor by 1 for each subsequent year
        years_elapsed = DISTRIBUTION_YEAR - (owner_death.year + 1)
        factor = max(base_factor - years_elapsed, 1.0)

        rmd_amount = _round2(prior_year_end_balance / factor)
        remaining = _round2(max(rmd_amount - ytd, 0.0))

        if rmd_amount == 0 or ytd >= rmd_amount:
            status, decision = "Completed", "RMD_COMPLETE"
        elif ytd <= 0:
            days_left = (date(DISTRIBUTION_YEAR, 12, 31) - today).days
            status = "Not Started"
            decision = "TAKE_RMD_NOW" if days_left < 90 else "RMD_PENDING"
        else:
            days_left = (date(DISTRIBUTION_YEAR, 12, 31) - today).days
            status = "In Progress"
            decision = "TAKE_RMD_NOW" if days_left < 90 else "RMD_IN_PROGRESS"

        rule = "pre-SECURE stretch rule" if not post_secure else "spouse stretch rule"
        return {
            "decision": decision,
            "eligible": True,
            "reason": f"Inherited IRA — {rule} applies (Single Life Expectancy Table, factor {factor:.1f}).",
            "age": ben_age_this_year,
            "rmd_required_amount": rmd_amount,
            "withdrawal_amount_ytd": ytd,
            "remaining_rmd": remaining,
            "withdrawal_status": status,
            "available_cash": cash,
            "market_value": float(market_value) if market_value is not None else None,
            "cash_covers_remaining": (cash >= remaining) if cash is not None and remaining > 0 else None,
            "flags": [f"Inherited IRA ({rule}). Factor {factor:.1f} based on beneficiary age {age_in_lookup_year} at first distribution year."],
            "inherited_rule": "stretch",
        }

    # --- Non-spouse post-SECURE: 10-year rule ---
    deadline_year = owner_death.year + 10
    deadline = date(deadline_year, 12, 31)
    years_remaining = deadline_year - DISTRIBUTION_YEAR

    if years_remaining < 0:
        # Deadline already passed
        decision = "TAKE_RMD_NOW"
        status = "Overdue"
        flags = [f"10-year rule deadline was Dec 31, {deadline_year} — already passed. Penalty may apply."]
    elif years_remaining == 0:
        # Final year — must empty the account
        rmd_amount = _round2(prior_year_end_balance)
        remaining = _round2(max(rmd_amount - ytd, 0.0))
        decision = "TAKE_RMD_NOW" if remaining > 0 else "RMD_COMPLETE"
        status = "Final Year" if remaining > 0 else "Completed"
        flags = [f"10-year rule: final year — full balance must be withdrawn by Dec 31, {deadline_year}."]
        return {
            "decision": decision,
            "eligible": True,
            "reason": f"Inherited IRA — 10-year rule, final year (owner died {owner_death_date}).",
            "age": ben_age_this_year,
            "rmd_required_amount": rmd_amount,
            "withdrawal_amount_ytd": ytd,
            "remaining_rmd": remaining,
            "withdrawal_status": status,
            "available_cash": cash,
            "market_value": float(market_value) if market_value is not None else None,
            "cash_covers_remaining": (cash >= remaining) if cash is not None and remaining > 0 else None,
            "flags": flags,
            "inherited_rule": "10-year",
        }
    else:
        # No annual RMD required — just track deadline
        decision = "RMD_PENDING"
        status = "Not Required"
        flags = [f"10-year rule: no annual RMD required. Full balance due by Dec 31, {deadline_year} ({years_remaining} years remaining)."]

    return {
        "decision": decision,
        "eligible": True,
        "reason": f"Inherited IRA — 10-year rule applies (non-spouse, post-SECURE 2.0, owner died {owner_death_date}).",
        "age": ben_age_this_year,
        "rmd_required_amount": None,
        "withdrawal_amount_ytd": ytd,
        "remaining_rmd": None,
        "withdrawal_status": status,
        "available_cash": cash,
        "market_value": float(market_value) if market_value is not None else None,
        "cash_covers_remaining": None,
        "flags": flags,
        "inherited_rule": "10-year",
    }


def _round2(value: float) -> float:
    """Round to 2 decimal places using ROUND_HALF_UP (standard financial rounding).

    Python's built-in round() uses banker's rounding (round half to even), which
    diverges from IRS/custodian calculations on .5 boundaries (e.g. 16240.835
    rounds to 16240.83 with round() but 16240.84 with ROUND_HALF_UP).
    """
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


_AMBIGUOUS_ACCOUNT_ID = "__ambiguous__"

def _fetch_object(auth_token: str, account_id: str) -> dict:
    """Fetch account object fields from the ontology API.

    Tries farther_virtual_account_id first, then custodian_account_id as fallback.

    Returns {} if not found.
    Returns {"_ambiguous": True} if multiple accounts match the same ID (P12).
    """
    fields = [
        "account_type", "date_of_birth",
        "farther_virtual_account_id", "id_object",
        "first_name", "last_name",
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
                params={"page": 1, "page_size": 2},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if len(data) > 1:
                logger.warning("[object fetch] Ambiguous account_id=%s (%s): %d records matched", account_id, filter_key, len(data))
                return {"_ambiguous": True}
            if data:
                return data[0]
        except requests.HTTPError as e:
            logger.warning("[object fetch] HTTP %s for account_id=%s (%s): %s", e.response.status_code, account_id, filter_key, e)
        except Exception as e:
            logger.warning("[object fetch] Unexpected error for account_id=%s (%s): %s", account_id, filter_key, e)
    return {}


def _fetch_daily(auth_token: str, id_object: int) -> dict:
    """Fetch latest daily values from the ontology."""
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
# Core functions — called directly by evaluate(), no LLM involved (P15)
# ---------------------------------------------------------------------------

def get_client_data(auth_token: str, account_id: str, client_input: dict) -> dict:
    """Retrieve and merge client data from human input and database.

    Priority: human input first, then database.
    Returns all available fields plus a _missing list of any required fields
    (date_of_birth, account_type, prior_year_end_balance) that could not be resolved.
    """
    data_quality: list[str] = []

    data: dict[str, Any] = {
        "account_id":             account_id,
        "client_name":            client_input.get("client_name"),
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
        if obj.get("_ambiguous"):
            data["_missing"] = ["account_id"]
            data["_source"] = "ambiguous"
            data["data_quality"] = data_quality
            data["_ambiguous"] = True
            return data
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

            id_object = obj.get("id_object")
            if id_object:
                daily = _fetch_daily(auth_token, id_object)
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


def compute_rmd(
    date_of_birth: str,
    account_type: str,
    prior_year_end_balance: float,
    withdrawal_amount_ytd: float = 0.0,
    rmd_amount_stored: float | None = None,
    market_value: float | None = None,
    available_cash: float | None = None,
    _today: date | None = None,
    _distribution_year: int | None = None,
    **kwargs,
) -> dict:
    # kwargs accepted: beneficiary_dob, owner_death_date, is_spouse_beneficiary
    """Apply IRS RMD rules and return withdrawal status.

    Uses the IRS Uniform Lifetime Table (2022 revision).
    Distribution year defaults to the current calendar year (auto-advances each Jan 1).
    Age is calculated as of Dec 31 of the distribution year.
    decision field is always set here — never by LLM (P10).

    Args:
        _today: Override today's date for testing deadline logic. Uses date.today() if None.
        _distribution_year: Override distribution year for testing. Uses current year if None.
    """
    dist_year = _distribution_year if _distribution_year is not None else DISTRIBUTION_YEAR

    if prior_year_end_balance < 0:
        return {
            "decision": "INVALID_INPUT",
            "reason": f"prior_year_end_balance cannot be negative (got {prior_year_end_balance}).",
        }

    age = _age_as_of_dec31(date_of_birth, dist_year)
    if age is None:
        return {
            "decision": "INVALID_INPUT",
            "reason": f"date_of_birth '{date_of_birth}' is not a valid date format (expected YYYY-MM-DD).",
        }

    account_type_norm = account_type.strip().lower()

    if account_type_norm in INHERITED_IRA_ACCOUNT_TYPES:
        # Try to compute if caller provided inherited IRA fields
        ben_dob   = kwargs.get("beneficiary_dob")
        death_dt  = kwargs.get("owner_death_date")
        is_spouse = kwargs.get("is_spouse_beneficiary", False)

        if ben_dob and death_dt:
            result = _compute_inherited_rmd(
                beneficiary_dob=ben_dob,
                owner_death_date=death_dt,
                is_spouse=bool(is_spouse),
                prior_year_end_balance=prior_year_end_balance,
                withdrawal_amount_ytd=withdrawal_amount_ytd,
                available_cash=available_cash,
                market_value=market_value,
                _today=_today,
            )
            if result is not None:
                return result

        # Fall back to MANUAL_REVIEW — missing beneficiary_dob or owner_death_date
        missing = []
        if not ben_dob:
            missing.append("beneficiary_dob")
        if not death_dt:
            missing.append("owner_death_date")
        return {
            "decision": "MANUAL_REVIEW",
            "eligible": None,
            "reason": (
                "Inherited IRAs are subject to special RMD rules (10-year rule or stretch rule "
                "depending on beneficiary relationship and owner death date). "
                f"Missing required fields: {missing}."
            ),
            "age": age,
            "rmd_required_amount": None,
            "withdrawal_amount_ytd": withdrawal_amount_ytd,
            "remaining_rmd": None,
            "withdrawal_status": "Manual Review Required",
            "available_cash": available_cash,
            "market_value": market_value,
            "cash_covers_remaining": None,
            "flags": [f"Inherited IRA — provide {missing} to compute automatically."],
        }

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

    if age < RMD_MIN_AGE:
        return {
            "decision": "NO_ACTION",
            "eligible": False,
            "reason": f"Client is age {age} as of Dec 31, {dist_year}. RMDs begin at age {RMD_MIN_AGE}.",
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

    if rmd_amount_stored is not None:
        rmd_amount = _round2(float(rmd_amount_stored))
    else:
        factor = _irs_factor(age)
        rmd_amount = _round2(prior_year_end_balance / factor) if factor else 0.0

    ytd = float(withdrawal_amount_ytd or 0)
    remaining = _round2(max(rmd_amount - ytd, 0.0))

    if rmd_amount == 0:
        status = "Completed"
    elif ytd <= 0:
        status = "Not Started"
    elif ytd < rmd_amount:
        status = "In Progress"
    else:
        status = "Completed"

    cash = float(available_cash) if available_cash is not None else None
    cash_covers = (cash >= remaining) if cash is not None and remaining > 0 else None

    flags: list[str] = []
    today = _today if _today is not None else date.today()
    days_left = (date(dist_year, 12, 31) - today).days
    if status != "Completed":
        if days_left < 90:
            flags.append(f"Fewer than 90 days remaining in {dist_year} — penalty risk if RMD not completed by Dec 31.")
        elif days_left < 180 and status == "Not Started":
            flags.append(f"RMD not started with fewer than 6 months remaining in {dist_year}.")
    if cash is not None and remaining > 0 and cash < remaining:
        flags.append(f"Available cash (${cash:,.2f}) is insufficient to cover remaining RMD (${remaining:,.2f}) — liquidation may be required.")

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
