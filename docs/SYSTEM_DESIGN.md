# Financial Planning Agent System — System Design

> Last updated: 2026-04-22 (Step 1 complete)

---

## 1. Overview

A multi-agent decision support system for Farther wealth management advisors.
Each agent is an independent specialist that evaluates one financial strategy and returns
a structured recommendation. An integration agent coordinates them, detects conflicts,
and presents a unified view to the advisor.

This is a **decision support system** — it surfaces recommendations and flags risks.
It never executes financial actions. Advisor confirmation is always required.

---

## 2. Repository Structure

```
financial-planning/
  pyproject.toml               ← single shared environment for all agents
  README.md                    ← entry point and documentation guide
  PLAN.md                      ← execution plan: output schema, tasks, gates
  docs/
    SYSTEM_DESIGN.md           ← architecture, boundaries, rationale (this file)
    PRINCIPLES.md              ← non-negotiable rules, enforced in code
  agents/
    integration/               ← orchestration layer (Step 2)
      README.md
    rmd/                       ← RMD sub-agent (Step 1)
      README.md
      core/                    ← agent package (tools, rules, agent, prompts)
      prompts/                 ← test fixtures
      run_tests.py
      Makefile
      agent.py                 ← CLI entry point
    roth/                      ← Roth conversion sub-agent (Step 2)
    tlh/                       ← Tax loss harvesting sub-agent (Step 2)
```

**Why one shared environment:** A single `pyproject.toml` at the repo root means one `uv sync` installs everything. All agents share the same dependency versions — no drift, no per-agent setup overhead. Separate environments only make sense if a real dependency conflict appears, which is a later-stage optimization for 16+ agents.

**Why agents are isolated directories:** Each sub-agent has its own `Makefile`, fixtures, and package. It can be run and tested independently with no knowledge of the others. Adding a new agent is additive — it gets its own directory. No existing file changes.

**Why integration is at the same level as sub-agents:** `agents/integration/` and `agents/rmd/` are siblings, not parent/child. The integration agent does not own or contain the sub-agents. It calls them. Same directory level reflects same abstraction level — they are all agents, with different responsibilities.

**Why docs are separate from agents:** `docs/` contains system-level thinking that spans all agents. `PRINCIPLES.md` applies to every agent equally — it belongs at the system level, not inside any single agent's folder. Each agent's `README.md` documents that agent only, and links up to `docs/` for system-level context.

---

## 3. Architecture

### System layers

```
┌──────────────────────────────────────────────────────────────────┐
│                        Advisor Interface                         │
│          (CLI today → FastAPI + SSE streaming in Step 2)         │
└─────────────────────────────┬────────────────────────────────────┘
                              │
┌─────────────────────────────▼────────────────────────────────────┐
│                      Integration Agent                           │
│                                                                  │
│  Owns: routing, parallel execution, conflict detection,          │
│        session state, human-in-the-loop gate                     │
│                                                                  │
│  Does NOT: fetch data, compute math, apply strategy logic        │
└──────┬──────────────────────┬──────────────────────┬─────────────┘
       │                      │                      │
       │     ← strict boundary: sub-agents are black boxes →
       │                      │                      │
┌──────▼──────┐      ┌────────▼──────┐      ┌────────▼──────┐
│  RMD Agent  │      │  Roth Agent   │      │  TLH Agent    │
│  sub-agent  │      │  sub-agent    │      │  sub-agent    │
│             │      │  (Step 2)     │      │  (Step 2)     │
│ evaluate()  │      │  evaluate()   │      │  evaluate()   │
└──────┬──────┘      └───────────────┘      └───────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Farther Ontology API                          │
│                  (single source of truth)                        │
│     object fields (account_type, DOB, name)                      │
│     daily fields (balance, cash, market_value)                   │
└──────────────────────────────────────────────────────────────────┘
```

### The strict boundary

Sub-agents and the integration agent are completely separate systems.

| | Sub-agent | Integration agent |
|---|---|---|
| Knows about other sub-agents | Never | Yes — calls them |
| Shares state with other sub-agents | Never | Manages it |
| Fetches data from ontology | Yes | No |
| Does financial math | Yes | No |
| Has its own evaluate() | Yes | No — only orchestrates |
| Can be proven correct in isolation | Yes — required | No |
| Can be added without changing others | Yes | No |

This is an architectural requirement, not a convention.
A bug in one sub-agent cannot affect another. Adding agent N requires zero changes to agents 1–N-1.

