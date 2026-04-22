# Financial Planning Agent — Principles

These rules apply to every agent. They are enforced in code, not by convention.

---

## P1 — LLM does orchestration only, never math

All financial calculations happen in Python. The LLM calls tools; it never computes numbers itself.

LLMs produce statistically likely output, not arithmetically correct output. A wrong RMD calculation causes real financial harm. Math must be deterministic and reproducible.

*Enforced by:* `compute_rmd()` and all financial logic are Python functions. The LLM calls them as tools.

---

## P2 — No silent fallback

When data is missing, the system says so. It never substitutes a default and pretends the data was there.

Silent fallbacks create automation bias — the advisor acts on incorrect output without knowing it.

*Enforced by:* `_missing` list from `get_client_data`, `pre_check` blocks before the agent runs, named flags (`USING_LATEST_BALANCE_AS_PROXY`) when proxy data is used.

---

## P3 — Python owns the output schema

The shape of every output is defined and enforced by Python. The LLM cannot add, remove, or rename fields. Every key is always present.

LLM output structure depends on prompt and sampling — not a type system. It can change after a prompt edit or model update.

*Enforced by:* `OUTPUT_SCHEMA` dict in `rules.py`. `post_check` merges every result into this schema — missing keys get defaults.

---

## P4 — Data provenance on every output

Every output must be traceable to its source.

Required for compliance auditing. When a result is wrong, you need to know whether the problem is in the data, the math, or the LLM.

*Enforced by:* `data_quality[]` named constants, `completeness` field (`full` / `partial` / `minimal`).

---

## P5 — Conservative default on ambiguity

When the system cannot determine a correct answer confidently, it returns the most conservative response — ask for more data, flag for manual review, or return `no_action`.

A false negative (flagging unnecessarily) has low cost. A confident wrong recommendation has high cost — IRS penalty, legal exposure, client harm.

*Enforced by:* `pre_check` returns `INSUFFICIENT_DATA` rather than proceeding. `MANUAL_REVIEW` for cases the standard logic cannot handle.

---

## P6 — Single data source

Farther Ontology only. No mixing with Athena, CRM, or external APIs.

Multiple sources create reconciliation ambiguity — when two sources disagree, there is no principled way to decide which is correct.

*Enforced by:* `get_client_data()` in each sub-agent calls only ontology endpoints.

---

## P7 — Separate advisor signals from system signals

`flags[]` is for the advisor (human-readable warnings). `data_quality[]` is for the system (machine-readable provenance). These two lists must never be merged.

If they are mixed, downstream systems cannot programmatically filter one without parsing strings.

*Enforced by:* Two separate fields in `OUTPUT_SCHEMA`. `data_quality` values are named constants, never free text.

---

## P8 — Correctness before features

All fixtures must pass before a new capability is added. Each sub-agent must pass its own Step 1 gate independently before connecting to the integration agent.

Features built on top of unproven code compound bugs. A sub-agent wired into the swarm before it is correct poisons every integrated result.

*Enforced by:* CI gate blocks merges when fixtures fail.

---

## P9 — Sub-agents are strictly isolated

Sub-agents have zero knowledge of each other. They do not call each other, share state, or import each other's code. Only the integration agent knows multiple sub-agents exist.

Isolation makes each sub-agent independently testable and replaceable. A bug in one agent cannot affect another. Adding agent N requires zero changes to agents 1 through N-1.

*Enforced by:* Each sub-agent exposes only `evaluate(auth_token, account_id, client_input) -> dict`. No cross-imports between sub-agent packages.

---

## P10 — Decision enum is Python-controlled, never LLM

The `decision` field is set by deterministic Python logic after all computations are complete. The LLM never writes a `decision` value directly.

`decision` is the primary signal consumed by the UI and the integration agent. If the LLM sets it, the value may not match the computed result or may not be in the valid enum.

