"""Mock data for RMD agent dev/test.

Used when the ontology returns no record for an account_id.
Keyed by farther_virtual_account_id or test account ID.
"""

# Fields: date_of_birth, account_type, prior_year_end_balance,
#         withdrawal_amount_ytd, available_cash, market_value, rmd_amount

RMD_MOCK: dict[str, dict] = {
    # Age 76, Traditional IRA, not started
    "rmd-eligible-not-started": {
        "date_of_birth": "1949-06-15",
        "account_type": "Traditional IRA",
        "prior_year_end_balance": 480000,
        "withdrawal_amount_ytd": 0,
        "available_cash": 25000,
        "market_value": 492000,
        "rmd_amount": None,
    },
    # Age 80, Traditional IRA, in progress
    "rmd-eligible-in-progress": {
        "date_of_birth": "1945-03-20",
        "account_type": "Traditional IRA",
        "prior_year_end_balance": 320000,
        "withdrawal_amount_ytd": 8000,
        "available_cash": 5000,
        "market_value": 315000,
        "rmd_amount": None,
    },
    # Age 74, Traditional IRA, completed
    "rmd-eligible-completed": {
        "date_of_birth": "1951-11-01",
        "account_type": "Traditional IRA",
        "prior_year_end_balance": 200000,
        "withdrawal_amount_ytd": 9000,
        "available_cash": 12000,
        "market_value": 198000,
        "rmd_amount": 7843.14,   # pre-stored rmd_amount
    },
    # Roth IRA — not eligible
    "rmd-roth-not-eligible": {
        "date_of_birth": "1948-07-04",
        "account_type": "Roth IRA",
        "prior_year_end_balance": 150000,
        "withdrawal_amount_ytd": 0,
        "available_cash": 8000,
        "market_value": 155000,
        "rmd_amount": None,
    },
    # Age 65 — under 73, not eligible
    "rmd-too-young": {
        "date_of_birth": "1960-09-10",
        "account_type": "Traditional IRA",
        "prior_year_end_balance": 95000,
        "withdrawal_amount_ytd": 0,
        "available_cash": 3000,
        "market_value": 98000,
        "rmd_amount": None,
    },
    # Missing required data
    "rmd-missing-data": {
        "date_of_birth": None,
        "account_type": "Traditional IRA",
        "prior_year_end_balance": None,
        "withdrawal_amount_ytd": None,
        "available_cash": None,
        "market_value": None,
        "rmd_amount": None,
    },
}

RMD_DEFAULT: dict = {
    "date_of_birth": "1948-05-01",
    "account_type": "Traditional IRA",
    "prior_year_end_balance": 250000,
    "withdrawal_amount_ytd": 0,
    "available_cash": 10000,
    "market_value": 255000,
    "rmd_amount": None,
}
