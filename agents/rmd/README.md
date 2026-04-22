# RMD Eligibility Agent

> Status: Step 1 complete
> Location: `agents/rmd/`

Evaluates whether a client account has a Required Minimum Distribution obligation for 2026.

---

## What it does

Given a client account, the agent determines:
- Whether the account is RMD-eligible
- The required distribution amount for 2026 (IRS Uniform Lifetime Table, 2022 revision)
- How much has been withdrawn year-to-date
- Whether cash is available to cover the remaining obligation
- Decision: `TAKE_RMD_NOW`, `RMD_IN_PROGRESS`, `RMD_PENDING`, `RMD_COMPLETE`, `NO_ACTION`, `MANUAL_REVIEW`, `INSUFFICIENT_DATA`, `ERROR`

**Rules:**
- RMDs apply to Traditional IRA, SEP IRA, Rollover IRA, 401(k), 403(b), 457(b), and Employer Retirement Plans
- RMDs begin at age 73 (SECURE 2.0). Age calculated as of Dec 31, 2026
- Roth IRAs are not subject to RMDs → `NO_ACTION`
- Inherited IRAs use special rules → `MANUAL_REVIEW` unless beneficiary fields are provided

**What it does not do:**
- Execute withdrawals
- Compute tax withholding
- Provide Dec 31 prior year balance automatically — ontology has latest balance only

---

## Input

```python
evaluate(auth_token, account_id, client_input)
```

| Field | Source | Required | Notes |
|---|---|---|---|
| `account_id` | caller | yes | 8-digit custodian ID or `"manual-input"` |
| `client_input.prior_year_end_balance` | advisor | yes | Dec 31 snapshot not in ontology |
| `client_input.withdrawal_amount_ytd` | advisor | no | defaults to 0 |
| `client_input.date_of_birth` | advisor | only if not in ontology | overrides DB |
| `client_input.account_type` | advisor | only if not in ontology | overrides DB |
| `client_input.beneficiary_dob` | advisor | inherited IRA only | enables auto-compute |
| `client_input.owner_death_date` | advisor | inherited IRA only | enables auto-compute |
| `client_input.is_spouse_beneficiary` | advisor | inherited IRA only | default `false` |

**Auto-fetched from ontology:**

| Field | Used as |
|---|---|
| `date_of_birth` | Age calculation |
| `account_type` | Eligibility branch |
| `first_name`, `last_name` | `client_name` in output |
| `account_balance` (daily) | `prior_year_end_balance` proxy — flagged if used |
| `account_available_cash` (daily) | Cash coverage check |

**Known ontology gaps:**
- `date_of_birth` — null for Pershing accounts; must be provided in `client_input`
- `account_type` — incorrect for some Schwab accounts (e.g. shows Roth IRA when actually Rollover IRA) — always verify or override via `client_input`
- `withdrawal_amount_ytd` — not in ontology; must always come from advisor

---

## Output

Every output always contains all fields. Missing fields are `null` or `[]` — never absent.

```json
{
  "decision": "RMD_PENDING",
  "eligible": true,
  "reason": "Client is age 76 and holds a Traditional IRA.",
  "age": 76,
  "rmd_required_amount": 7511.74,
  "withdrawal_amount_ytd": 0.0,
  "remaining_rmd": 7511.74,
  "withdrawal_status": "Not Started",
  "available_cash": 3200.00,
  "cash_covers_remaining": false,
  "flags": ["RMD not started with fewer than 6 months remaining in 2026."],
  "client_name": "John Smith",
  "missing_fields": [],
  "data_quality": ["USING_LATEST_BALANCE_AS_PROXY", "DOB_FROM_DB"],
  "completeness": "partial",
  "inherited_rule": null
}
```

### Field reference

| Field | Type | Notes |
|---|---|---|
| `decision` | str enum | Primary routing signal — Python-controlled, never LLM |
| `eligible` | bool or null | Core answer |
| `reason` | str | Explains the decision |
| `age` | int | For use in explanation |
| `rmd_required_amount` | float or null | Dollar amount |
| `withdrawal_amount_ytd` | float | What's been withdrawn so far |
| `remaining_rmd` | float or null | What's left |
| `withdrawal_status` | str | Current state |
| `available_cash` | float or null | Cash available for withdrawal |
| `cash_covers_remaining` | bool or null | Whether cash covers the remaining RMD |
| `flags` | list[str] | Human-readable urgency signals for the advisor |
| `client_name` | str or null | For personalizing the response |
| `missing_fields` | list[str] | What to ask the advisor for next |
| `data_quality` | list[str] | Machine-readable provenance constants |
| `completeness` | str | `full` / `partial` / `minimal` |
| `inherited_rule` | str or null | `"10-year"` / `"stretch"` for inherited IRAs |

### Decision enum

| Value | Condition |
|---|---|
| `TAKE_RMD_NOW` | Eligible, not completed, < 90 days left in 2026 |
| `RMD_IN_PROGRESS` | Eligible, in progress, ≥ 90 days left |
| `RMD_PENDING` | Eligible, not started, ≥ 90 days left |
| `RMD_COMPLETE` | Eligible, completed |
| `NO_ACTION` | Not eligible (Roth, too young, unrecognised type) |
| `MANUAL_REVIEW` | Inherited IRA without enough info to compute |
| `INSUFFICIENT_DATA` | Missing required fields |
| `ERROR` | `post_check` caught an incoherent result |

### Inherited IRA rules

When `beneficiary_dob` + `owner_death_date` are provided, the agent computes automatically:

