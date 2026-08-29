import asyncio
TOOL_REGISTRY = {}

async def execute_tool(tool_name: str, **kwargs) -> str:
    """统一的工具调度执行"""
    target_tool = TOOL_REGISTRY.get(tool_name)
    if not target_tool:
        return f'{{"error": "全局工具库中未找到工具: {tool_name}"}}'
    
    try:
        # 解包参数并执行真实的工具函数
        result = await asyncio.to_thread(target_tool, **kwargs)
        return result
    except Exception as e:
        return f'{{"error": "工具 {tool_name} 执行异常: {str(e)}"}}'