# Integration Agent

> Status: Step 2 — not yet built
> Location: `agents/integration/`
> Prerequisite: All sub-agents must independently pass their Step 1 gate before this agent is built

The integration agent coordinates independent sub-agents, detects conflicts, and returns a unified recommendation to the advisor.

**Boundary contract (structural, not configurable):**
The integration agent must treat all sub-agents as black boxes. It calls `evaluate()` and receives a dict. It cannot inspect sub-agent source code, access intermediate tool call results, modify sub-agent logic, or read sub-agent internal state. The only communication channel is the `evaluate()` return dict. This boundary is what makes each sub-agent independently testable and replaceable.

---

## What it does

Four responsibilities, exactly:

1. **Route** — determine which sub-agents are relevant for this client and account type
2. **Orchestrate** — call relevant sub-agents in parallel (swarm)
3. **Conflict detection** — when two recommendations draw from the same resource
4. **Unified output** — merge results into one advisor-facing recommendation

It does not fetch data, compute math, or apply strategy logic.

---

## What it does not do

- Fetch data from the ontology (sub-agents do this)
- Perform financial calculations (sub-agents do this)
- Know about sub-agent internals (sub-agents are black boxes)
- Execute financial actions (advisor confirmation is always required)

---

## Sub-agent contract

Every sub-agent exposes exactly one function:

```python
evaluate(auth_token: str, account_id: str, client_input: dict) -> dict
```

The returned dict always contains:

| Field | Type | Description |
|---|---|---|
| `decision` | str enum | Machine-readable action |
| `eligible` | bool or null | Whether the strategy applies |
| `flags` | list[str] | Advisor-facing warnings |
| `data_quality` | list[str] | System-facing provenance flags |
| `completeness` | str | `full` / `partial` / `minimal` |
| `input_echo` | dict | Exact values used in calculation |
| `_source` | str | Where the data came from |

The integration agent receives this contract and nothing else. Sub-agent internals are invisible to it.

---

## Routing rules

| Account type | Run RMD | Run Roth | Run TLH |
|---|---|---|---|
| Traditional IRA | Yes | Yes | No (tax-deferred) |
| Roth IRA | No | No | No |
| SEP IRA | Yes | No | No |
| Taxable | No | No | Yes |
| Employer Retirement Plan | Yes | No | No |
| Inherited IRA | Yes (→ MANUAL_REVIEW) | No | No |

---

## Conflict detection

Two recommendations conflict when they draw from the same resource in the same period.

**Known conflict: RMD + Roth conversion on the same Traditional IRA**

IRS rule: the RMD amount cannot be included in a Roth conversion. RMD must be satisfied first.

```python
{
    "strategy": "rmd",
    "decision": "RMD_PENDING",
    "conflicts_with": ["roth_conversion"],
    "conflict_reason": "RMD must be satisfied before any Roth conversion from this account in 2026."
}
```

The integration agent surfaces the conflict to the advisor. It does not resolve it automatically.

---

## Orchestration pattern

```python
# All sub-agents run in parallel (swarm)
results = await asyncio.gather(
    rmd.evaluate(token, account_id, client_input),
    roth.evaluate(token, account_id, client_input),
    tlh.evaluate(token, account_id, client_input),
)

# Conflict detection after all return
conflicts = detect_conflicts(results)

# Unified output
return build_unified_recommendation(results, conflicts)
```

Sub-agents are independent. A slow or failing sub-agent does not block the others.

---

## Unified output shape

```json
{
  "client_name": "John Smith",
  "account_id": "38279295",
  "evaluated_at": "2026-04-21T14:32:00Z",
  "strategies": {
    "rmd": {
      "decision": "RMD_PENDING",
      "rmd_required_amount": 7511.74,
      "conflicts_with": ["roth_conversion"],
      "conflict_reason": "RMD must be satisfied before Roth conversion.",
      "completeness": "partial",
      "flags": ["RMD not started with fewer than 6 months remaining."]
    },
    "roth": {
      "decision": "EVALUATE",
      "conflicts_with": ["rmd"],
      "conflict_reason": "Roth conversion draw conflicts with pending RMD.",
      "completeness": "partial",
      "flags": []
    }
  },
  "advisor_action_required": true,
  "human_in_the_loop": "Confirm RMD before initiating Roth conversion."
}
```

---

## Human-in-the-loop gate

No action that affects a client account is executed without advisor confirmation.

```
Integration agent produces recommendation
    ↓
Advisor sees unified recommendation with conflicts surfaced
    ↓
Advisor confirms each strategy independently
    ↓
System records confirmation and routes to execution (OMS, not this system)
```

This gate is non-negotiable. It is structural, not configurable.

---

## Session state (Step 2)

To avoid asking the same question twice in the same advisor session:

| Field | TTL | Scope |
|---|---|---|
| `date_of_birth` | 4 hours | Per client |
| `account_type` | 4 hours | Per account |
| `client_name`, `advisor_name` | 4 hours | Per client |

Storage: DynamoDB. PK: `session_id`, SK: `account_id`.

---

## Step 2 prerequisites

This agent is not built until:

- [ ] RMD agent passes its Step 1 gate independently
- [ ] Roth agent passes its Step 1 gate independently
- [ ] TLH agent passes its Step 1 gate independently

See [../../docs/SYSTEM_DESIGN.md](../../docs/SYSTEM_DESIGN.md) — Development model section.

---

## Step 2 completion gate

- [ ] Conflict detection works for RMD + Roth same-account case
- [ ] Swarm runs all 3 in parallel, unified result in < 5 seconds
- [ ] Session state: DOB not asked twice in same session
- [ ] Human-in-the-loop: no action executes without advisor confirmation
- [ ] Streaming: advisor sees first token within 500ms (FastAPI + SSE)
- [ ] Bedrock on all sub-agents