| Scenario | Rule | `inherited_rule` |
|---|---|---|
| Spouse beneficiary | Stretch — Single Life Expectancy Table | `"stretch"` |
| Non-spouse, owner died before 2020 | Stretch — Single Life Expectancy Table | `"stretch"` |
| Non-spouse, owner died 2020 or later | 10-year rule — full balance must be distributed by year 10, no annual RMD required | `"10-year"` |
| Fields missing | Falls back to `MANUAL_REVIEW` | `null` |

### data_quality constants

| Constant | Meaning |
|---|---|
| `USING_LATEST_BALANCE_AS_PROXY` | Latest account balance used instead of Dec 31 snapshot |
| `USER_PROVIDED_BALANCE` | Balance came from advisor input |
| `USER_PROVIDED_WITHDRAWAL_YTD` | YTD withdrawals came from advisor input |
| `DOB_FROM_DB` | Date of birth fetched from ontology |
| `DOB_FROM_INPUT` | Date of birth provided by advisor |
| `ACCOUNT_TYPE_FROM_DB` | Account type fetched from ontology |
| `ACCOUNT_TYPE_FROM_INPUT` | Account type provided by advisor |

### completeness

| Value | Condition |
|---|---|
| `full` | All required fields from ontology, no proxies |
| `partial` | Balance proxy used, or advisor provided balance/YTD |
| `minimal` | Required fields missing |

---

## Pipeline

```
evaluate(auth_token, account_id, client_input)
    │
    ▼
pre_check()          — block if required fields missing (manual-input path)
    │
    ▼
get_client_data()    — fetch from ontology, merge with client_input
    │
    ▼
compute_rmd()        — IRS math, eligibility logic, decision enum
    │
    ▼
post_check()         — enforce output schema, validate result
    │
    ▼
return dict          — all fields always present
```

No LLM in the main path. All logic is deterministic Python. LLM is used only in `parser.py` for extracting fields from free-text advisor input.

---

## Quick start

See repo root README for setup (AWS login + `make token`). Then from the repo root:

```bash
# Run all tests
make test-all

# Manual input — no AWS needed
make run-manual-rmd DOB=1950-03-15 TYPE="Traditional IRA" BALANCE=320000 YTD=10000

# Free-text input — requires AWS
make run-nl-rmd TEXT="John Smith, DOB March 15 1950, Traditional IRA, balance 320k, took out 10k"

# Live account — requires AWS + token
make run-rmd ACCOUNT_ID=38279295 BALANCE=178399
```

---

## Test fixtures

22 core fixtures + 5 NL parser fixtures + 21 real-data fixtures.

```bash
make test          # 22 core fixtures
make test-parser   # 5 NL parser fixtures
make test-real     # 21 real-data fixtures (live accounts)
```

| Fixture | Scenario |
|---|---|
| 01 | Age 76, Traditional IRA — not started |
| 02 | Age 80, Traditional IRA — in progress |
| 03 | Age 75, Traditional IRA — completed |
| 04 | Roth IRA — not subject to RMDs → `NO_ACTION` |
| 05 | Age 65 — under 73, not yet eligible → `NO_ACTION` |
| 06 | Missing DOB and balance → `INSUFFICIENT_DATA` |
| 07 | All fields supplied manually — no DB lookup |
| 08 | Negative balance → `INVALID_INPUT` |
| 09 | Invalid DOB format → `INVALID_INPUT` |
| 10 | Lowercase `traditional ira` — case-insensitive match |
| 11 | Lowercase `roth ira` — not eligible |
| 12 | Inherited IRA, no fields → `MANUAL_REVIEW` |
| 13 | Employer Retirement Plan (401k/403b/457b) — eligible |
| 14 | Age 73 — first RMD year, IRS factor 26.5 |
| 15 | Zero balance — RMD is zero → `RMD_COMPLETE` |
| 16 | YTD equals RMD exactly — boundary → `RMD_COMPLETE` |
| 17 | SEP IRA — eligible |
| 18 | Rollover IRA — eligible |
| 19 | Age 77, < 90 days left → `TAKE_RMD_NOW` |
| 20 | Inherited IRA, non-spouse, owner died 2021 — 10-year rule |
| 21 | Inherited IRA, spouse — stretch rule |
| 22 | Inherited IRA, no beneficiary fields → `MANUAL_REVIEW` |

Real-data fixtures cover Schwab, Fidelity IWS, and Pershing accounts across all decision paths.

---

## Known limitations

| Limitation | Impact |
|---|---|
| Dec 31 balance not in ontology | Proxy flagged; advisor must provide exact value |
| YTD withdrawals not in ontology | Must always come from advisor |
| DOB null for Pershing accounts | Advisor must provide in `client_input` |
| `account_type` wrong for some Schwab accounts | Always verify or override via `client_input` |
| Inherited IRA auto-compute requires advisor fields | Needs `beneficiary_dob` + `owner_death_date`; fallback is `MANUAL_REVIEW` |

---

## Step 1 completion gate

- [x] `make test` → 22/22 pass
- [x] `make test-parser` → 5/5 pass
- [x] `decision` enum on every output — uppercase, Python-controlled
- [x] All schema keys always present — `OUTPUT_SCHEMA` merge in `post_check`
- [x] `data_quality[]` and `completeness` on every output
- [x] LLM removed from main path — pure Python pipeline (P15)
- [x] NL layer — `parser.py` free-text → structured `client_input`
- [x] CI gate blocking on fixture failures
- [x] Phoenix tracing wired (`make test-trace`)
- [x] Bedrock swap verified — 3×22/22 pass, p50=5.6s, p95 within 30s threshold
- [x] 3-run stability confirmed (2026-04-22)
