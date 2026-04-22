# Financial Planning Agent — Build Plan

> Last updated: 2026-04-22
> Author: Bowei Wang

Principles: [docs/PRINCIPLES.md](docs/PRINCIPLES.md) — P1–P17, enforced in code.  
Architecture: [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md)

---

## Step 1 — RMD Agent

**Status: Complete** — [agents/rmd/README.md](agents/rmd/README.md)

- [x] 22/22 fixtures pass, 5/5 NL parser fixtures pass
- [x] Decision enum, schema enforcement, data quality on every output
- [x] LLM removed from main path — pure Python pipeline
- [x] CI gate, Phoenix tracing, Bedrock swap, 3-run stability
- [x] Security eval (5 injection cases), PII check
- [x] `make test-real` → 21 real-data fixtures pass (Schwab, Fidelity, Pershing)

---

## Blocking Questions — Before Step 2

| Question | Impact if No |
|---|---|
| YTD withdrawal data going into ontology? | Manual forever or Data Lake APIs |
| Dec 31 balance snapshot going into ontology? | `USING_LATEST_BALANCE_AS_PROXY` is permanent |
| Inherited IRA: prior owner DOB/DOD accessible? | `MANUAL_REVIEW` is permanent for inherited IRAs |
| Schwab "Individual" accounts — taxable or misclassified IRA? | Agent cannot be trusted for Schwab without confirmation |
| Team planning to use Data Lake APIs from agents? | May require revising P6 before Step 2 |

---

## Step 2 — Integrated Advisor

**Status: Not started**

Prerequisites: blocking questions resolved, each sub-agent independently passes Step 1 gate.

### Tasks

**2A — Additional sub-agents**  
Each sub-agent follows the same Step 1 process independently before connecting to the integration agent.

**2B — Conflict schema**  
Surface `conflicts_with` + `conflict_reason` when two strategies draw from the same account.

**2C — Router**  
Account-type filter before dispatch. Conflict detection after all sub-agents return.

**2D — Swarm orchestrator**  
Strands Graph: parallel sub-agent execution, single LLM synthesis call.

**2E — Session state**  
DynamoDB. PK: `session_id` / SK: `account_id`. Fields: DOB, account_type, names. TTL: 4h.

**2F — Human-in-the-loop gate**  
No action executes without explicit advisor confirmation.

**2G — Streaming**  
FastAPI + SSE. First token within 500ms.

**2H — Bedrock on all agents**  
Switch all to `BedrockModel`. CI uses AWS OIDC.

### Completion gate

- [ ] All sub-agents independently pass Step 1 gate
- [ ] Conflict detection works for same-account case
- [ ] Swarm: unified result in < 5s
- [ ] Session state: DOB not asked twice in same session
- [ ] Human-in-the-loop gate active
- [ ] Streaming: first token < 500ms
- [ ] Bedrock on all agents

### Pre-launch — Shadow Mode

- [ ] Read-only parallel run against real advisor workflow for 1 week
- [ ] Review: ERROR rate, MANUAL_REVIEW rate, unexpected INSUFFICIENT_DATA
- [ ] Advisor team sign-off

---

## Step 3 — Evaluation + More Sub-agents

**Status: Not started**

**3A — Evaluation agent**  
LLM-as-judge or rubric-based scoring against ground-truth fixtures. Regression gate: score drop blocks merge.

**3B — Expand fixtures to 50+**  
All IRS age boundaries, all account types, all cash/time-pressure scenarios, multi-account, NL edge cases.

**3C — Additional sub-agents**

Each follows the same Step 1 process. Priority order by data availability:

| Agent | Unlock condition |
|---|---|
| Cash Drag Detector | CRM `ips_cash_target` |
| QCD Recommender | CRM `charitable_intent` |
| TLH | Lot-level data + CRM `federal_tax_bracket` |
| Withdrawal Sequencing | Financial plan integration + CRM fields |
| Roth Conversion | CRM `federal_tax_bracket` |
| Holding Period | Lot-level data |
| Asset Location | Market data vendor |
| Appreciated Asset | CRM fields |
| Muni Bond | Market data vendor fix |
| Dividend Treatment | Position-level data |
| Borrow vs. Sell | CRM + custodian API |
| Step-Up in Basis | Lot-level data + CRM |
| HSA | CRM fields |
| QOZ | Transaction-level data |
| NUA | Plan administrator feed |

**Single unlock that matters most:** CRM `federal_tax_bracket` unblocks 9 of these.

---

## What is Never Built

| Item | Reason |
|---|---|
| LLM computing financial math | IRS penalty is 25% of shortfall — not a place for approximation |
| Auto-execute money movement | Human-in-the-loop is non-negotiable |
| Numeric confidence scores | Deterministic system — named flags carry the same information without implying probability |
| Cross-client memory | PII contamination risk — session state is per-client, TTL-gated |
| Athena as primary source | Multi-source reconciliation is unresolvable reliably |
| Silent fallback | Automation bias — system looks correct while data is wrong |

---

## Decisions Made

| Decision | Alternative | Reason |
|---|---|---|
| Strands SDK | Proteus BedrockAgentResolver | Simpler, local-dev friendly, swappable to Bedrock |
| Auth token via closure | Token as tool argument | Credentials must never appear in tool call arguments |
| `pre_check` / `post_check` in Python | System prompt guards | System prompt is a suggestion; Python is deterministic |
| `account_balance` (277) for Dec 31 proxy | `account_market_value` (1303) | IRS uses full account value including cash |
| Inherited IRA → `MANUAL_REVIEW` | `eligible=False` | Inherited IRAs have RMD requirements — different rules, not no rules |
| `DISTRIBUTION_YEAR = date.today().year` | Pinned constant | Pinned constant silently breaks on Jan 1 |
| `frozenset` for account type lookup | `if/elif` chain | O(1), immutable, explicit — prevents accidental mutation |
| Ontology only | Ontology + Athena | One source = one truth = one failure mode |
| `decision` in Python | LLM free text | Machine-readable signal for UI and orchestrator — LLM must not control it |
| `data_quality[]` separate from `flags[]` | One combined list | Different consumers: systems vs. advisors |
| `INVALID_INPUT` distinct from `ERROR` | Single `ERROR` | Different owners: caller vs. engineering |
| Observability in Step 1 | Defer to Step 2 | Pass/fail is blind — traces required to know *why* a fixture passes |
| Phoenix over CloudWatch | CloudWatch | CloudWatch measures infra; Phoenix measures agent correctness |
| Latency baseline in Step 1 | Measure later | No baseline = no way to detect Bedrock swap regression |
| 3-run stability gate | Single run | `temperature=0` reduces variance, does not eliminate it |
| No numeric confidence scores | `"confidence": 0.82` | Implies probabilistic reasoning in a deterministic system |
| LLM at integration layer only | LLM in each sub-agent | One synthesis call at the top vs. N calls paying latency for deterministic work |
| Strands Graph at integration layer | Graph inside sub-agents | Graph is for parallel multi-agent execution — wrong abstraction inside a single deterministic worker |
