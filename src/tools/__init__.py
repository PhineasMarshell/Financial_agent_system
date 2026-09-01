from .registry import TOOL_REGISTRY, execute_tool
from .tool_schemas import ALL_SCHEMAS
from .hy_finance_tools import hyperliquid_query
from .search_tools import search_news

TOOL_REGISTRY["hyperliquid_query"] = hyperliquid_query
TOOL_REGISTRY["search_news"] = search_news

__all__ = ["execute_tool", "ALL_SCHEMAS"]