HYPERLIQUID_QUERY_SCHEMA = {
  "type": "function",
  "function": {
    "name": "hyperliquid_query",
    "description": "查询 Hyperliquid 交易所的加密货币最新价格或 K 线趋势。",
    "parameters": {
      "type": "object",
      "properties": {
        "action": {
          "type": "string",
          "enum": ["get_latest_price", "get_candles"],
          "description": "查询类型：仅需当前报价选 'get_latest_price'；需要分析近期走势/布林带/高低点选 'get_candles'。"
        },
        "coin": {
          "type": "string",
          "description": "币种代码，如 BTC, ETH, SOL, DOGE, HYPE 等。",
          "enum": [
            "BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "AVAX", "DOT", 
            "LINK", "MATIC", "ARB", "OP", "DOGE", "SHIB", "HYPE", 
            "LTC", "BCH", "ETC", "UNI", "AAVE", "SUSHI", "CRV",
            "PAXG", "FARTCOIN", "GOAT", "MOODENG", "TRUMP", "WIF",
            "PEPE", "BONK", "FLOKI", "LUNC", "NEIRO", "SPX"
          ]
        },
        "interval": {
          "type": "string",
          "enum": ["1m", "5m", "15m", "1h", "4h", "1d"],
          "description": "K线周期。仅当 action 为 'get_candles' 时生效。"
        },
        "limit": {
          "type": "integer",
          "description": "获取的K线数量（建议 10-50 根）。仅当 action 为 'get_candles' 时生效。"
        }
      },
      "required": ["action", "coin"]
    }
  }
}

SEARCH_NEWS_SCHEMA = {
    "type": "function",
    "function": {
      "name": "search_news",
      "description": "调用duckduckgo查询全网最新的金融新闻，突发事件进展和市场舆情",
      "parameters": {
        "type": "object",
        "properties": {
          "query": {
            "type": "string",
            "description": "搜索的关键词，例如 '美联储 最新讲话', '中东局势 黄金影响'。支持中英文。",          
          },
          "max_results": {
            "type": "integer",
            "description": "需要返回的新闻条数。"
          },
          "time_limit": {
            "type": "string",
            "enum": ["d", "w", "m"],
            "default": "d",
            "description": "获取什么时间段的新闻，例如一天内关于中东局势的讨论，一个月内外界对黄金涨价趋势的看法。"
          }
        },
        "required": ["query", "max_results", "time_limit"]
      }
    }
}

ALL_SCHEMAS = [HYPERLIQUID_QUERY_SCHEMA, SEARCH_NEWS_SCHEMA]