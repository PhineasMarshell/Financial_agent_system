import pandas as pd
import json

df = pd.read_csv("data.csv")

def safe_json(s):
    """安全解析数据库导出的json字符串"""
    if pd.isna(s):
        return None
    try:
        return json.loads(s.replace('""', '"'))
    except:
        return None

# 解析数组字段
df["key_drivers_json"] = df["key_drivers"].apply(safe_json)
df["risk_factors_json"] = df["risk_factors"].apply(safe_json)
df["tickers_json"] = df["tickers"].apply(safe_json)
df["tools_used_json"] = df["tools_used"].apply(safe_json)

# 只保留核心业务字段，扔掉巨长raw_report（那个就是乱糟糟的完整报告）
keep_cols = [
    "id","event_id","agent_name","source","content_snippet",
    "signal","confidence","timeframe","entry_price","stop_loss",
    "take_profit","position_size","execution_status","execution_reason","created_at"
]
df_clean = df[keep_cols].copy()

# 数组转逗号拼接字符串，方便Excel查看
df_clean["key_drivers"] = df["key_drivers_json"].apply(lambda x: "; ".join(x) if x else "")
df_clean["risk_factors"] = df["risk_factors_json"].apply(lambda x: "; ".join(x) if x else "")
df_clean["tickers"] = df["tickers_json"].apply(lambda x: "; ".join(x) if x else "")
df_clean["tools_used"] = df["tools_used_json"].apply(lambda x: "; ".join(x) if x else "")

# 输出xlsx，不要csv，避免逗号捣乱
df_clean.to_excel("clean_output.xlsx", index=False)
print("✅ 清洗完成，输出 clean_output.xlsx")
