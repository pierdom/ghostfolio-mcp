# Analytics Changelog

Changes to the server-side analytics tools, by `method_version`. Every response of these tools carries its `method_version`, so a figure can always be traced to the method that produced it. Output changes are additive: fields are added, never renamed or removed.

This file is kept apart from `CHANGELOG.md`, which commitizen generates from commit history on release.

## audit_portfolio_data

### audit/5 - 2026-10-11

- `interest_misdated` (warn): a daily payer's double day next to an empty day is now one finding with both dates, both payments and `evidence.suggestion` (which payment to move where), instead of an `interest_day_conflict` plus a `missing_interest_day`. A double day without an empty neighbour stays `interest_day_conflict`.
- `interest_rule_contradiction` (info): an account configured with `interest.expected: "none"` that has interest payments, or one configured daily/monthly/quarterly with none recorded. Accounts without an interest setting are not judged.
- `missing_distribution` (warn): a distribution the data provider reports on an ex-date while the account held the asset (the route behind `get_dividends_for_import`), with no DIVIDEND recorded from 3 days before to 30 days after the ex-date. Evidence: shares (also counted from activities), amount per share, expected gross. `dividend_data_unavailable` (info) lists assets whose history could not be read.
- `trade_date_mismatch`: a verdict from intraday prices now needs the price at least 0.5% nearer one close (`trade_date_min_gap`, was 0.2%) and the other day at least twice as far (`trade_date_min_ratio`); otherwise it is `ambiguous`. New `evidence.confidence`: `high` when only one of the two days traded or the price is that day's close, `low` when decided from intraday prices, null when ambiguous.
- Daily interest import grace raised from 1 to 2 days before `as_of` (`daily_interest_grace_days`).
- Accounts excluded from analysis are left out of the cash and interest checks; they stay in the data-integrity checks.
- New thresholds in `config.thresholds`: `trade_date_min_ratio`, `dividend_window_before_days`, `dividend_window_after_days`.

### audit/4 - 2026-10-11

- A daily interest payment counts as missing only from the day before `as_of`, since payments are often recorded the next day.

### audit/3 - 2026-10-11

- `missing_interest_quarter` for accounts with `interest.expected: "quarterly"`.
- `config.interest_rules` lists only accounts whose profile has an interest section.

### audit/2 - 2026-10-11

- `cash_without_interest` skips accounts whose profile sets `interest.expected: "none"` explicitly; `config.no_interest_accounts` lists them.

### audit/1 - 2026-10-10

- First release.

## get_benchmark_comparison

### benchmark/2 - 2026-10-11

- `config.benchmark_reset: "monthly"` and `summary.benchmark_mode: "continuous"` make explicit that each month restarts the benchmark holding from the portfolio's opening value while the summary runs one holding throughout.
- `summary.benchmark_pnl_sum_of_months`: the sum of the monthly `benchmark_pnl`, next to the continuous `summary.benchmark_pnl`.
- `cash.coverage_ratio` (days counted / chart days) and `flags.cash_coverage_partial`, per month and in the summary, when days before the first balance snapshot are left out of cash drag.

### benchmark/1 - 2026-10-10

- First release.

## get_realized_gains

### realized-gains/2 - 2026-10-11

- Open lots are valued at the last stored close up to yesterday: `market_price`, `price_date`, `market_value`, `unrealized_gain`, `unrealized_pct`; top-level `valuation_date`.
- `include_open_lot_detail` adds `open_lots[].lot_detail`: each open lot in FIFO order with buy activity, date and account, remaining quantity, fee-inclusive cost, `holding_days` and valuation.
- `HOMOGENEOUS_GROUPS` makes equivalent securities count as one asset for the two-month rule; `config.homogeneous_groups` echoes it and `flags.wash_sale.buys[].security` names each buy's asset. Lots are still matched per asset.

### realized-gains/1 - 2026-10-10

- First release.

## get_monthly_returns

### monthly-returns/3 - 2026-10-11

- Quarterly interest payers appear in `missing_interest_accounts` in a quarter's last month when the quarter had no payment.
- `config.interest_rules` lists only accounts whose profile has an interest section.

### monthly-returns/2 - 2026-10-10

- Interest paid after `end_month` but within the chart is kept, so a month's accrual paid on the 1st of the next month is no longer lost when that month is the last one requested.
- `interest_fees`: fees on interest activities (typically withholding tax), apart from trading `fees`; `market_pnl_gross` adds both back.

### monthly-returns/1 - 2026-09-28

- First release.

## get_cash_yield

### cash-yield/1 - 2026-10-10

- First release. Since 2026-10-11, `config.interest_rules` lists only accounts whose profile has an interest section.

## preview_import

### import-preview/1 - 2026-10-10

- First release.

## get_modelo720_inputs

### modelo720/1 - 2026-10-10

- First release.
