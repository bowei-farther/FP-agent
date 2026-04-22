# RMD Eligibility Agent

> Status: Step 1 complete — pending Bedrock swap
> Location: `agents/rmd/`

Evaluates whether a client account has a Required Minimum Distribution obligation for 2026.
One independent sub-agent in the financial planning system.

---

## What it does

Given a client account, the agent determines:
- Whether the account is RMD-eligible
- The required distribution amount for 2026 (IRS Uniform Lifetime Table, 2022 revision)
- How much has been withdrawn year-to-date
- Whether cash is available to cover the remaining obligation
- Decision: `TAKE_RMD_NOW`, `RMD_IN_PROGRESS`, `RMD_PENDING`, `RMD_COMPLETE`, `NO_ACTION`, `MANUAL_REVIEW`, `INSUFFICIENT_DATA`, `ERROR`

**Core rules:**
- RMDs apply to Traditional IRA, SEP IRA, Rollover IRA, 401(k), 403(b), 457(b), and Employer Retirement Plans
- RMDs begin at age 73 (SECURE 2.0). Age calculated as of Dec 31, 2026
- Roth IRAs: not subject to RMD → `NO_ACTION`
- Inherited IRAs: special rules apply → `MANUAL_REVIEW` (standard table does not apply)
- Distribution year is pinned to `DISTRIBUTION_YEAR = 2026` — not dynamic

## What it does not do

- Execute withdrawals
- Compute tax withholding on the distribution
- Handle Inherited IRA rules (10-year rule, Life Expectancy method)
- Provide Dec 31 prior year balance automatically — ontology provides latest balance only

---

## Input

| Field | Source | Required |
|---|---|---|
| `date_of_birth` | Ontology `date_of_birth` (object) or advisor input | Yes |
| `account_type` | Ontology `account_type` (object) | Yes (auto-fetched) |
| `prior_year_end_balance` | Advisor input (Dec 31 snapshot not in ontology) | Yes |
| `withdrawal_amount_ytd` | Advisor input (no transaction history in ontology) | No — defaults to 0 |

**Ontology fields fetched automatically:**

| Ontology field | Daily ID | Used as |
|---|---|---|
| `account_balance` | 277 | `prior_year_end_balance` proxy (flagged if used) |
| `account_available_cash` | 1301 | Cash coverage check |
| `account_settled_cash` | 1302 | Supplemental cash info |
| `account_sweep_balance` | 1436 | Supplemental cash info |
| `account_market_value` | 1303 | Portfolio value (informational) |

**Note:** DOB for Pershing accounts is not yet in the ontology. Advisor must provide for Pershing clients.

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
  "market_value": 174100.00,
  "cash_covers_remaining": false,
  "flags": ["RMD not started with fewer than 6 months remaining in 2026."],
  "data_quality": ["USING_LATEST_BALANCE_AS_PROXY", "DOB_FROM_DB"],
  "completeness": "partial",
  "input_echo": {
    "date_of_birth": "1950-03-15",
    "account_type": "Traditional IRA",
    "prior_year_end_balance": 178000.0,
    "withdrawal_amount_ytd": 0.0
  },
  "client_name": "John Smith",
  "advisor_name": "Jane Doe",
  "missing_fields": [],
  "_source": "api"
}
```

### Field reference

| Field | Type | Description |
|---|---|---|
| `decision` | str enum | Machine-readable action — Python-controlled, never LLM |
| `eligible` | bool or null | Whether RMD rules apply to this account |
| `reason` | str | Human-readable explanation |
| `age` | int | Client age as of Dec 31, 2026 |
| `rmd_required_amount` | float or null | Required distribution for 2026 |
| `withdrawal_amount_ytd` | float | Year-to-date withdrawals taken |
| `remaining_rmd` | float or null | `rmd_required_amount - withdrawal_amount_ytd` |
| `withdrawal_status` | str | `Not Started` / `In Progress` / `Completed` / `Not Applicable` / `Manual Review Required` |
| `available_cash` | float or null | Uninvested cash from ontology |
| `market_value` | float or null | Positions value from ontology (informational) |
| `cash_covers_remaining` | bool or null | Whether cash covers remaining RMD |
| `flags` | list[str] | Advisor-facing warnings (deadline risk, cash shortfall) |
| `data_quality` | list[str] | System-facing provenance flags (named constants) |
| `completeness` | str | `full` / `partial` / `minimal` |
| `input_echo` | dict | Exact field values used in the calculation |
| `client_name` | str or null | From ontology |
| `advisor_name` | str or null | From ontology |
| `missing_fields` | list[str] | Fields that could not be resolved |
| `_source` | str | Where data came from (`input`, `api`, `input+api`, `pre_check`, `post_check`) |

### Decision enum

| Value | Condition |
|---|---|
| `TAKE_RMD_NOW` | Eligible, not completed, < 90 days left in 2026 |
| `RMD_IN_PROGRESS` | Eligible, In Progress, ≥ 90 days left |
| `RMD_PENDING` | Eligible, Not Started, ≥ 90 days left |
| `RMD_COMPLETE` | Eligible, Completed |
| `NO_ACTION` | Not eligible (Roth, too young, unrecognised type) |
| `MANUAL_REVIEW` | Inherited IRA |
| `INSUFFICIENT_DATA` | Missing required fields (set by `pre_check`) |
| `ERROR` | Post-check caught incoherent result |

### data_quality constants

| Constant | Meaning |
|---|---|
| `USING_LATEST_BALANCE_AS_PROXY` | `account_balance` used instead of Dec 31 snapshot |
| `USER_PROVIDED_BALANCE` | Balance came from advisor input |
| `USER_PROVIDED_WITHDRAWAL_YTD` | YTD withdrawals came from advisor input |
| `DOB_FROM_DB` | Date of birth fetched from ontology |
| `DOB_FROM_INPUT` | Date of birth provided by advisor |
| `ACCOUNT_TYPE_FROM_DB` | Account type fetched from ontology |
| `ACCOUNT_TYPE_FROM_INPUT` | Account type provided by advisor |

### completeness rule

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
pre_check()          — Python: block if required fields missing
    │
    ▼
Strands Agent        — LLM: orchestrate tool calls only
  ├── get_client_data()   — fetch from ontology, merge with client_input
  └── compute_rmd()       — IRS math in Python, return result dict
    │
    ▼
post_check()         — Python: enforce output schema, validate result, set decision
    │
    ▼
evaluate() return dict   — all fields always present
```

