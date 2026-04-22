# RMD Agent

> Status: Step 1 complete — 2026-04-22
> Location: `agents/rmd/`

Evaluates whether a client account has a Required Minimum Distribution obligation for the current year. Returns a structured, machine-verifiable result. Advisory only — never executes withdrawals.

---

## Interface

```python
evaluate(auth_token: str, account_id: str, client_input: dict) -> dict
```

---

## Input

| Field | Source | Required | Notes |
|---|---|---|---|
| `account_id` | caller | yes | 8-digit custodian ID or `"manual-input"` |
| `client_input.prior_year_end_balance` | advisor | yes | Dec 31 snapshot not in ontology |
| `client_input.withdrawal_amount_ytd` | advisor | no | defaults to 0 |
| `client_input.date_of_birth` | advisor | only if missing from ontology | overrides DB |
| `client_input.account_type` | advisor | only if missing from ontology | overrides DB |
| `client_input.beneficiary_dob` | advisor | inherited IRA only | enables auto-compute |
| `client_input.owner_death_date` | advisor | inherited IRA only | enables auto-compute |
| `client_input.is_spouse_beneficiary` | advisor | inherited IRA only | default `false` |

Auto-fetched from ontology: `date_of_birth`, `account_type`, `first_name`, `last_name`, `account_balance` (proxy), `account_available_cash`.

---

## Output

All fields always present. Missing values are `null` or `[]` — never absent.

| Field | Type | Description |
|---|---|---|
| `decision` | str enum | Primary routing signal — Python-controlled, never LLM |
| `eligible` | bool or null | Whether RMD applies |
| `reason` | str | Explains the decision |
| `age` | int | Age as of Dec 31 of distribution year |
| `rmd_required_amount` | float or null | IRS-computed obligation |
| `withdrawal_amount_ytd` | float | Withdrawn so far this year |
| `remaining_rmd` | float or null | Remaining obligation |
| `withdrawal_status` | str | `Not Started` / `In Progress` / `Completed` / `Not Required` |
| `available_cash` | float or null | Cash available for withdrawal |
| `cash_covers_remaining` | bool or null | Whether cash covers the remaining RMD |
| `flags` | list[str] | Advisor-facing urgency signals |
| `client_name` | str or null | For personalizing the response |
| `missing_fields` | list[str] | What to ask the advisor for next |
| `data_quality` | list[str] | Machine-readable provenance constants |
| `completeness` | str | `full` / `partial` / `minimal` |
| `inherited_rule` | str or null | `"10-year"` / `"stretch"` for inherited IRAs |

### Decision enum

| Value | Condition |
|---|---|
| `TAKE_RMD_NOW` | Eligible, not completed, < 90 days left |
| `RMD_IN_PROGRESS` | Eligible, partial withdrawal, ≥ 90 days left |
| `RMD_PENDING` | Eligible, not started, ≥ 90 days left |
| `RMD_COMPLETE` | Full obligation satisfied |
| `NO_ACTION` | Not eligible (Roth, under 73, unrecognised type) |
| `MANUAL_REVIEW` | Inherited IRA without enough data to compute |
| `INSUFFICIENT_DATA` | Required field missing from all sources |
| `INVALID_INPUT` | Caller-supplied data is structurally invalid |
| `ERROR` | `post_check` caught an incoherent result — system bug |

### Inherited IRA rules

| Scenario | Rule | `inherited_rule` |
|---|---|---|
| Spouse beneficiary | Stretch — Single Life Expectancy Table | `"stretch"` |
| Non-spouse, owner died before 2020 | Stretch — Single Life Expectancy Table | `"stretch"` |
| Non-spouse, owner died 2020 or later | 10-year rule — full balance by year 10, no annual RMD | `"10-year"` |
| Beneficiary fields missing | `MANUAL_REVIEW` | `null` |

### data_quality constants

| Constant | Meaning |
|---|---|
| `USING_LATEST_BALANCE_AS_PROXY` | Latest balance used instead of Dec 31 snapshot |
| `USER_PROVIDED_BALANCE` | Balance came from advisor |
| `USER_PROVIDED_WITHDRAWAL_YTD` | YTD withdrawals came from advisor |
| `DOB_FROM_DB` | DOB fetched from ontology |
| `DOB_FROM_INPUT` | DOB provided by advisor |
| `ACCOUNT_TYPE_FROM_DB` | Account type fetched from ontology |
| `ACCOUNT_TYPE_FROM_INPUT` | Account type provided by advisor |

