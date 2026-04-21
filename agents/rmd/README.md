# RMD Eligibility Agent

> Status: Step 1 in progress
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

## How to run

```bash
cd agents/rmd

# Install
uv sync

# Credentials
export ANTHROPIC_API_KEY=sk-ant-...
aws sso login --profile data-lake-dev

# Run against real account (fetches from ontology)
make run ACCOUNT_ID=38279295 BALANCE=178399

# Run with manual input (no AWS needed)
make run-manual DOB=1950-03-15 TYPE="Traditional IRA" BALANCE=320000 YTD=10000

# Run all fixtures
make test
```

---

## Test fixtures

13 fixtures covering all eligibility branches and error cases:

| Fixture | Scenario |
|---|---|
| 01 | Eligible — not started, no cash data |
| 02 | Eligible — in progress |
| 03 | Eligible — completed |
| 04 | Not eligible — too young (age 65) |
| 05 | Not eligible — Roth IRA |
| 06 | Eligible — cash covers remaining |
| 07 | Eligible — cash insufficient (liquidation warning) |
| 08 | Missing DOB → `INSUFFICIENT_DATA` |
| 09 | Missing balance → `INSUFFICIENT_DATA` |
| 10 | SEP IRA — eligible |
| 11 | Rollover IRA — eligible |
| 12 | Inherited IRA → `MANUAL_REVIEW` |
| 13 | Employer Retirement Plan (401k/403b) — eligible |

---

## Known limitations

| Limitation | Impact | Plan |
|---|---|---|
| Dec 31 balance requires advisor input | Balance proxy flagged with `USING_LATEST_BALANCE_AS_PROXY` | Remains advisor input until ontology exposes point-in-time snapshot |
| YTD withdrawals require advisor input | No transaction history in ontology | Defaults to 0, flagged with `USER_PROVIDED_WITHDRAWAL_YTD` |
| DOB missing for Pershing accounts | Advisor must provide DOB for Pershing clients | Resolved when people data lands in ontology |
| Inherited IRA always manual review | Cannot evaluate 10-year rule automatically | No automated source for beneficiary death date |
| `decision` enum not yet in code | Step 1 Task 2 | In progress |
| `data_quality[]` / `completeness` / `input_echo` not yet in code | Step 1 Task 4 | In progress |
| JSON parse retry not yet implemented | Step 1 Task 5 | In progress |
| NL input layer not yet built | Step 1 Task 8 | In progress |

---

## Step 1 completion gate

Before this agent connects to the integration agent:

- [ ] `make test` → 13/13 pass
- [ ] `decision` enum on every output — no free-text decisions
- [ ] All schema keys always present — no silent missing fields
- [ ] `data_quality[]` and `completeness` on every output
- [ ] `input_echo` on every output
- [ ] JSON parse retry — no parse failures in 20 consecutive `make test` runs
- [ ] NL layer: 5 advisor phrasings → correct `evaluate()` call
- [ ] CI gate blocking on fixture failures
- [ ] Bedrock swap verified