LLM does orchestration only. All math (`compute_rmd`), eligibility logic, schema enforcement, and `decision` assignment are Python.

---

## Quick start

```bash
# 1. Install dependencies — run once from the repo root
cd /path/to/financial-planning
uv sync

# 2. Set API key
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Run all test fixtures (no AWS needed)
cd agents/rmd
make test

# 4. Run with manual input (no AWS needed)
make run-manual DOB=1950-03-15 TYPE="Traditional IRA" BALANCE=320000 YTD=10000

# 5. Run with free-text advisor input (no AWS needed)
make run-nl TEXT="John Smith, DOB March 15 1950, Traditional IRA, balance 320k, took out 10k"

# 6. Run against a real account (requires AWS SSO)
aws sso login --profile data-lake-dev
make run ACCOUNT_ID=38279295 BALANCE=178399
```

One shared environment at the repo root (`financial-planning/.venv/`). Steps 1–4 require no database access. Step 5 requires AWS SSO for the Farther ontology.

---

## Test fixtures

18 fixtures covering all eligibility branches, boundary cases, and error cases:

| Fixture | Scenario |
|---|---|
| 01 | Age 76, Traditional IRA — not started |
| 02 | Age 80, Traditional IRA — in progress |
| 03 | Age 75, Traditional IRA — completed (ytd exceeds RMD) |
| 04 | Roth IRA — never subject to RMDs → `NO_ACTION` |
| 05 | Age 65 — under 73, not yet eligible → `NO_ACTION` |
| 06 | Missing DOB and balance — ask back → `INSUFFICIENT_DATA` |
| 07 | All fields supplied manually — no DB lookup |
| 08 | Negative balance — rejected before compute → `INSUFFICIENT_DATA` |
| 09 | Invalid DOB format — rejected before compute → `INSUFFICIENT_DATA` |
| 10 | Lowercase `traditional ira` — case-insensitive match |
| 11 | Lowercase `roth ira` — not eligible, not unknown type |
| 12 | Inherited IRA — standard table does not apply → `MANUAL_REVIEW` |
| 13 | Employer Retirement Plan (401k/403b/457b) — eligible |
| 14 | Age 73 — first RMD year, IRS factor 26.5 |
| 15 | Zero balance — RMD is zero, nothing owed → `RMD_COMPLETE` |
| 16 | YTD equals RMD exactly — boundary → `RMD_COMPLETE` |
| 17 | SEP IRA — eligible, same Uniform Lifetime Table |
| 18 | Rollover IRA — eligible, same Uniform Lifetime Table |

---

## Known limitations

| Limitation | Impact | Plan |
|---|---|---|
| Dec 31 balance requires advisor input | Balance proxy flagged with `USING_LATEST_BALANCE_AS_PROXY` | Remains advisor input until ontology exposes point-in-time snapshot |
| YTD withdrawals require advisor input | No transaction history in ontology | Defaults to 0, flagged with `USER_PROVIDED_WITHDRAWAL_YTD` |
| DOB missing for Pershing accounts | Advisor must provide DOB for Pershing clients | Resolved when people data lands in ontology |
| Inherited IRA always manual review | Cannot evaluate 10-year rule automatically | No automated source for beneficiary death date |

---

## Step 1 completion gate

Before this agent connects to the integration agent:

- [x] `make test` → 18/18 pass
- [x] `decision` enum on every output — uppercase, Python-controlled
- [x] All schema keys always present — `OUTPUT_SCHEMA` merge in `post_check`
- [x] `data_quality[]` and `completeness` on every output
- [x] `input_echo` on every output
- [x] JSON parse retry — 3-attempt loop with fence stripping
- [x] NL layer — `parser.py` free-text → structured `client_input`
- [x] CI gate blocking on fixture failures
- [ ] Bedrock swap verified
- [x] NL layer — `parser.py` free-text → structured `client_input`
- [x] CI gate blocking on fixture failures
- [ ] Bedrock swap — moved to Step 2 (Task 2L)