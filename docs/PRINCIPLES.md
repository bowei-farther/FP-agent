# Financial Planning Agent System — Principles

These rules apply to every agent in this system without exception.
They are enforced structurally in code — not by convention or trust.
When adding a new agent, verify each principle is upheld before connecting it to the integration agent.

---

## P1 — LLM does orchestration only, never math

**Rule:** All financial calculations happen in Python. The LLM calls tools; it never computes numbers itself.

**Why:** LLMs are stochastic text predictors — they produce statistically likely output, not arithmetically correct output. The IRS penalty for a missed RMD is 25% of the shortfall. One wrong number = real financial harm. Reproducibility requires determinism: same input must always produce same output.

**Enforced by:** `compute_rmd()` and all financial logic are Python functions. The LLM calls them as tools but cannot produce their outputs. Financial math never appears in system prompts or LLM-generated text.

---

## P2 — No silent fallback

**Rule:** When data is missing, the system surfaces it explicitly. It never substitutes a default and proceeds as if the data were present.

**Why:** Silent fallbacks create automation bias — the system looks correct but the data is wrong. In financial systems this is more dangerous than failing loudly, because the advisor acts on incorrect output without knowing it.

**Enforced by:** `_missing` list returned by `get_client_data`, `pre_check` blocks before the agent runs, named flags (`USING_LATEST_BALANCE_AS_PROXY`, `USER_PROVIDED_WITHDRAWAL_YTD`) when proxy or unverified data is used.

---

## P3 — Python owns the output schema

**Rule:** The shape of every output is defined and enforced by Python. The LLM cannot add, remove, or rename fields. Every key is always present.

**Why:** LLMs produce probabilistically likely text. Output structure depends on prompt and sampling — not guaranteed by a type system. A well-formed result today can become malformed after a prompt change or model update.

**Enforced by:** `OUTPUT_SCHEMA` dict in each agent's `rules.py`. `post_check` merges every agent result into this schema — missing keys get their defaults. No field is ever absent.

---

## P4 — Data provenance on every output

**Rule:** Every output must be traceable to its source. The advisor must always be able to answer "where did this number come from?"

**Why:** Required for compliance auditing. Essential for debugging — when a result is wrong, you need to know whether the problem is in the data, the math, or the LLM.

**Enforced by:** `_source` field on every output, `data_quality[]` named constants, `completeness` field (`full` / `partial` / `minimal`).

---

## P5 — Conservative default on ambiguity

**Rule:** When the system cannot determine a correct answer confidently, it returns the most conservative response — ask for more data, flag for manual review, or return `no_action`. It never silently passes an uncertain case as correct.

**Why:** Asymmetric cost of errors. A false negative (flagging unnecessarily) has low cost. A false positive (confident wrong recommendation) has high cost — IRS penalty, legal exposure, client harm.

**Enforced by:** `pre_check` returns `INSUFFICIENT_DATA` rather than proceeding. `MANUAL_REVIEW` for cases the standard logic cannot handle (e.g. Inherited IRA). Proxy data flagged, not hidden.

---

## P6 — Single data source

**Rule:** Farther Ontology only. No mixing with Athena, CRM, external APIs, or any other source in the same evaluation.

**Why:** Multiple sources create reconciliation ambiguity. If two sources disagree, the system has no principled way to decide which is correct. One source = one truth = one failure mode = one auth pattern.

**Enforced by:** `get_client_data()` in each sub-agent calls only ontology endpoints. No Athena queries, no CRM calls, no external lookups in agent code.

---

## P7 — Separate advisor signals from system signals

**Rule:** `flags[]` is for the advisor (human-readable warnings). `data_quality[]` is for the system (machine-readable provenance). These two lists serve different consumers and must never be merged.

**Why:** Mixing them means you cannot programmatically filter one without parsing strings. A downstream UI that wants to highlight uncertain results cannot do so reliably if uncertainty signals are embedded in narrative text.

**Enforced by:** Two separate fields in `OUTPUT_SCHEMA`. `data_quality` values are named constants, never free text. `flags` values are human-readable sentences.

---

## P8 — Correctness before features

