.PHONY: test lint test-rmd run-rmd run-nl-rmd

# Run all agent test suites
test: test-rmd

# RMD agent
test-rmd:
	$(MAKE) -C agents/rmd test

run-rmd:
	$(MAKE) -C agents/rmd run ACCOUNT_ID=$(ACCOUNT_ID) $(if $(BALANCE),BALANCE=$(BALANCE)) $(if $(YTD),YTD=$(YTD))

run-manual-rmd:
	$(MAKE) -C agents/rmd run-manual DOB=$(DOB) TYPE="$(TYPE)" BALANCE=$(BALANCE) YTD=$(YTD)

run-nl-rmd:
	$(MAKE) -C agents/rmd run-nl TEXT="$(TEXT)"

lint:
	$(MAKE) -C agents/rmd lint
