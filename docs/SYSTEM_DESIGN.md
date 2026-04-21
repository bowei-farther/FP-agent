# Financial Planning Agent System — System Design

> Last updated: 2026-04-21

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

| Field | Type | Status | Description |
|---|---|---|---|
| `eligible` | bool or None | Built | Whether the strategy applies |
| `reason` | str | Built | Human-readable eligibility explanation |
| `age` | int or None | Built | Client age as of Dec 31 of distribution year |
| `rmd_required_amount` | float or None | Built | Total required withdrawal |
| `withdrawal_amount_ytd` | float | Built | Withdrawals taken so far this year |
| `remaining_rmd` | float or None | Built | Amount still owed |
| `withdrawal_status` | str enum | Built | `Not Started` / `In Progress` / `Completed` / `Not Applicable` |
| `available_cash` | float or None | Built | Cash available for withdrawal |
| `market_value` | float or None | Built | Current portfolio value |
| `cash_covers_remaining` | bool or None | Built | Whether cash covers remaining RMD |
| `flags` | list[str] | Built | Advisor-facing warnings (P7) |
| `client_name` | str or None | Built | Resolved from ontology or input |
| `advisor_name` | str or None | Built | Resolved from ontology or input |
| `_source` | str | Built | Where the data came from (P4) |
| `decision` | str enum | Step 1 Task 2 | Machine-readable action enum — set by Python, never LLM (P10) |
| `missing_fields` | list[str] | Step 1 Task 2 | Fields that could not be resolved |
| `data_quality` | list[str] | Step 1 Task 4 | System-facing named provenance constants (P7) |
| `completeness` | str | Step 1 Task 4 | `full` / `partial` / `minimal` (P4) |
| `input_echo` | dict | Step 1 Task 6 | Exact field values used in the calculation (P4) |

The integration agent receives this contract and nothing else.

### Per-sub-agent pipeline (same pattern for all agents)

```
Advisor input (free text or structured)
        │
        ▼
  NL extraction  (LLM — parse fields only, no reasoning, no guessing)
  [Step 1, Task 8 — not yet built. Currently: structured dict passed directly to evaluate()]
        │
        ▼
  pre_check      (Python — block on missing required data before LLM)
        │
        ▼
  Strands Agent  (LLM — orchestration only)
    ├── get_client_data()  → fetch from ontology, merge with input, return _missing
    └── compute_*()        → financial/IRS math in Python, return result
        │
        ▼
  post_check     (Python — enforce output schema, validate result, set decision)
        │
        ▼
  evaluate() return dict
```

**LLM roles — three separate calls, three separate jobs:**

| Call | Job | Does NOT do |
|---|---|---|
| NL extraction | Parse free text → structured fields | Reason, infer, guess |
| Agent orchestration | Decide tool call order, assemble output | Math, eligibility logic |
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
| YTD withdrawal amount | No transaction history | RMD: advisor must provide |
| DOB for Pershing accounts | People data not yet in ontology | RMD: advisor must provide DOB for Pershing accounts |
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

### Step 2 — ontology-evals integration

Wire fixtures into ontology-evals `config.json`. Add typed assertions:
- `exact_value` for `decision`, `withdrawal_status`
- `field_populated` for `rmd_required_amount` when `eligible=true`
- `tool_called` for `compute_rmd`
- `set_contains` for expected `data_quality` flags

Purpose: structured evaluation with reusable assertion types and CI integration.

### Step 3 — Scientific evaluation

Arize Phoenix for production tracing. LLM-as-judge for output quality.
50+ fixtures covering boundary cases. Hard metrics: accuracy, latency p95, tool call count.

Purpose: measure quality across the full system — not just pass/fail.

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
