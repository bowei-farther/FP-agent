PROJECT_NAME := rmd-agent

.PHONY: run run-manual test lint

# Run with a real account ID from the database
# Requires: AWS SSO login (aws sso login --profile data-lake-dev)
# Usage: make run ACCOUNT_ID=<id>
# Usage: make run ACCOUNT_ID=<id> BALANCE=320000
# Usage: make run ACCOUNT_ID=<id> BALANCE=320000 YTD=10000
run:
	AWS_PROFILE=data-lake-dev uv run python agent.py $(ACCOUNT_ID) \
		$(if $(BALANCE),--balance $(BALANCE)) \
		$(if $(YTD),--ytd $(YTD)) \
		$(if $(CASH),--cash $(CASH))

# Run with manual input — no database lookup, no AWS needed
# Usage: make run-manual DOB=1950-03-15 TYPE="Traditional IRA" BALANCE=320000 YTD=10000
run-manual:
	uv run python agent.py --manual \
		--dob $(DOB) \
		--account-type "$(TYPE)" \
		--balance $(BALANCE) \
		--ytd $(YTD)

# Run all test fixtures — no AWS or database needed
test:
	uv run python run_tests.py 2>/dev/null

# Lint
lint:
	uv run ruff check .
