# Financial Planning Agent System

Decision support system for Farther wealth management advisors.
Independent sub-agents evaluate financial strategies; an integration agent routes, merges, and surfaces conflicts.

---

## Quick start

```bash
# 1. Clone and install
git clone <repo-url>
cd financial-planning
uv sync

# 2. Log into AWS (required for ontology access)
aws sso login --profile data-lake-dev

# 3. Get Farther auth token (saved to .env, valid for 24h)
make token

# 4. Run all tests
make test-all
```

`make test-all` runs:
- Core fixtures — 22 cases, no AWS needed
- NL parser fixtures — 5 cases, requires AWS
- Real-data fixtures — 21 live accounts, requires AWS

---

## Run the RMD agent

```bash
# Manual input — no AWS needed
make run-manual-rmd DOB=1950-03-15 TYPE="Traditional IRA" BALANCE=320000 YTD=10000

# Free-text input — requires AWS
make run-nl-rmd TEXT="John Smith, trad IRA, born March 1950, balance 320k, took out 10k"

# Live account — requires AWS + token
make run-rmd ACCOUNT_ID=38279295 BALANCE=178399
```

---

## Agents

| Agent | Status | What it evaluates |
|---|---|---|
| RMD Eligibility | Step 1 complete | Required Minimum Distributions |
| Roth Conversion | Step 2 — not yet built | Roth IRA conversion window |
| Tax Loss Harvesting | Step 2 — not yet built | Unrealized loss harvesting |
| Integration | Step 2 — not yet built | Routes, orchestrates, conflict detection |

---

## Docs

| Document | What it covers |
|---|---|
| [agents/rmd/README.md](agents/rmd/README.md) | RMD agent — inputs, outputs, pipeline, fixtures |
| [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md) | Architecture, data layer, design decisions |
| [docs/PRINCIPLES.md](docs/PRINCIPLES.md) | Rules enforced in code across all agents |
| [PLAN.md](PLAN.md) | Step-by-step build plan |
