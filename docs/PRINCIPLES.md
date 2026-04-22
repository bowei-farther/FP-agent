# Principles

These invariants apply to every agent. They are enforced in code, not by convention.

---

| # | Principle | What it prevents |
|---|---|---|
| P1 | LLM never performs financial math | Wrong RMD/tax calculation — LLMs approximate, Python computes exactly |
| P2 | No silent fallback — missing data is always surfaced | Automation bias — advisor acts on incorrect output without knowing |
| P3 | Python owns the output schema — all keys always present | Missing fields crashing downstream consumers |
| P4 | Data provenance on every output | Cannot audit where a number came from |
| P5 | Conservative default on ambiguity — ask, flag, or `MANUAL_REVIEW` | Confident wrong recommendation on incomplete data |
| P6 | Single data source — ontology only | Two sources disagreeing; wrong value used silently |
| P7 | Separate advisor signals (`flags[]`) from system signals (`data_quality[]`) | UI unable to distinguish data warnings from advice |
| P8 | Correctness before features — fixtures pass before new capability added | New feature breaking existing correct behavior |
| P9 | Sub-agents are strictly isolated — no cross-imports, no shared state | Bug in one agent affecting another |
| P10 | Decision enum is Python-controlled — never LLM | LLM decision contradicting verified math or outside valid enum |
| P11 | Ask one field at a time — priority: DOB → balance → YTD | Advisor abandoning multi-field prompts |
| P12 | Identity resolution before compute — ambiguous match is hard fail | Computing against the wrong account silently |
| P13 | Observe before you ship — Phoenix traces required at Step 1 | Passing fixture masking wrong tool call arguments |
| P14 | Prove stability before integration — 3-run pass + p95 latency baseline | Flaky agent poisoning integrated results |
| P15 | Dumb workers, smart coordinator — no LLM inside sub-agents | Paying ~5s LLM latency per call for deterministic work |
| P16 | Input and output cover what is needed, nothing more | Internal fields bleeding into public contracts |
| P17 | Financial rounding uses `ROUND_HALF_UP` — never Python `round()` | $0.01 divergence from IRS/custodian calculations at scale |

---

## Enforcement

Each principle is backed by a specific code mechanism — not a doc, not a convention.

| Principle | Enforcement |
|---|---|
| P1 | `compute_*()` are Python functions; LLM calls them as tools |
| P2 | `_missing` list in `get_client_data`; `pre_check` blocks before agent runs |
| P3 | `OUTPUT_SCHEMA` in `rules.py`; `post_check` merges every result |
| P4 | `data_quality[]` named constants; `completeness` field on every response |
| P5 | `pre_check` returns `INSUFFICIENT_DATA`; `MANUAL_REVIEW` for unresolvable cases |
| P6 | `get_client_data()` calls only ontology endpoints |
| P7 | Two separate fields in `OUTPUT_SCHEMA`; `data_quality` values are constants, never strings |
| P8 | CI gate blocks merges when any fixture fails |
| P9 | Each sub-agent exposes only `evaluate(...) -> dict`; no cross-package imports |
| P10 | `decision` is assigned in `compute_*()` or `post_check()` from verified field values |
| P11 | `pre_check` returns exactly one field in `missing_fields` per call |
| P12 | `get_client_data()` uses `page_size=2`; `len > 1` → `INVALID_INPUT` |
| P13 | Phoenix traces required before Step 1 gate is declared passed |
| P14 | Step 1 gate requires 3-run stability check and p95 measurement |
| P15 | Sub-agents are pure Python pipelines; no LLM imports in `core/` |
| P16 | `post_check` controls exactly what leaves the agent boundary |
| P17 | `_round2()` uses `Decimal.quantize(ROUND_HALF_UP)`; raw `round()` never used on money |
