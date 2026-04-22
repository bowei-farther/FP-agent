.PHONY: test test-latency test-trace lint \
        test-parser test-parser-trace \
        test-real \
        run-rmd run-manual-rmd run-nl-rmd token

# ── Tests ────────────────────────────────────────────────────────────────────

test:
	uv run python agents/rmd/run_tests.py 2>/dev/null

test-latency:
	uv run python agents/rmd/run_tests.py --latency

test-trace:
	uv run python agents/rmd/run_tests.py --trace --latency

test-parser:
	AWS_PROFILE=data-lake-dev uv run python agents/rmd/run_parser_tests.py

test-parser-trace:
	AWS_PROFILE=data-lake-dev uv run python agents/rmd/run_parser_tests.py --trace

test-real:
	AWS_PROFILE=data-lake-dev uv run python agents/rmd/run_real_tests.py

# ── RMD agent ─────────────────────────────────────────────────────────────────
# make run-rmd ACCOUNT_ID=38279295
# make run-rmd ACCOUNT_ID=38279295 BALANCE=320000 YTD=10000

run-rmd:
	AWS_PROFILE=data-lake-dev uv run python agents/rmd/agent.py $(ACCOUNT_ID) \
		$(if $(BALANCE),--balance $(BALANCE)) \
		$(if $(YTD),--ytd $(YTD)) \
		$(if $(CASH),--cash $(CASH))

# make run-manual-rmd DOB=1950-03-15 TYPE="Traditional IRA" BALANCE=320000 YTD=10000

run-manual-rmd:
	uv run python agents/rmd/agent.py --manual \
		--dob $(DOB) \
		--account-type "$(TYPE)" \
		--balance $(BALANCE) \
		$(if $(YTD),--ytd $(YTD))

# make run-nl-rmd TEXT="John Smith, trad IRA, born March 1950, balance 320k"

run-nl-rmd:
	uv run python agents/rmd/agent.py --nl "$(TEXT)"

# ── Auth ──────────────────────────────────────────────────────────────────────
# Fetches a Bearer token from the dev Lambda and saves it to .env.
# Skips fetch if existing token has more than 1 day remaining.
# Usage: make token

TOKEN_LAMBDA=arn:aws:lambda:us-east-1:851725219327:function:dev-data-lake-tokenreq-tokenrequestrequesterE21E4F-udO6xaGKWVcy

token:
	@python3 scripts/check_token.py || ( \
		AWS_PROFILE=data-lake-dev aws lambda invoke \
			--function-name $(TOKEN_LAMBDA) \
			--payload '{"httpMethod":"GET"}' \
			--cli-binary-format raw-in-base64-out \
			/tmp/farther_token.json > /dev/null && \
		python3 scripts/save_token.py \
	)

# ── Lint ──────────────────────────────────────────────────────────────────────

lint:
	uv run ruff check agents/
