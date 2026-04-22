# Financial Planning Agent System — Design

> Last updated: 2026-04-22

---

## Overview

A multi-agent decision support system for Farther wealth management advisors. Each agent is an independent specialist that evaluates one financial strategy and returns a structured recommendation. An integration agent coordinates them, detects conflicts, and presents a unified view to the advisor.

This is a **decision support system** — it surfaces recommendations and flags risks. It never executes financial actions. Advisor confirmation is always required.

---

## Repository Structure

```
financial-planning/
  pyproject.toml               ← single shared environment for all agents
  docs/
    SYSTEM_DESIGN.md           ← this file
    PRINCIPLES.md              ← rules enforced in code
  agents/
    integration/               ← orchestration layer (Step 2)
    rmd/                       ← RMD sub-agent (Step 1 complete)
    roth/                      ← Roth conversion sub-agent (Step 2)
    tlh/                       ← Tax loss harvesting sub-agent (Step 2)
```

One shared `pyproject.toml` — one `uv sync` installs everything. Each sub-agent has its own `Makefile`, fixtures, and package; it can be run and tested independently.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Advisor Interface                    │
│         (CLI today → FastAPI + SSE in Step 2)           │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                    Integration Agent                     │
│  Owns: routing, parallel execution, conflict detection,  │
│        session state, human-in-the-loop gate             │
│  Does NOT: fetch data, compute math, apply strategy      │
└──────┬─────────────────┬─────────────────┬──────────────┘
       │                 │                 │
┌──────▼──────┐  ┌───────▼──────┐  ┌──────▼──────┐
│  RMD Agent  │  │  Roth Agent  │  │  TLH Agent  │
│  evaluate() │  │  evaluate()  │  │  evaluate() │
└──────┬──────┘  └──────────────┘  └─────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────┐
│                   Farther Ontology API                   │
│   object fields (account_type, DOB, name)                │
│   daily fields (balance, cash)                           │
└─────────────────────────────────────────────────────────┘
```

### Sub-agent contract

Every sub-agent exposes exactly one function:

```python
evaluate(auth_token: str, account_id: str, client_input: dict) -> dict
```

Sub-agents are completely isolated — they do not call each other, share state, or import each other's code. Only the integration agent knows multiple sub-agents exist. Adding agent N requires zero changes to agents 1–N-1.

The returned dict always contains all fields (guaranteed by `post_check`):

| Field | Type | Notes |
|---|---|---|
| `decision` | str enum | Python-controlled — never set by LLM |
| `eligible` | bool or null | Core answer |
| `reason` | str | Explains the decision |
| `age` | int or null | For use in explanation |
| `rmd_required_amount` | float or null | Dollar amount |
| `withdrawal_amount_ytd` | float | What's been withdrawn so far |
| `remaining_rmd` | float or null | What's left |
| `withdrawal_status` | str enum | Current state |
| `available_cash` | float or null | Cash available for withdrawal |
| `cash_covers_remaining` | bool or null | Whether cash covers the remaining RMD |
| `flags` | list[str] | Human-readable urgency signals for the advisor |
| `client_name` | str or null | For personalizing the response |
| `missing_fields` | list[str] | What to ask the advisor for |
| `data_quality` | list[str] | Machine-readable provenance constants |
| `completeness` | str | `full` / `partial` / `minimal` |
| `inherited_rule` | str or null | `"10-year"` / `"stretch"` for inherited IRAs |

Output contains exactly what the integration layer needs. Internal fields (`_source`, `input_echo`, `market_value`) are stripped by `post_check` before returning.

### Sub-agent pipeline

```
Advisor input (free text or structured)
        │
        ▼
  NL extraction   — LLM: parse fields only, no reasoning, no guessing
        │
        ▼
  pre_check       — Python: block on missing required data
        │
        ▼
  get_client_data — Python: fetch from ontology, merge with input
        │
        ▼
  compute_*()     — Python: financial math, eligibility logic, decision
        │
        ▼
  post_check      — Python: enforce schema, validate result
        │
        ▼
  return dict     — all fields always present