### Integration agent responsibilities (Step 2)

The integration agent owns exactly four things:

1. **Route** — which sub-agents are relevant for this client and account type
2. **Orchestrate** — call relevant sub-agents in parallel (swarm)
3. **Conflict detection** — when two recommendations draw from the same resource
4. **Unified output** — merge results into one advisor-facing recommendation

### Sub-agent contract

Every sub-agent exposes exactly one function:

```python
evaluate(auth_token: str, account_id: str, client_input: dict) -> dict
```

The returned dict always contains (guaranteed by `post_check`). Full schema defined in [PLAN.md](../PLAN.md#output-schema-every-field-always-present).

| Field | Type | Purpose for reasoning layer |
|---|---|---|
| `decision` | str enum | Primary routing signal — Python-controlled, never LLM (P10) |
| `eligible` | bool or null | Core answer |
| `reason` | str | Explain the decision |
| `age` | int or null | "At age 87, factor is 14.4..." |
| `rmd_required_amount` | float or null | Dollar amount to cite |
| `withdrawal_amount_ytd` | float | What's been done so far |
| `remaining_rmd` | float or null | What's left to act on |
| `withdrawal_status` | str enum | State to explain |
| `available_cash` | float or null | "You have $X available" |
| `cash_covers_remaining` | bool or null | Cash covers / doesn't cover remaining |
| `flags` | list[str] | Urgency signals to communicate (P7) |
| `client_name` | str or null | Personalize the response |
| `missing_fields` | list[str] | What to ask the advisor for |
| `data_quality` | list[str] | Confidence of explanation — advisor-provided vs DB (P7) |
| `completeness` | str | How confident to sound (`full` / `partial` / `minimal`) |
| `inherited_rule` | str or null | `"10-year"` / `"stretch"` — which inherited IRA rule applies |

**P16 — input and output cover what is needed, nothing more:** fields contain exactly what the reasoning layer needs.
Internal fields (`_source`, `input_echo`, `market_value`, `advisor_name`) are stripped by `post_check`.

The integration agent receives this contract and nothing else.

### Per-sub-agent pipeline (same pattern for all agents)

```
Advisor input (free text or structured)
        │
        ▼
  NL extraction  (LLM — parse fields only, no reasoning, no guessing)
  [parser.py — built for RMD, same pattern for Roth/TLH]
        │
        ▼
  pre_check      (Python — block on missing required data, manual-input path only)
        │
        ▼
  get_client_data()  (Python — fetch from ontology, merge with input, return _missing)
        │
        ▼
  compute_*()        (Python — financial/IRS math, eligibility logic, decision enum)
        │
        ▼
  post_check     (Python — enforce output schema, validate result)
        │
        ▼
  evaluate() return dict
```

No LLM in the sub-agent main path (P15). Sub-agents are deterministic Python workers.
LLM lives at the integration layer only, where it reasons across all sub-agent outputs.

**LLM roles:**

| Call | Where | Job | Does NOT do |
|---|---|---|---|
| NL extraction | `parser.py` (sub-agent input layer) | Parse free text → structured fields | Reason, infer, guess |
| Integration synthesis | Integration agent (Step 2) | Conflict detection, prioritization, advisor explanation | Math, eligibility logic |
| Explanation (optional) | Structured result → plain English | Change any values |

### Development model — one agent at a time

```
Step 1:  RMD agent
           ↓ 13+ fixtures pass
           ↓ CI gate
           ↓ output schema enforced
           ↓ Bedrock swap
           ↓ ✓ proven in isolation
         ──────────────────────────────
Step 2:  Roth agent  → same Step 1 process → ✓ proven
         TLH agent   → same Step 1 process → ✓ proven
           ↓ all three proven
         Integration agent wires them → conflict detection → swarm
         ──────────────────────────────
Step 3:  Agent 4 → Step 1 process → wire into swarm
         Agent 5 → ...
         Agent N → ...
```

No agent is wired into the integration agent until it independently passes its Step 1 gate.
This is a release criterion, not a suggestion.

---

## 4. Data Layer

### Single source of truth: Farther Ontology

One data source. No Athena, no CRM, no mixing.

**Why:** Multiple sources create reconciliation ambiguity. If ontology and Athena disagree on a balance, the system has no principled way to decide which is correct. One source = one truth = one failure mode = one auth pattern.

### What the ontology provides

**Object fields** (static account attributes):

| Field | Description | Custodian coverage |
|---|---|---|
| `account_type` | "Traditional IRA", "Roth IRA", "SEP IRA", etc. — string, no FK mapping needed | All |
| `date_of_birth` | Account holder DOB | Schwab, Fidelity — Pershing pending |
| `first_name`, `last_name` | Account holder name | All |
| `manager` | Advisor name | Partial |
| `custodian_account_id` | Custodian account number | All |
| `farther_virtual_account_id` | Farther internal ID | All |

**Daily fields** (updated daily):

| Field | ID | Description |
|---|---|---|
| `account_balance` | 277 | Total account value including cash |
| `account_available_cash` | 1301 | Cash available for withdrawal |
| `account_settled_cash` | 1302 | T+0 settled cash |
| `account_market_value` | 1303 | Positions only, excluding cash |
| `account_sweep_balance` | 1436 | Core sweep fund |
| `account_cost_basis` | 1057 | Total cost basis |
| `account_unrealized_lt_gains` | 1061 | Unrealized long-term gains |
| `account_unrealized_st_gains` | 1062 | Unrealized short-term gains |
| `account_ytd_lt_realized_gain` | 1065 | YTD realized long-term gains |
| `account_ytd_st_realized_gain` | 1066 | YTD realized short-term gains |

### What the ontology cannot provide

| Data | Why missing | Impact |
|---|---|---|
| Dec 31 prior year balance | Only latest balance — no point-in-time snapshot | RMD: advisor must provide; proxy used with warning flag |
| YTD withdrawal amount | No transaction history | RMD: must always come from advisor |
| `manager` (advisor name) | Never populated | Removed from output entirely |
| DOB for Pershing accounts | People data not yet in ontology | RMD: advisor must provide for Pershing clients |
| `account_type` accuracy | Wrong for some Schwab accounts (e.g. Rollover IRA stored as Roth IRA) | Critical: agent returns wrong decision silently — always verify or override |
| Inherited IRA beneficiary fields | Death date, relationship not stored | Inherited IRA auto-compute requires advisor input; fallback is `MANUAL_REVIEW` |
| Marginal tax rate | Not in ontology | Blocks Roth, TLH, and 9 of 13 planned agents |
| Lot-level cost basis / purchase date | Not in ontology | Blocks full TLH, Holding Period, Step-Up agents |

---

## 5. Principles

> Full principles with enforcement details: [PRINCIPLES.md](PRINCIPLES.md)

| Principle | Rule |
|---|---|
| P1 | LLM does orchestration only — never math |
| P2 | No silent fallback — missing data is always surfaced |
| P3 | Python owns the output schema — all keys always present |
| P4 | Data provenance on every output — every number is traceable |
| P5 | Conservative default on ambiguity — no_action when uncertain |
| P6 | Single data source — ontology only |
| P7 | Separate advisor signals from system signals |
| P8 | Correctness before features — CI gate enforces this |
| P9 | Sub-agents are strictly isolated — no cross-imports, no shared state |
| P10 | Decision enum is Python-controlled — never written by LLM |
| P11 | Ask one field at a time — priority order, never multiple at once |
| P12 | Identity resolution before compute — never proceed on ambiguous match |
| P13 | Observe before you ship — Phoenix traces required before Step 1 gate |
| P14 | Prove stability before integration — 3-run stability + latency baseline required |
| P15 | Dumb workers, smart coordinator — no LLM in sub-agent main path |
| P16 | Input and output cover what is needed, nothing more — no internal fields in public contracts |

---

## 6. Design Rationale and Tradeoffs

### Why Strands SDK, not Proteus

Proteus is Farther's production chatbot — FastAPI + BedrockAgentResolver + Lambda + DynamoDB + SSE. It is a full production system optimized for conversational UX.

This system is a decision support tool, not a conversational tool. It needs:
- Deterministic pipelines with Python safety guards
- Observable tool calls (what arguments did the LLM actually pass?)
- Local testing without Lambda deployment
- Swappable model providers (Anthropic → Bedrock is one line)

**Tradeoff:** Strands doesn't have Proteus's DynamoDB session or SSE streaming out of the box. These are added in Step 2. Proteus's complexity is not warranted at Step 1.

### Why temperature=0

Same input must produce same output. temperature=0 maximizes the probability of the most likely token at each step, making outputs as deterministic as possible within an LLM.

**Tradeoff:** temperature=0 does not guarantee identical outputs — it maximizes likelihood, not determinism. True reproducibility for the financial math is achieved by keeping it in Python, not by relying on temperature.

### Why `build_tools()` closure factory

Auth credentials must never appear in tool call arguments — the LLM sees arguments and they can appear in traces, logs, or outputs. Closures capture `auth_token` and `account_id` invisibly. The LLM calls `get_client_data()` with no arguments.

### Why `pre_check` AND `post_check` both in Python

A system prompt instruction is a suggestion to the LLM. It can be ignored or misinterpreted.
A Python guard is deterministic — `post_check` runs after the agent and overrides any unsafe result regardless of what the model produced.

### Why manual input for `withdrawal_amount_ytd`

No reliable automated source exists in the ontology today. Using an unverified Athena source and presenting it as fact is worse than asking the advisor — it creates silent wrong answers. The NL input layer makes asking for this field low friction.

### Why `decision` is a Python enum, not LLM text

`decision` drives the UI and conflict detector. If the LLM sets it, it may contradict the Python math, fall outside the valid enum, or reflect reasoning that diverges from the verified computation. Python sets `decision` from verified field values after `compute_rmd()` completes.

### What is strong

- Deterministic boundary is explicit and enforced — no ambiguity about what LLM does vs Python
- Output schema is complete — no missing fields silently dropped
- Safety guards are structural — `pre_check` / `post_check` cannot be bypassed
- Single data source eliminates reconciliation complexity
- Sub-agent isolation means adding agent N costs zero changes to agents 1–N-1
- Fixture coverage is meaningful — all eligibility branches and error cases

### What is missing (current gaps)

| Gap | Severity | Plan |
|---|---|---|
| `withdrawal_amount_ytd` requires advisor input | High | Remains advisor input until ontology exposes transaction history |
| Dec 31 balance requires advisor input | High | Proxy with flag; exact value requires advisor confirmation |
| DOB missing for Pershing accounts | Medium | Resolved when people data lands in ontology |
| NL input layer not yet built | Medium | Step 1 Task 8 |
| Inherited IRA cannot be auto-evaluated | Medium | Always manual review — no automated source for death date |
| `federal_tax_bracket` not in ontology | Critical for Steps 2–3 | CRM field addition required — unlocks 9 of 13 planned agents |

---

## 7. Evaluation Strategy

### Step 1 — Fixture correctness

13+ fixtures per agent covering all eligibility branches and error cases.
Run with `make test`. CI gate blocks merges on failure.

Purpose: prove the deterministic core is correct before adding any capability.

### Step 2 — ontology-evals + Phoenix

Wire fixtures into ontology-evals `config.json`. Assertions evaluated automatically, results uploaded to Arize Phoenix for tracing.

Assertion types used:
- `exact_value` for `decision`, `withdrawal_status`
- `field_populated` for `rmd_required_amount` when `eligible=true`
- `tool_called` for `compute_rmd`
- `set_subset` for expected `data_quality` flags
- `max_turns` and `max_latency_s` for performance bounds
- LLM-as-judge for output quality ("Is the recommendation consistent with age and account type?")

Purpose: per-fixture pass/fail with full tool call traces, latency, and decision distribution visible in Phoenix.

### Step 3 — Expanded evaluation

50+ fixtures covering all IRS age boundaries, account types, cash coverage scenarios, and time pressure cases. LLM-as-judge criteria expanded to cover `flags[]` correctness and `data_quality` provenance.

Purpose: regression safety net as agents are added and wired into the swarm.

### Key evaluation principle

Retrieval evaluation (did we get the right data?) is separate from answer evaluation
(did we produce the right recommendation?). Different failure modes require different test cases.

---

## 8. Future Evolution

### Step 2 — Integrated advisor

- Roth and TLH agents proven correct independently (same Step 1 process as RMD)
- Recommendation schema with `conflicts_with` field
- Integration agent: router + swarm + conflict detection
- Session state: DynamoDB, 4-hour TTL, per-client scoping
- Human-in-the-loop: advisor confirms before any actionable recommendation
- FastAPI + SSE streaming

### Step 3 — Scale

- Graph pipeline: chained decisions (RMD result feeds QCD Recommender)
- Agents 4–16 as data gaps resolve
- The single most important unlock: CRM `federal_tax_bracket` field unblocks 9 of 13 remaining agents

### What never changes

- LLM does not compute financial math
- No auto-execution of financial actions
- No cross-client memory contamination
- No silent fallback to default values
- Ontology as single data source
- Sub-agents never know about each other