**Rule:** All fixtures must pass before a new capability is added. Each sub-agent must pass its own Step 1 gate independently before connecting to the integration agent.

**Why:** Features built on top of unproven foundations compound bugs. A sub-agent wired into the swarm before it is proven correct poisons every integrated result. Catching a schema bug in a fixture costs minutes; catching it in production costs trust.

**Enforced by:** CI gate blocks merges when fixtures fail. No sub-agent connects to the integration agent until its Step 1 gate is passed. Integration work does not begin until RMD is independently proven.

---

## P9 — Sub-agents are strictly isolated

**Rule:** Sub-agents have zero knowledge of each other. They do not call each other, do not share state, and do not import each other's code. The integration agent is the only component that knows multiple sub-agents exist.

**Why:** Isolation makes each sub-agent independently testable, replaceable, and debuggable. A bug in one agent cannot affect another. Adding agent N requires zero changes to agents 1 through N-1.

**Enforced by:** Each sub-agent exposes only `evaluate(auth_token, account_id, client_input) -> dict`. The integration agent receives this contract and nothing else. No cross-imports between sub-agent packages.

---

## P10 — Decision enum is Python-controlled, never LLM

**Rule:** The `decision` field is set by deterministic Python logic after all computations are complete. The LLM never writes a `decision` value directly.

**Why:** `decision` is the primary signal consumed by the UI, the integration agent, and the conflict detector. If the LLM sets it, the value may not match the computed result or may not be in the valid enum.

**Enforced by:** `decision` is assigned in `compute_*()` or `post_check()` based on verified field values — never parsed from LLM output. LLM output is used only for `reason` (human-readable explanation).

---

## P11 — Ask one field at a time

**Rule:** When required data is missing, ask for exactly one field per message in priority order: `date_of_birth` → `prior_year_end_balance` → `withdrawal_amount_ytd`. Never ask for multiple fields at once.

**Why:** Asking for multiple fields at once increases drop-off — advisors answer the first question and submit. Single-field prompts produce higher completion rates and cleaner validation. Priority order reflects which missing field blocks computation earliest.

**Enforced by:** NL input layer (`rmd/parser.py`, Step 1 Task 8) controls the ask-back loop. `pre_check` returns exactly one field in `missing_fields` in priority order when multiple are absent.

---

## P12 — Identity resolution before compute

**Rule:** If the ontology returns multiple accounts for a given identifier, ask the advisor which account to use. Never proceed with an ambiguous match.

**Why:** An RMD computed against the wrong account is worse than no RMD at all. Account IDs can be ambiguous — a custodian account number may match more than one Farther virtual account. Silently picking the first result is a silent fallback (P2 violation).

**Enforced by:** `get_client_data()` checks the count of ontology results before proceeding. If `len(results) > 1`, returns `decision: INSUFFICIENT_DATA` with `reason: "Multiple accounts matched — advisor must specify which account."`.

---

## P13 — Observe before you ship

**Rule:** Every agent must be wired into ontology-evals with Phoenix tracing before it is declared Step 1 complete. `run_tests.py` pass/fail is not sufficient — full tool call traces must be visible.

**Why:** A fixture can pass for the wrong reason — the LLM called `compute_rmd` with incorrect arguments but the output happened to be correct due to rounding. Pass/fail without traces is blind. You cannot debug what you cannot see, and you cannot trust what you cannot debug. Observability is not a Step 2 feature — it is a Step 1 requirement.

**Enforced by:** ontology-evals `config.json` wired for each sub-agent before its Step 1 gate is declared passed. Phoenix traces showing tool call arguments, turn count, and latency are required, not optional.

---

## P14 — Prove stability before integration

**Rule:** The full fixture suite must pass 3 consecutive runs with zero flip-flop before a sub-agent connects to the integration agent. A latency baseline (p95) must be established on Anthropic direct and re-verified after Bedrock swap.

**Why:** `temperature=0` reduces variance but does not eliminate it. A fixture that flips pass/fail across runs is unreliable regardless of today's result. Without a latency baseline before Bedrock swap, there is no way to detect if the swap degraded performance. Stability and latency are not performance concerns — they are correctness concerns for a system that advisors will act on.

