# Ghostfolio MCP Server

<!-- mcp-name: io.github.mhajder/ghostfolio-mcp -->

Ghostfolio MCP Server is a Python-based Model Context Protocol (MCP) server designed to provide advanced, programmable access to Ghostfolio portfolio management and financial data. It exposes a modern API for querying, analyzing, and managing your investment portfolio through Ghostfolio's comprehensive features. The server supports both read and write operations, robust security features, and is suitable for integration with automation tools, financial dashboards, and custom portfolio management applications.

## Features

### Core Features

- Query portfolio performance, holdings, and positions with flexible time ranges
- Retrieve comprehensive investment data including dividends, returns, and allocations
- Access detailed market data, asset profiles, and historical price information
- Monitor portfolio metrics, benchmarks, and performance comparisons
- Track orders, transactions, and account balances across multiple accounts
- Search and lookup financial symbols, stocks, ETFs, and other assets
- Get user information, settings, and account details

### Management Operations

- Create and manage investment accounts with different currencies and platforms
- Create, delete, and manage individual transactions and activities
- Import transactions and historical data from other platforms
- Configure read-only mode to restrict all write operations for safe monitoring
- Support for bulk transaction imports and portfolio data management
- Monitor system health and platform availability

### Advanced Capabilities

- Rate limiting and API security features
- Real-time portfolio monitoring and performance tracking
- Comprehensive logging and audit trails
- SSL/TLS support and configurable timeouts
- Extensible with custom middlewares and tag-based tool filtering
- Optional tool-search transform for large tool catalogs
- Bearer token or OIDC/OAuth authentication for HTTP transports

## Installation

### Prerequisites

- Python 3.11 or higher
- Access to a Ghostfolio instance
- Valid Ghostfolio API token

### Quick Install from PyPI

The easiest way to get started is to install from PyPI:

```sh
# Using UV (recommended)
uvx ghostfolio-mcp

# Or using pip
pip install ghostfolio-mcp
```

Remember to configure the environment variables for your Ghostfolio instance before running the server:

```sh
# Create environment configuration
export GHOSTFOLIO_URL=https://domain.tld:3333
export GHOSTFOLIO_TOKEN=your-ghostfolio-token
```

For more details, visit: https://pypi.org/project/ghostfolio-mcp/

### Install from Source

1. Clone the repository:

```sh
git clone https://github.com/mhajder/ghostfolio-mcp.git
cd ghostfolio-mcp
```

2. Install dependencies:

```sh
# Using UV (recommended)
uv sync

# Or using pip
pip install -e .
```

3. Configure environment variables:

```sh
cp .env.example .env
# Edit .env with your Ghostfolio URL and token
```

4. Run the server:

```sh
# Using UV (recommended)
uv run ghostfolio-mcp

# Or using the installed command directly
ghostfolio-mcp
```

### Development Setup

For development with additional tools:

```sh
# Clone and install with development dependencies
git clone https://github.com/mhajder/ghostfolio-mcp.git
cd ghostfolio-mcp
uv sync --group dev

# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=src/

# Run linting and formatting
uv run ruff check .
uv run ruff format .

# Run type checking
uv run ty check .

# Setup prek hooks
uv run prek install
```

## Configuration

### Environment Variables