```

No LLM in the sub-agent main path. Sub-agents are deterministic Python workers. The LLM lives at the integration layer, where it reasons across all sub-agent outputs and produces a single advisor-facing recommendation.

### Integration agent responsibilities (Step 2)

1. **Route** — which sub-agents are relevant for this account
2. **Orchestrate** — call relevant sub-agents in parallel
3. **Conflict detection** — when two recommendations draw from the same resource
4. **Unified output** — merge results into one advisor-facing recommendation

### Development model

Each agent proves itself correct in isolation before connecting to the integration agent. This is a release criterion, not a suggestion.

```
Step 1:  RMD agent → fixtures pass → CI gate → Bedrock swap → ✓ proven
Step 2:  Roth, TLH → same Step 1 process → all proven → integration agent
Step 3:  Agent N → Step 1 process → wire into swarm
```

---

## Data Layer

**Single source of truth: Farther Ontology.** No Athena, no CRM, no mixing. Multiple sources create reconciliation ambiguity — when ontology and Athena disagree on a balance, there is no principled way to decide which is correct.

### Object fields (static account attributes)

| Field | Description |
|---|---|
| `account_type` | "Traditional IRA", "Roth IRA", "SEP IRA", etc. |
| `date_of_birth` | Account holder DOB |
| `first_name`, `last_name` | Account holder name |
| `custodian_account_id` | Custodian account number |

### Daily fields (updated daily)

| Field | ID | Description |
|---|---|---|
| `account_balance` | 277 | Total account value |
| `account_available_cash` | 1301 | Cash available for withdrawal |
| `account_market_value` | 1303 | Positions only, excluding cash |
| `account_unrealized_lt_gains` | 1061 | Unrealized long-term gains |
| `account_unrealized_st_gains` | 1062 | Unrealized short-term gains |

### Known ontology gaps

| Data | Impact |
|---|---|
| Dec 31 prior year balance — only latest available | RMD: advisor must provide; proxy flagged with `USING_LATEST_BALANCE_AS_PROXY` |
| YTD withdrawal amount — no transaction history | RMD: must always come from advisor |
| DOB missing for Pershing accounts | Advisor must provide for Pershing clients |
| `account_type` wrong for some Schwab accounts | Agent can return wrong decision silently — always verify or override |
| Inherited IRA beneficiary fields not stored | Auto-compute requires advisor input; fallback is `MANUAL_REVIEW` |
| `federal_tax_bracket` not in ontology | Blocks Roth, TLH, and most planned agents |

---

## Principles

Full details: [PRINCIPLES.md](PRINCIPLES.md)

| Principle | Rule |
|---|---|
| P1 | LLM does orchestration only — never math |
| P2 | No silent fallback — missing data is always surfaced |
| P3 | Python owns the output schema — all keys always present |
| P4 | Data provenance on every output |
| P5 | Conservative default on ambiguity |
| P6 | Single data source — ontology only |
| P7 | Separate advisor signals from system signals |
| P8 | Correctness before features |
| P9 | Sub-agents are strictly isolated |
| P10 | Decision enum is Python-controlled — never LLM |
| P11 | Ask one field at a time |
| P12 | Identity resolution before compute |
| P13 | Observe before you ship — Phoenix traces required |
| P14 | Prove stability before integration |
| P15 | Dumb workers, smart coordinator — no LLM in sub-agent main path |
| P16 | Input and output cover what is needed, nothing more |
| P17 | Financial rounding uses ROUND_HALF_UP |

---

## Design Notes

**Why `decision` is Python-controlled:** `decision` drives the UI and conflict detector. If the LLM sets it, it may contradict the math, fall outside the valid enum, or be based on reasoning that diverges from the verified computation. Python sets `decision` from verified field values after `compute_rmd()` completes.

**Why `pre_check` AND `post_check`:** A system prompt is a suggestion. A Python guard is deterministic — `post_check` runs after the agent and overrides any unsafe result regardless of what the model produced.

**Why auth credentials are not in tool arguments:** Auth tokens appear in traces, logs, and model context. Closures capture `auth_token` and `account_id` invisibly — the LLM calls the tool with no credentials in the argument list.

**Why `withdrawal_amount_ytd` requires advisor input:** No reliable automated source exists in the ontology. Using an unverified Athena source and presenting it as fact is worse than asking the advisor directly.

---

## What is not built yet

| Gap | Plan |
|---|---|
| Roth and TLH agents | Step 2 — same Step 1 process as RMD |
| Integration agent | Step 2 — after Roth and TLH are proven |
| Session state (DynamoDB) | Step 2 |
| FastAPI + SSE streaming | Step 2 |
| `federal_tax_bracket` in ontology | CRM field addition — unlocks most Step 3 agents |