**Enforced by:** Step 1 completion gate requires 3-run stability check and p95 latency measurement before and after Bedrock swap. Both must pass before Step 2 begins.

---

## P15 — Dumb workers, smart coordinator

**Rule:** Sub-agents are deterministic Python workers — no LLM inside them. The LLM lives at the integration layer only, where it does real reasoning: conflict detection, prioritization, advisor-facing explanation. Every LLM round-trip inside a sub-agent is latency paid for work Python can do faster and more reliably.

**Why:** Sub-agents (RMD, Roth, TLH) compute deterministic outputs from structured inputs. Calling an LLM to decide which Python function to invoke next is paying ~2s per turn for zero judgment. The integration agent is where judgment is actually needed — reading three sub-agent outputs and producing a coherent recommendation for the advisor. That's one LLM call at the top, not three-per-sub-agent multiplied across every request.

**Enforced by:** Sub-agents expose `evaluate(auth_token, account_id, client_input) → dict` — pure Python, no Strands Agent inside. The integration agent (Step 2) is a Strands Agent that calls sub-agents as tools in parallel and invokes the LLM once for synthesis. Task 13 removes the LLM from the RMD sub-agent main path.

---

## P16 — Input and output cover what is needed, nothing more

**Rule:** Every layer — input to sub-agent, output from sub-agent, input to integration — contains exactly the fields needed by the next layer. No internal fields, debug tags, or convenience data bleed across boundaries.

**Why:** Extra fields create implicit coupling. If the integration agent starts depending on `_source` or `input_echo`, it breaks when those fields are removed or renamed. Each layer should only receive what it acts on — this keeps boundaries clean, makes contracts auditable, and prevents internal implementation details from becoming public API.

**Enforced by:** `post_check` strips internal fields (`_source`, `input_echo`, `market_value`, `advisor_name`) before returning. Output schema is defined explicitly — new fields require a deliberate decision, not accidental inclusion.

---

## P17 — Financial rounding uses ROUND_HALF_UP

**Rule:** All monetary rounding uses `ROUND_HALF_UP` (standard financial rounding). Never use Python's built-in `round()`, which applies banker's rounding (round half to even).

**Why:** Python's `round(16240.835, 2)` returns `16240.83`. The IRS and custodians compute `16240.84`. A $0.01 discrepancy per account, multiplied across thousands of accounts and years, causes real reconciliation failures. Financial systems must match the rounding convention of the authoritative source.

**Enforced by:** `_round2()` helper in `tools.py` uses `Decimal.quantize(ROUND_HALF_UP)`. All monetary calculations in `compute_rmd()` and `_compute_inherited_rmd()` route through `_round2()`. Raw `round()` is never used on dollar amounts.

---

## Summary

| Principle | What it prevents |
|---|---|
| P1 — LLM no math | Wrong RMD/tax calculation |
| P2 — No silent fallback | Acting on missing or proxy data without knowing |
| P3 — Python owns schema | Missing fields crashing downstream consumers |
| P4 — Data provenance | Cannot audit where a number came from |
| P5 — Conservative default | Confident wrong recommendation on incomplete data |
| P6 — Single data source | Two sources disagreeing, wrong value used silently |
| P7 — Separate signals | UI unable to distinguish data warnings from advice |
| P8 — Correctness first | New feature breaking existing correct behavior |
| P9 — Sub-agent isolation | Bug in one agent affecting another |
| P10 — Decision in Python | LLM decision contradicting verified math |
| P11 — Ask one field at a time | Advisor abandoning multi-field prompts, incomplete data |
| P12 — Identity resolution first | Computing against the wrong account silently |
| P13 — Observe before you ship | Passing fixture masking wrong tool call arguments |
| P14 — Prove stability before integration | Flaky agent poisoning integrated results |
| P15 — Dumb workers, smart coordinator | LLM inside sub-agents paying latency for work Python can do deterministically |
| P16 — Needed but no more | Internal fields bleeding into public contracts, creating hidden coupling |
| P17 — ROUND_HALF_UP for money | $0.01 rounding divergence from IRS/custodian calculations |
