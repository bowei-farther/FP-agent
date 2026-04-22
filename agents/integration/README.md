# Integration Agent

> Status: Step 2 — not yet built
> Prerequisite: All sub-agents must independently pass their Step 1 gate

Coordinates independent sub-agents, detects conflicts, and returns a unified recommendation to the advisor.

**Boundary contract:** The integration agent treats all sub-agents as black boxes. It calls `evaluate()` and receives a dict. It cannot inspect sub-agent internals, access intermediate state, or modify sub-agent logic. The only communication channel is the `evaluate()` return dict.

---

## Responsibilities

1. **Route** — determine which sub-agents are relevant for this account type
2. **Orchestrate** — call relevant sub-agents in parallel
3. **Conflict detection** — surface when two recommendations draw from the same resource
4. **Unified output** — merge results into one advisor-facing recommendation

Does not fetch data, compute math, apply strategy logic, or execute financial actions.

---

## Related docs

- Architecture and design principles: [docs/SYSTEM_DESIGN.md](../../docs/SYSTEM_DESIGN.md)
- Build tasks and completion gate: [PLAN.md](../../PLAN.md) — Step 2
- Sub-agent contract: [agents/rmd/README.md](../rmd/README.md)
