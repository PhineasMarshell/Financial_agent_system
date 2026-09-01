from ddgs import DDGS
import json

def search_news(query: str, max_results: int = 5, time_limit: str = 'd') -> str:
    """搜索最新新闻舆情。time_limit: d=当天, w=本周, m=本月"""
    try:
        with DDGS() as ddgs:
            results = ddgs.news(
                query,
                region="wt-wt",
                safesearch="moderate",
                timelimit=time_limit,
                backend="bing",
                max_results=max_results
            )
            
        return json.dumps({
            "query": query,
            "status": f"成功获取 {len(results)} 条相关资讯",
            "data": results
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps(
            {"error": f"搜索工具报错: {str(e)}"},
            ensure_ascii=False,
            indent=2
        )
    
if __name__ == "__main__":
    print(search_news("特朗普遭受枪击", max_results=2))