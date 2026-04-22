# Financial Planning Agent Platform — System Design

> Last updated: 2026-04-22

---

## Core Insight

Separate interpretation from decisioning.

- **LLM** — translates natural language into structured input
- **Python** — performs all financial computation and decision logic

Same inputs always produce identical output. No model-driven financial risk.

---

## What This System Is

A deterministic multi-agent platform for financial decision support. Each sub-agent evaluates exactly one strategy and returns structured, verifiable output. An integration agent selects relevant sub-agents, executes them in parallel, detects conflicts, and produces a unified recommendation.

**Advisory only.** The system never executes financial actions. Advisor confirmation is always required.

---

## Architecture

```
Advisor Input
      │
      ▼
Integration Agent
  routing · parallel execution · conflict detection · aggregation
      │
      ├─────────────────┬─────────────────┐
      ▼                 ▼                 ▼
 Sub-agent A       Sub-agent B       Sub-agent N
 evaluate()        evaluate()        evaluate()
      │                 │                 │
      └─────────────────┴─────────────────┘
                        │
                        ▼
              Farther Ontology API
            (single source of truth)
```

---

## System Contracts

### Sub-agent contract

```python
evaluate(auth_token: str, account_id: str, client_input: dict) -> dict
```

Every sub-agent is: stateless, isolated, deterministic, no cross-agent communication.

Adding a new sub-agent is strictly additive — zero changes to existing agents.

### Integration agent contract

Owns: routing, parallel execution, conflict detection, final output.  
Does not: compute financial logic, fetch raw data, override sub-agent decisions.

### Sub-agent pipeline

```
NL extraction   — LLM: free text → structured fields
pre_check       — Python: block on missing required data
get_client_data — Python: fetch from ontology, merge with input
compute_*()     — Python: financial math, eligibility, decision
post_check      — Python: enforce schema, validate result
return dict     — all fields always present
```

No LLM in the sub-agent main path.

---

## Data Layer

**Single source of truth: Farther Ontology.** No Athena, no CRM, no mixing.

Conflicting sources cannot be reconciled reliably. Silent reconciliation destroys trust. Tradeoff: lower coverage, higher correctness.

### Object fields

| Field | Description |
|---|---|
| `account_type` | "Traditional IRA", "Roth IRA", "SEP IRA", etc. |
| `date_of_birth` | Account holder DOB |
| `first_name`, `last_name` | Account holder name |
| `custodian_account_id` | Custodian account number |

### Daily fields

| Field | ID | Description |
|---|---|---|
| `account_balance` | 277 | Total account value |
| `account_available_cash` | 1301 | Cash available for withdrawal |
| `account_market_value` | 1303 | Positions only, excluding cash |
| `account_unrealized_lt_gains` | 1061 | Unrealized long-term gains |
| `account_unrealized_st_gains` | 1062 | Unrealized short-term gains |

### Known gaps

| Data | Impact |
|---|---|
| Dec 31 prior year balance — only latest available | Proxy used and flagged; advisor must provide exact value |
| YTD withdrawal amount — no transaction history | Must always come from advisor |
| DOB missing for Pershing accounts | Advisor must provide |
| `account_type` wrong for some Schwab accounts | Agent can return wrong decision — always verify or override |
| Inherited IRA beneficiary fields not stored | Auto-compute requires advisor input; fallback is `MANUAL_REVIEW` |
| `federal_tax_bracket` not in ontology | Blocks most planned sub-agents |

---

## Reliability Model

### Error taxonomy

| Decision | Meaning |
|---|---|
| `TAKE_RMD_NOW` | Eligible; deadline approaching or passed |
| `RMD_IN_PROGRESS` | Partial withdrawal recorded; remainder still due |
| `RMD_PENDING` | Eligible; deadline not near |
| `RMD_COMPLETE` | Full obligation satisfied |
| `NO_ACTION` | Account is not eligible |
| `MANUAL_REVIEW` | Cannot determine eligibility; advisor must review |
| `INSUFFICIENT_DATA` | Required field missing from all sources |
| `INVALID_INPUT` | Caller-supplied data is structurally invalid |
| `ERROR` | Internal exception — system bug, not a data issue |

`INVALID_INPUT` = caller error, actionable by caller.  
`INSUFFICIENT_DATA` = data gap, actionable by advisor.  
`ERROR` = system failure, actionable by engineering.  
No silent fallback. Ever.

### Data quality model

Every response includes `completeness` and `data_quality[]`.

| completeness | Condition |
|---|---|
| `full` | All fields from ontology, no proxies |
| `partial` | Balance proxy used, or advisor-provided values |
| `minimal` | Required fields missing |

`data_quality[]` is machine-readable provenance (e.g. `USING_LATEST_BALANCE_AS_PROXY`, `DOB_FROM_INPUT`). Downstream systems gate on flags without parsing strings.

### Failure handling

| Scenario | Behavior |
|---|---|
| Missing data | Block or downgrade — never infer silently |
| Ambiguous identity | Hard fail — require disambiguation before compute |
| Conflicting strategies | Surface at coordinator — no automatic resolution |
| System error | Isolated, observable, never masked as a data issue |

---

## Observability

Every LLM call and agent execution is traced to Phoenix (OpenTelemetry).

| Span | Project | Contents |
|---|---|---|
| `rmd.evaluate` | `rmd-agent` | account_id, decision, completeness, test result |
| `nl-parse` | `ontology-eval` | raw text, extracted fields, LLM input/output/tokens |
| `bedrock.converse` | `ontology-eval` | prompt, response, token counts, model ID |

Latency SLO: NL parser p95 < 30s. Measured per run; printed as p50/p75/p95/max/mean.

LLM calls are first-class observable events, not black boxes.

---

## Scalability

Sub-agents are stateless → horizontally scalable.  
Execution is parallel → latency bounded by slowest agent.  
Architecture scales linearly with number of strategies.

Adding a new sub-agent:

1. Create `agents/<name>/core/` with the standard pipeline
2. Expose `evaluate(token, account_id, client_input) -> dict`
3. Write fixtures, add CI target, wire traces
4. Connect to integration agent — zero changes to other sub-agents

---

## Development Model

Prove correctness in isolation before integrating.

```
Step 1  Sub-agent → fixtures pass → CI gate → traces baseline → proven
Step 2  Additional sub-agents proven → integration agent built
Step 3  Evaluation agent → automated scoring → regression gate
```

Each step is fully functional in production independently of whether the next step exists.

---

## Key Tradeoffs

| Tradeoff | Choice | Reason |
|---|---|---|
| Determinism vs. flexibility | Determinism | Financial correctness outweighs model creativity |
| Single source vs. broader coverage | Single source | Consistency over coverage — silent reconciliation is worse than a gap |
| Sub-agent isolation vs. code reuse | Isolation | Independently testable and replaceable; bugs don't compound |
| Explicit failure vs. graceful degradation | Explicit failure | Hidden errors in financial systems are unacceptable |
| Smart coordinator vs. distributed logic | Smart coordinator | Isolates complexity; sub-agents stay simple and fast |
| LLM in sub-agents vs. LLM at integration layer | Integration layer only | Sub-agents are deterministic workers; LLM reasoning belongs at synthesis |

---

## Principles

Full details: [PRINCIPLES.md](PRINCIPLES.md)

| # | Rule |
|---|---|
| P1 | LLM never performs financial math |
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
| P13 | Observe before you ship |
| P14 | Prove stability before integration |
| P15 | Dumb workers, smart coordinator |
| P16 | Input and output cover what is needed, nothing more |
| P17 | Financial rounding uses ROUND_HALF_UP |
