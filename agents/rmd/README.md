# RMD Eligibility Agent

> Status: Step 1 complete
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
- Provide Dec 31 prior year balance automatically — ontology provides latest balance only

---

## Input

```python
evaluate(auth_token, account_id, client_input)
```

| Field | Source | Required | Notes |
|---|---|---|---|
| `account_id` | caller | yes | 8-digit custodian ID or `"manual-input"` |
| `client_input.prior_year_end_balance` | advisor | yes | Dec 31 snapshot not in ontology |
| `client_input.withdrawal_amount_ytd` | advisor | no | defaults to 0 — no transaction history in ontology |
| `client_input.date_of_birth` | advisor | only if not in ontology | overrides DB |
| `client_input.account_type` | advisor | only if not in ontology | overrides DB |
| `client_input.beneficiary_dob` | advisor | inherited IRA only | enables auto-compute |
| `client_input.owner_death_date` | advisor | inherited IRA only | enables auto-compute |
| `client_input.is_spouse_beneficiary` | advisor | inherited IRA only | default `false` |

**Auto-fetched from ontology (no client_input needed):**

| Ontology field | Used as |
|---|---|
| `date_of_birth` | age calculation |
| `account_type` | eligibility branch |
| `first_name`, `last_name` | `client_name` in output |
| `account_balance` (daily) | `prior_year_end_balance` proxy — flagged if used |
| `account_available_cash` (daily) | cash coverage check |

**Known ontology gaps:**
- `manager` (advisor name) — always null, not in output
- `date_of_birth` — null for Pershing accounts, must be provided in `client_input`
- `account_type` — incorrect for some Schwab accounts (e.g. shows Roth IRA when actually Rollover IRA) — always verify or override via `client_input`
- `withdrawal_amount_ytd` — not in ontology, must always come from advisor

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

**Design principle — no more, no less:**
Output contains exactly what the reasoning layer (LLM at integration) needs to explain and act on the result. Internal fields (`_source`, `input_echo`, `market_value`) are stripped by `post_check`.

### Field reference

| Field | Type | For reasoning layer |
|---|---|---|
| `decision` | str enum | primary routing signal — Python-controlled, never LLM |
| `eligible` | bool or null | core answer |
| `reason` | str | explain the decision |
| `age` | int | "at age 87, factor is 14.4..." |
| `rmd_required_amount` | float or null | dollar amount to cite |
| `withdrawal_amount_ytd` | float | what's been done so far |
| `remaining_rmd` | float or null | what's left to act on |
| `withdrawal_status` | str | state to explain |
| `available_cash` | float or null | "you have $X available" |
| `cash_covers_remaining` | bool or null | cash covers / doesn't cover remaining |
| `flags` | list[str] | urgency signals to communicate |
| `client_name` | str or null | personalize the response |
| `missing_fields` | list[str] | what to ask the advisor for |
| `data_quality` | list[str] | confidence of explanation (advisor-provided vs DB) |
| `completeness` | str | how confident to sound (`full` / `partial` / `minimal`) |
| `inherited_rule` | str or null | `"10-year"` / `"stretch"` — explain the correct inherited IRA rule |

### Decision enum

| Value | Condition |
|---|---|
| `TAKE_RMD_NOW` | Eligible, not completed, < 90 days left in 2026 |
| `RMD_IN_PROGRESS` | Eligible, In Progress, ≥ 90 days left |
| `RMD_PENDING` | Eligible, Not Started, ≥ 90 days left |
| `RMD_COMPLETE` | Eligible, Completed |
| `NO_ACTION` | Not eligible (Roth, too young, unrecognised type) |
| `MANUAL_REVIEW` | Inherited IRA — insufficient info to compute automatically |
| `INSUFFICIENT_DATA` | Missing required fields |
| `ERROR` | `post_check` caught incoherent result |

### Inherited IRA rules (when `beneficiary_dob` + `owner_death_date` provided)

| Scenario | Rule | `inherited_rule` |
|---|---|---|
| Spouse beneficiary | Stretch — Single Life Expectancy Table | `"stretch"` |
| Non-spouse, owner died before 2020 | Stretch — Single Life Expectancy Table | `"stretch"` |
| Non-spouse, owner died 2020+ | 10-year rule — no annual RMD, full balance by year 10 | `"10-year"` |
| Fields missing | Falls back to `MANUAL_REVIEW` | `null` |

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
pre_check()          — Python: block if required fields missing on manual-input path
    │
    ▼