*Enforced by:* `decision` is assigned in `compute_*()` or `post_check()` based on verified field values.

---

## P11 — Ask one field at a time

When required data is missing, ask for exactly one field per message in priority order: `date_of_birth` → `prior_year_end_balance` → `withdrawal_amount_ytd`.

Asking for multiple fields at once increases drop-off. Single-field prompts produce higher completion rates.

*Enforced by:* `pre_check` returns exactly one field in `missing_fields` in priority order when multiple are absent.

---

## P12 — Identity resolution before compute

If the ontology returns multiple accounts for a given identifier, ask the advisor which account to use. Never proceed with an ambiguous match.

An RMD computed against the wrong account is worse than no RMD at all. Silently picking the first result is a silent fallback (P2 violation).

*Enforced by:* `get_client_data()` checks the count of ontology results. If `len(results) > 1`, returns `INSUFFICIENT_DATA`.

---

## P13 — Observe before you ship

Every agent must be wired into ontology-evals with Phoenix tracing before it is declared Step 1 complete. Pass/fail alone is not enough — full tool call traces must be visible.

A fixture can pass for the wrong reason — the LLM called `compute_rmd` with wrong arguments but the output happened to be correct. You cannot debug what you cannot see.

*Enforced by:* ontology-evals `config.json` and Phoenix traces are required before the Step 1 gate is declared passed.

---

## P14 — Prove stability before integration

The full fixture suite must pass 3 consecutive runs with zero failures before a sub-agent connects to the integration agent. A latency baseline (p95) must be established on Anthropic direct and re-verified after Bedrock swap.

`temperature=0` reduces variance but does not eliminate it. A fixture that flips pass/fail is unreliable regardless of today's result.

*Enforced by:* Step 1 gate requires 3-run stability check and p95 latency measurement before Step 2 begins.

---

## P15 — Dumb workers, smart coordinator

Sub-agents are deterministic Python workers — no LLM inside them. The LLM lives at the integration layer only, where it reasons across all sub-agent outputs.

Sub-agents compute deterministic outputs from structured inputs. Calling an LLM to pick which Python function to run next pays ~2s per turn for work Python can do faster and more reliably.

*Enforced by:* Sub-agents expose `evaluate(...) → dict` — pure Python, no LLM inside. The integration agent calls them in parallel and invokes the LLM once for synthesis.

---

## P16 — Input and output cover what is needed, nothing more

Every layer contains exactly the fields needed by the next layer. No internal fields, debug tags, or convenience data bleed across boundaries.

Extra fields create hidden coupling. If the integration agent starts depending on `_source`, it breaks when that field is renamed or removed.

*Enforced by:* `post_check` strips internal fields (`_source`, `input_echo`, `market_value`) before returning. New output fields require a deliberate decision.

---

## P17 — Financial rounding uses ROUND_HALF_UP

All monetary rounding uses `ROUND_HALF_UP` (standard financial rounding). Never use Python's built-in `round()`, which applies banker's rounding (round half to even).

Python's `round(16240.835, 2)` returns `16240.83`. The IRS and custodians compute `16240.84`. A $0.01 discrepancy per account, across thousands of accounts, causes real reconciliation failures.

*Enforced by:* `_round2()` helper in `tools.py` uses `Decimal.quantize(ROUND_HALF_UP)`. Raw `round()` is never used on dollar amounts.

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
| P11 — Ask one field at a time | Advisor abandoning multi-field prompts |
| P12 — Identity resolution first | Computing against the wrong account silently |
| P13 — Observe before you ship | Passing fixture masking wrong tool call arguments |
| P14 — Prove stability before integration | Flaky agent poisoning integrated results |
| P15 — Dumb workers, smart coordinator | LLM in sub-agents paying latency for deterministic work |
| P16 — Needed but no more | Internal fields bleeding into public contracts |
| P17 — ROUND_HALF_UP for money | $0.01 rounding divergence from IRS/custodian calculations |