```env
# Ghostfolio Connection Details
GHOSTFOLIO_URL=https://domain.tld:3333
GHOSTFOLIO_TOKEN=your-ghostfolio-token

# SSL Configuration
GHOSTFOLIO_VERIFY_SSL=true
GHOSTFOLIO_TIMEOUT=30

# Read-Only Mode
# Set READ_ONLY_MODE true to disable all write operations (put, post, delete)
READ_ONLY_MODE=false

# Disabled Tags
# Comma-separated list of tags to disable tools for (empty by default)
# Example: GHOSTFOLIO_DISABLED_TAGS=portfolio,symbol
GHOSTFOLIO_DISABLED_TAGS=

# Logging Configuration
LOG_LEVEL=INFO

# Rate Limiting (requests per minute)
# Set RATE_LIMIT_ENABLED true to enable rate limiting
RATE_LIMIT_ENABLED=false
RATE_LIMIT_MAX_REQUESTS=100
RATE_LIMIT_WINDOW_MINUTES=1

# Tool Search Transform (Optional)
# Set TOOL_SEARCH_ENABLED true to replace full tool listings with search_tools + call_tool
TOOL_SEARCH_ENABLED=false
# Search strategy: bm25 (natural language) or regex (pattern match)
TOOL_SEARCH_STRATEGY=bm25
# Maximum number of tools returned by search_tools
TOOL_SEARCH_MAX_RESULTS=5

# Account Profiles (Optional, used by the analytics tools)
# JSON object keyed by Ghostfolio account ID; every section is optional.
#   interest.attribution: "payment_date" (default) or "previous_month" - the
#     month a payment is booked in under interest_attribution=accrual
#   interest.expected: "none" (default), "daily", "monthly" or "quarterly" -
#     the known payment cadence; days, months or quarters without interest
#     are flagged. Set
#     "none" explicitly for an account that pays no interest, so the audit
#     does not flag its cash (left unset, the account is still checked)
#   tax.excluded: true leaves the account out of realized gains (e.g. a pension plan)
#   modelo720.foreign: true counts the account for Modelo 720
# ACCOUNT_PROFILES={"<account-id>": {"interest": {"attribution": "previous_month", "expected": "monthly"}, "modelo720": {"foreign": true}}}
# INTEREST_RULES (older) is still read and fills the interest section of
# accounts whose ACCOUNT_PROFILES entry has none.
# INTEREST_RULES={"<account-id>": {"attribution": "previous_month", "expected": "monthly"}}

# Benchmark used when a tool call does not name one (DATASOURCE:SYMBOL).
# Falls back to the benchmark selected in the Ghostfolio user settings.
# DEFAULT_BENCHMARK=YAHOO:VWCE.DE

# Sentry Error Tracking (Optional)
# Set SENTRY_DSN to enable error tracking and performance monitoring
# SENTRY_DSN=https://your-key@o12345.ingest.us.sentry.io/6789
# Optional Sentry configuration
# SENTRY_TRACES_SAMPLE_RATE=1.0
# SENTRY_SEND_DEFAULT_PII=true
# SENTRY_ENVIRONMENT=production
# SENTRY_RELEASE=1.2.3
# SENTRY_PROFILE_SESSION_SAMPLE_RATE=1.0
# SENTRY_PROFILE_LIFECYCLE=trace
# SENTRY_ENABLE_LOGS=true

# MCP Transport Configuration
# Transport type: 'stdio' (default), 'sse' (Server-Sent Events), or 'http' (HTTP Streamable)
MCP_TRANSPORT=stdio

# HTTP Transport Settings (used when MCP_TRANSPORT=sse or MCP_TRANSPORT=http)
# Host to bind the HTTP server (default: 127.0.0.1)
# MCP_HTTP_HOST=127.0.0.1
# Port to bind the HTTP server (default: 8000)
# MCP_HTTP_PORT=8000
# Optional bearer token for authentication (leave empty for no auth)
# MCP_HTTP_BEARER_TOKEN=

# OIDC / OAuth Authentication (optional, for remote hosting)
# Set all four to enable; takes precedence over MCP_HTTP_BEARER_TOKEN
# OIDC_CONFIG_URL=https://id.example.com/.well-known/openid-configuration
# OIDC_CLIENT_ID=
# OIDC_CLIENT_SECRET=
# OIDC_BASE_URL=https://ghostfolio-mcp.example.com
# Optional OIDC settings
# OIDC_REDIRECT_PATH=/auth/callback
# OIDC_REQUIRED_SCOPES=
# OIDC_ALLOWED_REDIRECT_URIS=
# OIDC_VERIFY_ID_TOKEN=false
# OIDC_FORWARD_RESOURCE=false

# Host/Origin Guard (optional, HTTP/SSE only)
# MCP_HOST_ORIGIN_PROTECTION=true
# MCP_ALLOWED_HOSTS=
# MCP_ALLOWED_ORIGINS=
```

### Sentry Error Tracking & Monitoring (Optional)

The server optionally supports **Sentry** for error tracking, performance monitoring, and debugging. Sentry integration is completely optional and only initialized if configured.

#### Installation

To enable Sentry monitoring, install the optional dependency:

```sh
# Using UV (recommended)
uv sync --extra sentry
```

#### Configuration

Enable Sentry by setting the `SENTRY_DSN` environment variable in your `.env` file:

