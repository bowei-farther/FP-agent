"""NL input layer for the RMD agent (Task 8).

Converts free-text advisor input into a structured client_input dict
that evaluate() can consume directly.

Pipeline:
    free text
        ↓
    LLM extraction  (fields only — no reasoning, no guessing)
        ↓
    Python normalization  (balance strings → float, DOB format, type matching)
        ↓
    Python validation  (missing field detection)
        ↓
    client_input dict  → evaluate()

LLM role: extract only. Never infer, guess, or fill in missing values.
Python role: normalize and validate. Never ask the LLM to interpret ambiguous input.
"""

from __future__ import annotations

import json
import logging
import os
import re

import anthropic

from .tools import DISTRIBUTION_YEAR, RMD_ELIGIBLE_ACCOUNT_TYPES, ROTH_ACCOUNT_TYPES, INHERITED_IRA_ACCOUNT_TYPES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known account type aliases → canonical ontology strings
# Used by Python normalization — not the LLM
# ---------------------------------------------------------------------------

_ACCOUNT_TYPE_ALIASES: dict[str, str] = {
    # Traditional IRA variants
    "traditional ira":        "Traditional IRA",
    "trad ira":               "Traditional IRA",
    "trad_ira":               "Traditional IRA",
    "traditional":            "Traditional IRA",
    # SEP IRA variants
    "sep ira":                "SEP IRA",
    "sep-ira":                "SEP IRA",
    "sep_ira":                "SEP IRA",
    "sep":                    "SEP IRA",
    # Rollover IRA variants
    "rollover ira":           "Rollover IRA",
    "rollover_ira":           "Rollover IRA",
    "rollover":               "Rollover IRA",
    # Roth IRA variants
    "roth ira":               "Roth IRA",
    "roth_ira":               "Roth IRA",
    "roth":                   "Roth IRA",
    # Employer plans
    "401k":                   "Employer Retirement Plan",
    "401(k)":                 "Employer Retirement Plan",
    "403b":                   "Employer Retirement Plan",
    "403(b)":                 "Employer Retirement Plan",
    "457":                    "Employer Retirement Plan",
    "457(b)":                 "Employer Retirement Plan",
    "employer retirement":    "Employer Retirement Plan",
    "employer retirement plan": "Employer Retirement Plan",
    # Inherited IRA
    "inherited ira":          "Inherited IRA",
    "inherited_ira":          "Inherited IRA",
    # SIMPLE IRA
    "simple ira":             "SIMPLE IRA",
    "simple_ira":             "SIMPLE IRA",
}

# ---------------------------------------------------------------------------
# LLM extraction prompt
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """\
Extract RMD-related fields from the advisor's input. Return ONLY valid JSON with these keys:

{
  "client_name":            string or null,
  "date_of_birth":          string (YYYY-MM-DD) or null,
  "account_type":           string or null,
  "prior_year_end_balance": number or null,
  "withdrawal_amount_ytd":  number or null,
  "advisor_name":           string or null
}

Rules:
- If a field is not explicitly stated, return null. Do NOT infer or guess.
- date_of_birth: convert to YYYY-MM-DD format if possible. If only year given (e.g. "born 1950"), return null — need full date.
- prior_year_end_balance: convert to a plain number (e.g. "$178,000" → 178000, "320k" → 320000, "1.2M" → 1200000).
- withdrawal_amount_ytd: same conversion as balance. If not mentioned, return null (not 0).
- account_type: return the string as stated. Do not normalize or canonicalize.
- Return JSON only. No commentary, no explanation, no markdown fences.

Advisor input:
{input}
"""

# ---------------------------------------------------------------------------
# Python normalization helpers
# ---------------------------------------------------------------------------

def _normalize_balance(value: float | int | str | None) -> float | None:
    """Convert balance value to float. Returns None if unparseable."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # Remove currency symbols and commas
        s = value.strip().replace("$", "").replace(",", "").lower()
        try:
            if s.endswith("k"):
                return float(s[:-1]) * 1_000
            if s.endswith("m"):
                return float(s[:-1]) * 1_000_000
            return float(s)
        except ValueError:
            return None
    return None


def _normalize_account_type(raw: str | None) -> str | None:
    """Map raw account type string to canonical ontology string."""
    if raw is None:
        return None
    normalized = _ACCOUNT_TYPE_ALIASES.get(raw.strip().lower())
    if normalized:
        return normalized
    # Return as-is if not in alias map — tools.py handles case-insensitive matching
    return raw.strip()


def _normalize_dob(raw: str | None) -> str | None:
    """Validate DOB is in YYYY-MM-DD format. Return None if not."""
    if raw is None:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw.strip()):
        return raw.strip()
    return None


# ---------------------------------------------------------------------------
# LLM extraction call
# ---------------------------------------------------------------------------

def _extract_fields(text: str) -> dict:
    """Call LLM to extract structured fields from free text.

    Returns a dict with all keys present (null for missing fields).
    Never raises — returns empty extraction on failure.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=api_key)
    prompt = _EXTRACTION_PROMPT.replace("{input}", text)

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()

        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        parsed = json.loads(raw)
        return parsed

    except Exception as exc:
        logger.warning("[parser] LLM extraction failed: %s", exc)
        return {
            "client_name":            None,
            "date_of_birth":          None,
            "account_type":           None,
            "prior_year_end_balance": None,
            "withdrawal_amount_ytd":  None,
            "advisor_name":           None,
        }


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def parse(text: str) -> dict:
    """Convert free-text advisor input to a structured client_input dict.

    Returns a dict ready to pass to evaluate(). Fields that could not be
    extracted are absent from the dict (not null) so that evaluate() can
    distinguish "not provided" from "provided as null".

    Args:
        text: Free-text advisor input, e.g.
              "John Smith, DOB March 15 1950, Traditional IRA, balance 320k, took out 10k"

    Returns:
        dict with any subset of: client_name, date_of_birth, account_type,
        prior_year_end_balance, withdrawal_amount_ytd, advisor_name.
        Missing fields are omitted (not present in dict).
    """
    raw = _extract_fields(text)

    result: dict = {}

    # client_name
    if raw.get("client_name"):
        result["client_name"] = str(raw["client_name"]).strip()

    # advisor_name
    if raw.get("advisor_name"):
        result["advisor_name"] = str(raw["advisor_name"]).strip()

    # date_of_birth — normalize to YYYY-MM-DD, drop if invalid
    dob = _normalize_dob(raw.get("date_of_birth"))
    if dob:
        result["date_of_birth"] = dob

    # account_type — normalize to canonical string
    account_type = _normalize_account_type(raw.get("account_type"))
    if account_type:
        result["account_type"] = account_type

    # prior_year_end_balance
    balance = _normalize_balance(raw.get("prior_year_end_balance"))
    if balance is not None:
        result["prior_year_end_balance"] = balance

    # withdrawal_amount_ytd
    ytd = _normalize_balance(raw.get("withdrawal_amount_ytd"))
    if ytd is not None:
        result["withdrawal_amount_ytd"] = ytd

    return result