get_client_data()    — Python: fetch from ontology, merge with client_input
    │
    ▼
compute_rmd()        — Python: IRS math, eligibility logic, decision enum
    │
    ▼
post_check()         — Python: enforce output schema, validate result
    │
    ▼
evaluate() return dict   — all fields always present
```

No LLM in the main path. All logic is deterministic Python. LLM is used only in the NL input layer (`parser.py`) for field extraction from free text.

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

22 core fixtures + 5 NL parser fixtures + 21 real-data fixtures. Run separately:

```bash
make test          # 22 core fixtures (rmd-*.json)
make test-parser   # 5 NL parser fixtures (nl-*.json)
make test-real     # 21 real-data fixtures (prompts/real/*.json)
```

| Fixture | Scenario |
|---|---|
| 01 | Age 76, Traditional IRA — not started |
| 02 | Age 80, Traditional IRA — in progress |
| 03 | Age 75, Traditional IRA — completed (ytd exceeds RMD) |
| 04 | Roth IRA — never subject to RMDs → `NO_ACTION` |
| 05 | Age 65 — under 73, not yet eligible → `NO_ACTION` |
| 06 | Missing DOB and balance — ask back → `INSUFFICIENT_DATA` |
| 07 | All fields supplied manually — no DB lookup |
| 08 | Negative balance — rejected → `INVALID_INPUT` |
| 09 | Invalid DOB format — rejected → `INVALID_INPUT` |
| 10 | Lowercase `traditional ira` — case-insensitive match |
| 11 | Lowercase `roth ira` — not eligible, not unknown type |
| 12 | Inherited IRA, no fields — standard table does not apply → `MANUAL_REVIEW` |
| 13 | Employer Retirement Plan (401k/403b/457b) — eligible |
| 14 | Age 73 — first RMD year, IRS factor 26.5 |
| 15 | Zero balance — RMD is zero → `RMD_COMPLETE` |
| 16 | YTD equals RMD exactly — boundary → `RMD_COMPLETE` |
| 17 | SEP IRA — eligible, same Uniform Lifetime Table |
| 18 | Rollover IRA — eligible, same Uniform Lifetime Table |
| 19 | Age 77, < 90 days left → `TAKE_RMD_NOW` |
| 20 | Inherited IRA, non-spouse, post-SECURE — 10-year rule |
| 21 | Inherited IRA, spouse — stretch rule, Single Life Expectancy Table |
| 22 | Inherited IRA, no fields provided — fallback → `MANUAL_REVIEW` |

**Real-data fixtures** (`prompts/real/*.json`) — live accounts from ontology:
- Schwab, Fidelity IWS, Pershing across all decision paths
- Bugs found: Fidelity `"Designated Beneficiary"` was silently `NO_ACTION`; rounding `.5` boundary divergence fixed with `ROUND_HALF_UP`

---

## Known limitations

| Limitation | Impact | Plan |
|---|---|---|
| Dec 31 balance not in ontology | Proxy flagged with `USING_LATEST_BALANCE_AS_PROXY` | Advisor must provide; exact value requires confirmation |
| YTD withdrawals not in ontology | Defaults to 0 | Must always come from advisor |
| DOB null for Pershing accounts | Must provide in `client_input` | Resolved when people data lands in ontology |
| `account_type` wrong for some Schwab accounts | Ontology shows Roth IRA when actually Rollover IRA | Always verify or override via `client_input` |
| `manager` (advisor name) always null | Removed from output | Not populated in ontology |
| Inherited IRA auto-compute requires advisor fields | Needs `beneficiary_dob` + `owner_death_date` | No automated source; fallback is `MANUAL_REVIEW` |

---

## Step 1 completion gate

- [x] `make test` → 19/19 pass
- [x] `make test-parser` → 5/5 pass
- [x] `decision` enum on every output — uppercase, Python-controlled
- [x] All schema keys always present — `OUTPUT_SCHEMA` merge in `post_check`
- [x] `data_quality[]` and `completeness` on every output
- [x] `input_echo` on every output
- [x] LLM removed from main path — pure Python pipeline (P15)
- [x] NL layer — `parser.py` free-text → structured `client_input`
- [x] CI gate blocking on fixture failures
- [x] Phoenix tracing wired (`make test-trace`)
- [x] Bedrock swap verified — 3×19/19 pass, p50=5.6s, p95 within 30s threshold
- [x] 3-run stability confirmed (2026-04-22)