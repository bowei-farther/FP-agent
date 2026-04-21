# Financial Planning Agent System

Decision support system for Farther wealth management advisors.
A set of independent sub-agents, each evaluating one financial strategy,
coordinated by an integration agent that routes, merges, and surfaces conflicts.

---

## What this system does

- Evaluates tax and retirement planning strategies for client accounts
- Surfaces structured recommendations with explicit uncertainty signals
- Flags deadline risk, missing data, and cases requiring manual review
- Never executes financial actions — all recommendations require advisor confirmation

## What it does not do

- Provide tax advice or legal opinions
- Execute trades, withdrawals, or account changes
- Guess or infer when data is missing
- Mix data sources

---

## Agents

| Agent | Location | Status | Strategy |
|---|---|---|---|
| RMD Eligibility | `agents/rmd/` | Step 1 in progress | Required Minimum Distributions |
| Integration | `agents/integration/` | Step 2 — not yet built | Routes, orchestrates, merges, conflict detection |
| Roth Conversion | `agents/roth/` | Step 2 — after RMD proven | Roth IRA conversion window |
| Tax Loss Harvesting | `agents/tlh/` | Step 2 — after RMD proven | Unrealized loss harvesting |
| Agents 4–16 | Planned | Step 3 | Various tax strategies |

---

## Architecture summary

Each sub-agent is an independent specialist with a single `evaluate()` function.
The integration agent calls them, detects conflicts, and returns a unified recommendation.
Sub-agents never call each other and never share state.

```
Advisor
  │
  ▼
Integration Agent          ← routes + orchestrates + merges (Step 2)
  ├── rmd.evaluate()       ← independent sub-agent
  ├── roth.evaluate()      ← independent sub-agent
  └── tlh.evaluate()       ← independent sub-agent
        │
        ▼
  Farther Ontology API     ← single data source for all agents
```

Full architecture: [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md)
Non-negotiable rules: [docs/PRINCIPLES.md](docs/PRINCIPLES.md)

---

## Quick start (RMD agent)

```bash
# 1. Go to the repo root and install dependencies
cd /Users/bowei/Documents/financial-planning
uv sync

# 2. Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Run all test fixtures (no AWS needed)
cd agents/rmd
make test

# 4. Run with manual input (no AWS needed)
make run-manual DOB=1950-03-15 TYPE="Traditional IRA" BALANCE=320000 YTD=10000

# 5. Run against a real account (requires AWS SSO)
aws sso login --profile data-lake-dev
make run ACCOUNT_ID=38279295 BALANCE=178399
```

Full details: [agents/rmd/README.md](agents/rmd/README.md)

---

## Development model

One agent at a time. Each sub-agent is built and proven correct in isolation.
No sub-agent connects to the integration agent until it passes its own Step 1 gate.

See [PLAN.md](PLAN.md) for the full build plan.

---

## Documentation guide

| Document | What it answers |
|---|---|
| [README.md](README.md) | What this system is, how to start |
| [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md) | Architecture, boundaries, data layer, rationale, tradeoffs |
| [docs/PRINCIPLES.md](docs/PRINCIPLES.md) | Non-negotiable rules — enforced in code, not convention |
| [PLAN.md](PLAN.md) | Execution plan — output schema, decision enum, step-by-step tasks |
| [agents/integration/README.md](agents/integration/README.md) | How the integration agent orchestrates sub-agents |
| [agents/rmd/README.md](agents/rmd/README.md) | RMD agent — inputs, outputs, pipeline, fixtures |

---

## Repository structure

```
financial-planning/
  pyproject.toml               ← single shared environment for all agents
  uv.lock                      ← locked dependencies
  README.md                    ← this file
  PLAN.md                      ← step-by-step build plan
  docs/
    SYSTEM_DESIGN.md           ← full architecture, rationale, tradeoffs
    PRINCIPLES.md              ← non-negotiable rules (apply to all agents)
  agents/
    integration/
      README.md                ← orchestration logic (Step 2)
    rmd/
      README.md                ← RMD agent
      core/                    ← agent package (tools, rules, agent, prompts)
      prompts/                 ← test fixtures
      agent.py                 ← CLI entry point
      run_tests.py
      Makefile
    roth/                      ← Step 2
    tlh/                       ← Step 2
```