---

## Pipeline

```
pre_check       — block if required fields missing
get_client_data — fetch from ontology, merge with client_input
compute_rmd     — IRS math, eligibility logic, decision enum
post_check      — enforce output schema, validate result
```

No LLM in the main path. LLM is used only in `parser.py` for free-text input extraction.

---

## Known limitations

| Limitation | Impact |
|---|---|
| Dec 31 balance not in ontology | Proxy flagged; advisor must provide exact value for precision |
| YTD withdrawals not in ontology | Must always come from advisor |
| DOB null for Pershing accounts | Advisor must provide via `client_input` |
| `account_type` wrong for some Schwab accounts | Always verify or override via `client_input` |
| Inherited IRA auto-compute requires advisor fields | Needs `beneficiary_dob` + `owner_death_date`; fallback is `MANUAL_REVIEW` |

---

## Quick start

```bash
make test                                                          # 22 core fixtures
make test-parser                                                   # 5 NL parser fixtures
make test-real                                                     # live accounts (requires AWS)
make run-manual-rmd DOB=1950-03-15 TYPE="Traditional IRA" BALANCE=320000 YTD=10000
make run-rmd ACCOUNT_ID=38279295 BALANCE=178399                   # requires AWS + token
```

---

## Fixtures

22 core + 5 NL parser + real-data fixtures.

| # | Scenario | Decision |
|---|---|---|
| 01 | Age 76, Traditional IRA, not started | `RMD_PENDING` |
| 02 | Age 80, Traditional IRA, in progress | `RMD_IN_PROGRESS` |
| 03 | Age 75, Traditional IRA, completed | `RMD_COMPLETE` |
| 04 | Roth IRA | `NO_ACTION` |
| 05 | Age 65 — under 73 | `NO_ACTION` |
| 06 | Missing DOB and balance | `INSUFFICIENT_DATA` |
| 07 | All fields supplied manually | `RMD_PENDING` |
| 08 | Negative balance | `INVALID_INPUT` |
| 09 | Invalid DOB format | `INVALID_INPUT` |
| 10 | Lowercase `traditional ira` | `RMD_PENDING` |
| 11 | Lowercase `roth ira` | `NO_ACTION` |
| 12 | Inherited IRA, no fields | `MANUAL_REVIEW` |
| 13 | Employer Retirement Plan (401k/403b/457b) | `RMD_PENDING` |
| 14 | Age 73 — first RMD year | `RMD_PENDING` |
| 15 | Zero balance | `RMD_COMPLETE` |
| 16 | YTD equals RMD exactly | `RMD_COMPLETE` |
| 17 | SEP IRA | `RMD_PENDING` |
| 18 | Rollover IRA | `RMD_PENDING` |
| 19 | Age 77, < 90 days left | `TAKE_RMD_NOW` |
| 20 | Inherited IRA, non-spouse, owner died 2021 — 10-year rule | `MANUAL_REVIEW` |
| 21 | Inherited IRA, spouse — stretch rule | `MANUAL_REVIEW` |
| 22 | Inherited IRA, no beneficiary fields | `MANUAL_REVIEW` |

---

## Step 1 completion gate

- [x] `make test` → 22/22 pass
- [x] `make test-parser` → 5/5 pass
- [x] Decision enum on every output — Python-controlled
- [x] All schema keys always present — `OUTPUT_SCHEMA` merge in `post_check`
- [x] `data_quality[]` and `completeness` on every output
- [x] LLM removed from main path — pure Python pipeline
- [x] CI gate blocking on fixture failures
- [x] Phoenix tracing wired — `make test-trace`
- [x] Bedrock swap — 3×22/22 pass, p50=5.6s, p95 within 30s threshold
- [x] 3-run stability confirmed (2026-04-22)
- [x] Security eval — 5 injection cases, decision always Python-controlled
- [x] PII check — `social_security_number` never referenced