```env
# Required: Sentry DSN for your project
SENTRY_DSN=https://your-key@o12345.ingest.us.sentry.io/6789

# Optional: Performance monitoring sample rate (0.0-1.0, default: 1.0)
SENTRY_TRACES_SAMPLE_RATE=1.0

# Optional: Include personally identifiable information (default: true)
SENTRY_SEND_DEFAULT_PII=true

# Optional: Environment name (e.g., "production", "staging")
SENTRY_ENVIRONMENT=production

# Optional: Release version (auto-detected from package if not set)
SENTRY_RELEASE=1.2.2

# Optional: Profiling - continuous profiling sample rate (0.0-1.0, default: 1.0)
SENTRY_PROFILE_SESSION_SAMPLE_RATE=1.0

# Optional: Profiling - lifecycle mode for profiling (default: "trace")
# Options: "all", "continuation", "trace"
SENTRY_PROFILE_LIFECYCLE=trace

# Optional: Enable log capture as breadcrumbs and events (default: true)
SENTRY_ENABLE_LOGS=true
```

#### Features

When enabled, Sentry automatically captures:

- **Exceptions & Errors**: All unhandled exceptions with full context
- **Performance Metrics**: Request/response times and traces
- **MCP Integration**: Detailed MCP server activity and interactions
- **Logs & Breadcrumbs**: Application logs and event trails for debugging
- **Context Data**: Environment, client info, and request parameters

#### Getting a Sentry DSN

