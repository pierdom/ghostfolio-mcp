"""
Ghostfolio MCP Tools Package
"""

from fastmcp import FastMCP

from ghostfolio_mcp.models import GhostfolioConfig
from ghostfolio_mcp.tools.accounts import register_accounts_tools
from ghostfolio_mcp.tools.activities import register_activities_tools
from ghostfolio_mcp.tools.assets import register_assets_tools
from ghostfolio_mcp.tools.benchmarks import register_benchmarks_tools
from ghostfolio_mcp.tools.exchange_rates import register_exchange_rates_tools
from ghostfolio_mcp.tools.export import register_export_tools
from ghostfolio_mcp.tools.imports import register_imports_tools
from ghostfolio_mcp.tools.market_data import register_market_data_tools
from ghostfolio_mcp.tools.portfolio import register_portfolio_tools
from ghostfolio_mcp.tools.symbols import register_symbols_tools
from ghostfolio_mcp.tools.system import register_system_tools
from ghostfolio_mcp.tools.user import register_user_tools
from ghostfolio_mcp.tools.watchlist import register_watchlist_tools


def register_tools(mcp: FastMCP, config: GhostfolioConfig) -> None:
    """Register all Ghostfolio tools with the FastMCP server."""
    register_accounts_tools(mcp, config)
    register_activities_tools(mcp, config)
    register_assets_tools(mcp, config)
    register_benchmarks_tools(mcp, config)
    register_exchange_rates_tools(mcp, config)
    register_export_tools(mcp, config)
    register_imports_tools(mcp, config)
    register_market_data_tools(mcp, config)
    register_portfolio_tools(mcp, config)
    register_symbols_tools(mcp, config)
    register_system_tools(mcp, config)
    register_user_tools(mcp, config)
    register_watchlist_tools(mcp, config)