1. Create a free account at [sentry.io](https://sentry.io)
2. Create a new Python project
3. Copy your DSN from the project settings
4. Set it in your `.env` file

#### Disabling Sentry

Sentry is completely optional. If you don't set `SENTRY_DSN`, the server will run normally without any Sentry integration, and no monitoring data will be collected.

## Available Tools

### Account Management Tools

- `get_accounts`: Get all accounts in your portfolio including account types and balances
- `get_account_balances`: Get account balances for a specific account
- `create_account`: Create a new account in your portfolio
- `delete_account`: Delete an existing account from your portfolio (destructive operation)
- `get_account_details`: Get details for a specific account
- `update_account`: Update settings or details of an existing account
- `transfer_account_balance`: Transfer cash balances between two accounts
- `create_account_balance`: Set an account's balance for a specific date in its balance history (defaults to today)
- `delete_account_balance`: Delete a single entry from an account's balance history

### Portfolio & Transaction Management Tools

- `get_portfolio_performance`: Get portfolio performance data including returns, benchmarks, and performance metrics
- `audit_portfolio_data`: Read-only data-quality audit - trades booked a day off, duplicates, misdated or missing interest, stale balances and prices, chart gaps and more - see [Portfolio Data Audit](#portfolio-data-audit)
- `get_cash_yield`: Effective interest yield on cash per account and month, from balance history - see [Cash Yield](#cash-yield)
- `get_realized_gains`: FIFO realized gains per sale and tax year, with lots consumed and a two-month-rule flag - see [Realized Gains](#realized-gains)
- `get_modelo720_inputs`: Year-end and Q4-average cash and year-end securities for accounts held abroad, against the Modelo 720 thresholds - see [Modelo 720 Inputs](#modelo-720-inputs)
- `get_benchmark_comparison`: Portfolio vs a configurable benchmark per month - TWR vs benchmark return, P&L vs a same-flows benchmark holding - plus cash drag - see [Benchmark Comparison and Cash Drag](#benchmark-comparison-and-cash-drag)
- `get_monthly_returns`: Deterministic per-month returns (net/gross P&L, fees, interest per account, flows, TWR, Modified Dietz) with data-quality flags - see [Monthly Returns](#monthly-returns)
- `get_portfolio_holdings`: Get portfolio holdings and positions including allocations and asset breakdowns
- `get_portfolio_details`: Get comprehensive portfolio details including accounts, positions, and summary
- `get_position`: Get position details for a specific symbol from a data source
- `get_investments`: Get investment data grouped by time period showing cash flows and contributions
- `get_dividends`: Get dividend data grouped by time period showing dividend payments and yield
- `get_orders`: Get all activities/orders from your portfolio, optionally filtered by account
- `create_activity`: Create a single new transaction/activity in your portfolio (BUY, SELL, DIVIDEND, INTEREST, FEE, etc.)
- `delete_activity`: Delete a single activity/transaction by its ID (destructive operation)

### Benchmark Tools

- `get_benchmarks`: Get all configured benchmarks
- `get_benchmark_performance`: Compare portfolio performance against a benchmark symbol starting from a specific date

### Watchlist Tools

- `get_watchlist`: Get all items in the user's watchlist
- `add_to_watchlist`: Add a symbol to the user's watchlist
- `remove_from_watchlist`: Remove a symbol from the user's watchlist

### Exchange Rate Tools

- `get_exchange_rate`: Get the exchange rate for a given currency symbol on a specific date

### Data Export Tools

- `export_portfolio`: Export portfolio activities/transactions data as JSON

### Market Data & Symbol Tools

- `get_market_data_for_asset`: Get market data for a specific asset
- `add_market_data_points`: Add one or more market data points for an asset (typically a `MANUAL` data source — Ghostfolio rejects writes for auto-fetched sources)
- `get_symbol_data`: Get symbol data for a specific asset from a data source
- `get_historical_data`: Get historical data for a specific symbol on a specific date
- `lookup_symbols`: Search for symbols using a query string
- `get_asset_profile`: Get asset profile information for a specific symbol
- `upsert_asset_profile`: Create-or-update an asset profile, including optional `countries` and `sectors` allocations (idempotent; tolerates Ghostfolio's HTTP 500 on the create step and relies on the subsequent PATCH as the source of truth)
- `delete_asset_profile`: Delete an asset profile (destructive operation; may delete associated activities and market data depending on backend rules)

### Data Import Tools

- `preview_import`: Read-only dry run of an import - classifies each activity as new, duplicate, conflict or invalid before anything is written - see [Import Preview](#import-preview)
- `import_transactions`: Import transactions into your portfolio (bulk import operation)
- `get_dividends_for_import`: Fetch historical dividend data formatted for import for a specific symbol

### System & Platform Tools

- `get_health`: Get system health status of the Ghostfolio backend service
- `get_platforms`: Get list of available platforms (brokers, exchanges, etc.) for account tracking

### User Management Tools

- `get_user_info`: Get user information and settings

## Security & Safety Features

### Read-Only Mode

The server supports a read-only mode that disables all write operations for safe monitoring:

```env
READ_ONLY_MODE=true
```

When enabled, this mode prevents any modifications to your portfolio data while still allowing full read access to all information.

### Tag-Based Tool Filtering

You can disable specific categories of tools by setting disabled tags:

```env
GHOSTFOLIO_DISABLED_TAGS=portfolio,symbol,import
```

Available tags include:
- `account` - Account management tools (create, delete, update, get accounts)
- `portfolio` - Portfolio analysis and performance tools
- `symbol` - Symbol lookup and data tools
- `import` - Data import tools
- `asset` - Asset profile tools
- `user` - User information tools
- `system` - System health and platform information tools
- `activities` - Activity/transaction management tools (create, delete activities)
- `watchlist` - Watchlist management tools
- `exchange-rate` - Currency exchange rate tools
- `export` - Data export tools
- `benchmark` - Benchmark tools

### Rate Limiting

The server supports rate limiting to control API usage and prevent abuse. If enabled, requests are limited per client using a sliding window algorithm.

Enable rate limiting by setting the following environment variables in your `.env` file:

```env
RATE_LIMIT_ENABLED=true
RATE_LIMIT_MAX_REQUESTS=100   # Maximum requests allowed per window
RATE_LIMIT_WINDOW_MINUTES=1   # Window size in minutes
```

If `RATE_LIMIT_ENABLED` is set to `true`, the server will apply rate limiting middleware. Adjust `RATE_LIMIT_MAX_REQUESTS` and `RATE_LIMIT_WINDOW_MINUTES` as needed for your environment.

### Tool Search for Large Toolsets

FastMCP tool search can reduce prompt size for servers with many tools.
When enabled, `list_tools` returns two synthetic tools:

- `search_tools`: Finds matching tools and returns their full schemas
- `call_tool`: Executes any discovered tool by name

Enable it with:

```env
TOOL_SEARCH_ENABLED=true
TOOL_SEARCH_STRATEGY=bm25      # bm25 or regex
TOOL_SEARCH_MAX_RESULTS=8      # optional, default is 5
```

`bm25` supports natural language queries, while `regex` uses a regex
`pattern` input for deterministic matching.

Tool search respects existing visibility controls (read-only mode and
disabled tags).

### SSL/TLS Configuration

The server supports SSL certificate verification and custom timeout settings:

```env
GHOSTFOLIO_VERIFY_SSL=true    # Enable SSL certificate verification
GHOSTFOLIO_TIMEOUT=30         # Connection timeout in seconds
```

### Transport Configuration

The server supports multiple transport protocols for different deployment scenarios:

#### STDIO Transport (Default)

The default transport uses standard input/output for communication. This is ideal for local usage and integration with tools that communicate via stdin/stdout:

```env
MCP_TRANSPORT=stdio
```

#### HTTP SSE Transport (Server-Sent Events)

For network-based deployments, you can use HTTP with Server-Sent Events. This allows the MCP server to be accessed over HTTP with real-time streaming:

```env
MCP_TRANSPORT=sse
MCP_HTTP_HOST=127.0.0.1        # Localhost
MCP_HTTP_PORT=8000           # Port to listen on
MCP_HTTP_BEARER_TOKEN=your-secret-token  # Optional authentication token
```

When using SSE transport with a bearer token, clients must include the token in their requests:

```bash
curl -H "Authorization: Bearer your-secret-token" http://localhost:8000/sse
```

#### HTTP Streamable Transport

The HTTP Streamable transport provides HTTP-based communication with request/response streaming. This is ideal for web integrations and tools that need HTTP endpoints:

```env
MCP_TRANSPORT=http
MCP_HTTP_HOST=127.0.0.1        # Localhost
MCP_HTTP_PORT=8000           # Port to listen on
MCP_HTTP_BEARER_TOKEN=your-secret-token  # Optional authentication token
```

When using streamable transport with a bearer token:

```sh
curl -H "Authorization: Bearer your-secret-token" \
     -H "Accept: application/json, text/event-stream" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
     http://localhost:8000/mcp
```

**Note**: The HTTP transport requires proper JSON-RPC formatting with `jsonrpc` and `id` fields. The server may also require session initialization for some operations.

### OIDC / OAuth Authentication (Optional)

A static bearer token is enough for machine-to-machine clients, but many MCP clients can only authenticate over OAuth with Dynamic Client Registration. For those, the server can act as an OAuth interface in front of an existing OIDC identity provider (Authentik, Keycloak, PocketID, Auth0, Entra ID, ...) using FastMCP's `OIDCProxy`. Clients register and authenticate against this server; the server brokers the flow upstream. No Ghostfolio credential ever reaches the client.

This applies to the `sse` and `http` transports only.

#### Registering the client

Create a **confidential** client (client ID + secret) on your identity provider with the redirect URI set to `OIDC_BASE_URL` + `OIDC_REDIRECT_PATH`, for example `https://ghostfolio-mcp.example.com/auth/callback`.

#### Configuration

```env
MCP_TRANSPORT=http
MCP_HTTP_HOST=0.0.0.0
MCP_HTTP_PORT=8000

# All four are required to enable OIDC
OIDC_CONFIG_URL=https://id.example.com/.well-known/openid-configuration
OIDC_CLIENT_ID=your-client-id
OIDC_CLIENT_SECRET=your-client-secret
# Public URL where this server is reachable, used to build its OAuth endpoints.
# Must be HTTPS (except on localhost), as required for an OAuth issuer.
OIDC_BASE_URL=https://ghostfolio-mcp.example.com
```

OIDC is entirely optional. Leaving these unset keeps the existing behaviour, and a partially configured setup is ignored with a warning rather than half-enabled. When OIDC is configured it takes precedence over `MCP_HTTP_BEARER_TOKEN`.

Optional settings:

```env
# Callback path registered on the identity provider (default: /auth/callback)
OIDC_REDIRECT_PATH=/auth/callback

# Comma-separated scopes required on presented tokens
OIDC_REQUIRED_SCOPES=openid,profile

# Comma-separated allowed client redirect URI patterns (wildcards accepted)
OIDC_ALLOWED_REDIRECT_URIS=https://example.com/*

# Verify the id_token instead of the access token (default: false)
OIDC_VERIFY_ID_TOKEN=false

# Forward the RFC 8707 'resource' indicator upstream (default: false)
OIDC_FORWARD_RESOURCE=false
```

`OIDC_ALLOWED_REDIRECT_URIS` restricts which clients may complete the flow. Leaving it unset accepts any redirect URI a client registers, so set it to the hosts you expect, for example `https://example.com/*`.

Set `OIDC_VERIFY_ID_TOKEN=true` if your identity provider issues opaque (non-JWT) access tokens; the `id_token` is then verified instead.

`OIDC_FORWARD_RESOURCE` is off by default because identity providers that do not implement RFC 8707 resource indicators reject the authorization request with `invalid_request`, which breaks login immediately after consent. Turn it on only if your provider supports resource indicators. Token audience binding is unaffected either way.

#### Persisting OAuth state

Client registrations and encrypted tokens are stored on disk, under FastMCP's data directory. If that directory is not persistent, every restart forces all clients to register and authenticate again. The Docker image sets `FASTMCP_HOME=/data`, so mount a volume there:

```sh
docker run -v ghostfolio-mcp-data:/data --env-file .env ghcr.io/mhajder/ghostfolio-mcp:latest
```

#### Running behind a reverse proxy

`OIDC_BASE_URL` must be the externally reachable HTTPS URL, and the proxy must forward the `Host` header unchanged, otherwise the OAuth metadata this server advertises will point at the wrong host.

### Host/Origin Guard (Optional)

FastMCP can validate the `Host` and `Origin` headers of incoming HTTP requests as a defence against DNS rebinding. `MCP_HOST_ORIGIN_PROTECTION` is unset by default, which leaves FastMCP's own default in place.

```env
MCP_HOST_ORIGIN_PROTECTION=true

# Extra values, comma-separated. The host and origin implied by OIDC_BASE_URL are allowed automatically.
MCP_ALLOWED_HOSTS=extra.example.com
MCP_ALLOWED_ORIGINS=https://app.example.com
```

Only loopback hosts are accepted out of the box, so behind a reverse proxy the public hostname must be allowed, otherwise every request is answered with `421 Misdirected Request`. Setting `OIDC_BASE_URL` covers that automatically; add anything else, such as a browser client's origin, to the two lists above.

## Monthly Returns

`get_monthly_returns` computes monthly figures server-side from the daily performance chart and the activity list, so the same Ghostfolio data always gives byte-identical output. The calculation lives in `ghostfolio_mcp.analytics.monthly_returns` (pure, no I/O), and every response carries `method_version` plus the window, timezone and rules it used.

Inputs: `start_month`, `end_month` (`YYYY-MM`), `timezone` (default `Europe/Madrid`) and `interest_attribution` (`payment_date` or `accrual`).

Data and window:

- The chart is fetched with the shortest daily range (`mtd`, `ytd`, `1y`) that has a row before `start_month`. Longer ranges skip days, so months older than about a year are refused rather than approximated.
- All deltas come from that single chart response. Today's row is dropped (`window.dropped_chart_dates`) because its prices are still moving.
- Returns cover the securities sleeve only (chart `value`, cash excluded) and are net of fees. Interest is reported next to them, not inside them.

Two calendars:

- Ghostfolio buckets activities into chart days on its server calendar (UTC). Chart-derived figures - P&L, flows, TWR, Modified Dietz - and the fees that make up `market_pnl_gross` follow that calendar.
- Interest, sell days and the per-account breakdown use each activity's date converted to `timezone`. An activity whose two months differ is listed in `calendar_mismatches`.

Definitions, per chart day `d` with netPerformance `NP`, value `V` and total investment `I`:

| Field | Definition |
| --- | --- |
| `market_pnl_net` | Σ ΔNP |
| `fees` | Σ fees of trades on the month's chart days |
| `interest_fees` | Σ fees recorded on INTEREST activities (typically withholding tax) on the month's chart days; Ghostfolio subtracts them from netPerformance |
| `market_pnl_gross` | `market_pnl_net + fees + interest_fees` |
| `interest` | Σ INTEREST activities (gross) attributed to the month, with `interest_by_account` |
| `total_gross` | `market_pnl_gross + interest` |
| `net_flows` | Σ ΔI (buys minus sells, at cost) |
| flow `F` | `ΔV - ΔNP`: money entering (+) or leaving (-) the securities, at market value, at the start of the day |
| `twr` | Π (1 + ΔNP / (V[d-1] + F)) - 1 |
| `avg_invested_capital` | `V0 + Σ w·F`, `w = (D - i + 1) / D`; `V0` is the value before the month, `i` the calendar day, `D` the days covered |
| `modified_dietz` | `market_pnl_net / avg_invested_capital` |

With no flows in a month, `twr` equals `modified_dietz`. If money is earned on zero capital (a full sale), `twr` is `null` and the day is listed in `undefined_return_days`.

Flags per month: `partial_month`, `sell_days`, `missing_interest_accounts` (daily and monthly payers without interest that month; quarterly payers in a quarter's last month when the quarter had none), `invariant_breaks`, `chart_gaps`, `boundary_gap` (a missing day across a month boundary, so a delta mixes two months), `calendar_mismatches` and `undefined_return_days`.

The invariant checked every day is `ΔNP - (ΔV - ΔI) + fees + interest_fees = 0` within 0.01. It is expected to break on sell days, by the realized gain on Ghostfolio's own (average) cost basis; on those days the flow `F` equals minus the sale proceeds. A break equal to that day's interest (`hint: matches_interest`) means Ghostfolio started counting interest inside netPerformance. Breaks are reported, never corrected.

`accrual` applies the per-account interest rules in `ACCOUNT_PROFILES`. Accounts without a rule keep their payment date. Values are rounded only in the output: money to 2 decimals, ratios to 8.

## Portfolio Data Audit

`audit_portfolio_data` checks the data the analytics tools rely on and returns findings, most severe first. Each finding has a `check`, a `severity` (`error`, `warn`, `info`), the account, asset, dates and activity ids involved, and the `evidence`. Nothing is corrected. It is read-only and deterministic, measured against yesterday (`as_of`).

| Check | Severity | Meaning |
| --- | --- | --- |
| `trade_date_mismatch` | warn / info | A timestamp between local and UTC midnight has two candidate dates; the stored close decides which one the price belongs to. A day counts as a trading day only if its close is neither carried forward nor a repeat of the day before (Ghostfolio fills weekends with Friday's close). Trades of one account sharing a timestamp are judged together, as one batch. `booked_day_before_trade` (warn): Ghostfolio values the trade a day early. `local_date_after_trade` (info): the local date, used for monthly bucketing, is a day late. `ambiguous` (info): the price cannot tell. |
| `price_far_from_close` | warn | The price is more than 5% from the close on the day Ghostfolio books the trade |
| `duplicate_activity` | error | Same account, type, asset, local date, quantity, price and fee |
| `interest_day_conflict` | warn | A daily payer with two payments on one day, usually next to a day with none |
| `missing_interest_day` / `missing_interest_month` / `missing_interest_quarter` | warn | A gap in a daily, monthly or quarterly payer's interest (`interest.expected` in `ACCOUNT_PROFILES`). Daily cadence is checked per kind of interest, and only for a kind paid on at least half the days of its span |
| `cash_without_interest` | info | Cash above 100 with no interest in 190 days; skipped for accounts whose profile sets `interest.expected` to `"none"` explicitly |
| `stale_balance` | info | Cash above 100 and no balance snapshot in 45 days |
| `stale_price` | warn | A held asset whose last real (not carried-forward) close is over 5 days old |
| `chart_gap` / `invariant_break` | info / warn | Missing chart days, and invariant breaks outside sell days (see [Monthly Returns](#monthly-returns)) |
| `legacy_seed_position` | info | A first buy more than 180 days before the next and under 1% of all buys |
| `mixed_timestamp_styles` | info | One account records the same kind of activity at UTC midnight, local midnight and intraday times |
| `missing_isin`, `unsupported_activity_type`, `non_base_currency` | info | Data the analytics tools cannot fully interpret |

## Cash Yield

`get_cash_yield` measures what cash actually earns, per account and month, plus a period summary. Inputs match `get_monthly_returns`: `start_month`, `end_month`, `timezone`, `interest_attribution`.

- The balance on a day is the latest balance snapshot on or before it: a balance holds until the next snapshot. `avg_balance` is the mean over the covered days.
- `period_yield = interest / avg_balance` and `annualized_yield = period_yield × 365 / days` (simple, ACT/365), up to yesterday. `interest` is gross; `interest_fees` are the fees recorded on the interest activities (typically withholding tax), and `net_period_yield` / `net_annualized_yield` use `interest - interest_fees`.
- A period not covered by snapshots on every day gets no yield (`partial_coverage`), since a whole month's interest would be set against part of its balance. The portfolio `total` sums only fully covered accounts and names the others in `accounts_left_out`.
- Flags per account: `history_starts`, `partial_coverage`, `stale_snapshot` (none in 45 days), `max_snapshot_gap_days`, `interest_without_balance`.
- Every account with balance history is reported, including accounts excluded from analysis (`excluded_from_analysis`).
- An account paying quarterly or semiannually puts several months of interest into one month, so its single-month yield is inflated; read its yield over the whole period (`summary`).

## Import Preview

`preview_import` takes the payload you would pass to `import_transactions` and imports nothing. Each activity is compared with the existing activities and with the earlier items of the same batch, by its date in `timezone`:

- `duplicate`: same account, type, asset (`dataSource:symbol`), local date, quantity, price and fee (compared at 8 decimals).
- `conflict`: same account, type, asset and date with a different quantity, price or fee - or a second payment on one day from an account whose `interest.expected` is `daily`.
- `invalid`: a required field (`currency`, `dataSource`, `date`, `quantity`, `symbol`, `type`, `unitPrice`) is missing or malformed.
- `new`: none of the above.

Warnings never change the status: a local-midnight timestamp (Ghostfolio books it on the previous UTC day), a date-only value (read as UTC midnight), a future date, an unknown or missing `accountId`. The tool only reads, so it also works in `READ_ONLY_MODE`.

## Realized Gains

`get_realized_gains` matches lots first-in, first-out and reports every sale (or those of one `year`), a summary per tax year and the open lots. It is read-only and deterministic, and it is not tax advice.

- Lots are pooled per asset (`dataSource:symbol`) across all accounts. Accounts with `tax.excluded` in `ACCOUNT_PROFILES` (e.g. a pension plan) are left out.
- Activities are processed in timestamp order (a BUY first on a tie). Buy fees add to cost and sell fees reduce proceeds; a partly used lot gives up cost in proportion to quantity.
- Dates and tax years are the activity's date in `timezone`.
- Two-month rule: a sale at a loss is flagged when the same asset was bought within two calendar months before or after and those shares were still held after the sale. The flag gives the affected quantity, its share and the affected loss; the loss is reported unchanged, and `by_year` totals the flagged part in `losses_flagged_two_month_rule`.
- A sale larger than the shares held reports the uncovered quantity and no gain.
- Only base-currency trades are supported; anything else is refused.

Assets are identified by `dataSource:symbol`, so the same security bought under two listings counts as two assets (see the audit's `missing_isin` check).

## Modelo 720 Inputs

`get_modelo720_inputs` computes the figures Spain's Modelo 720 asks about, for the accounts marked `modelo720.foreign` in `ACCOUNT_PROFILES`. It is read-only and deterministic, and it is not tax advice: which accounts count as foreign, and what to declare, is yours to decide.

- Valuation date: Dec 31 of `year`, or yesterday while the year is still running (`year_complete: false`, a preview).
- Cash (`accounts` category): the balance on the valuation date and the average daily balance over Q4, from balance history (a balance holds until the next snapshot). Uncovered days are reported (`q4_covered_days`), not filled in.
- Securities: per account, the quantity held on the valuation date times the last stored close on or before it, with `no_price` and `stale_price` flags.
- Each category reports whether it exceeds 50,000 - for cash, the larger of the year-end and Q4-average totals - and, given `previous_declared_accounts` / `previous_declared_securities`, whether it grew by more than 20,000 since.
- Accounts excluded from Ghostfolio's analysis are included when marked foreign. Only base-currency trades are supported.

## Benchmark Comparison and Cash Drag

`get_benchmark_comparison` puts the portfolio next to a benchmark, per month and over the period, on the same daily chart and flows as `get_monthly_returns`. The benchmark is `benchmark` (`DATASOURCE:SYMBOL`), else `DEFAULT_BENCHMARK`, else the benchmark selected in the Ghostfolio user settings. Any asset with stored prices works; an accumulating fund's price return is its total return.

| Field | Definition |
| --- | --- |
| `benchmark_return` | The benchmark's own return: its close chained over the chart days (last stored close on or before each day) |
| `excess_return` | `twr - benchmark_return` |
| `benchmark_pnl` | P&L of a benchmark holding that starts at the portfolio's opening value and receives the same flows, bought or sold at the previous close at the start of the day; compare with `market_pnl_net` (`pnl_difference`) |
| `cash.benchmark_opportunity` | Σ previous day's cash × the benchmark's return that day: what the cash would have earned in the benchmark |
| `cash.cash_drag` | `benchmark_opportunity - interest`; positive when holding cash cost money against the benchmark |

Months are measured independently; the summary runs one benchmark holding over the whole period. Cash comes from the chart (`totalCashInBaseCurrency`, which leaves out accounts excluded from analysis), and interest - gross, attributed as in `get_monthly_returns` - from the same accounts. Days before the first balance snapshot (`cash_known_from`) are left out of cash drag, since the chart shows no cash before then.

## Data Sources

Ghostfolio supports multiple data sources for market data and symbols:

- **YAHOO** - Yahoo Finance data source
- **COINGECKO** - CoinGecko for cryptocurrency data
- **MANUAL** - Manually entered data
- And other configured data sources in your Ghostfolio instance

When using tools that require a data source parameter, specify the appropriate source for your asset type.

## Using Docker

A Docker images are available on GitHub Packages for easy deployment.

```sh
# Normal STDIO image
docker pull ghcr.io/mhajder/ghostfolio-mcp:latest

# MCPO image for usage with Open WebUI
docker pull ghcr.io/mhajder/ghostfolio-mcpo:latest
```

When [OIDC authentication](#oidc--oauth-authentication-optional) is enabled, mount a volume on `/data` so OAuth client registrations survive container recreation.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests and ensure code quality (`uv run pytest && uv run ruff check .`)
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## License

GNU Affero General Public License - see LICENSE file for details.
