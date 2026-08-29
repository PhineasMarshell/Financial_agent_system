import asyncio
import json
import operator
import re
import time
from typing import TypedDict, List, Annotated, Dict, Any

from src.database.db_manager import DatabaseManager
from src.gateway.ws_fetcher import listen_to_market
from src.agents.Gold_analyst import Gold_AnalystAgent
from src.agents.BTC_analyst import BTC_AnalystAgent
from src.agents.Finance_Information_Router import Finance_Information_Router
from src.execution.trade_executor import TradeExecutor, AGENT_TO_COIN

from langgraph.graph import StateGraph, END
from langgraph.types import Send

class AgentState(TypedDict):
    """
    raw_content: 原始新闻数据 (Dict)
    dispatch_result: 当前节点的执行结果
    final_reports: 汇总所有 Agent 生成的报告，使用 operator.add 实现列表自动合并
    """
    raw_content: Dict[str, Any]
    dispatch_result: Dict[str, Any]
    final_reports: Annotated[List[Dict[str, Any]], operator.add]

class FinancialAgentSystem:
    def __init__(self):
        # 1. 初始化具体的 Agent 实例
        self.router = Finance_Information_Router()
        self.gold_analyst = Gold_AnalystAgent()
        self.btc_analyst = BTC_AnalystAgent()
        print("Agent 实例初始化成功")
        self.db = DatabaseManager()
        print("数据库初始化成功")

        # 2. 构建并编译 LangGraph 工作流
        self.workflow = self._create_workflow()
        self.app = self.workflow.compile()
        print("LangGraph 工作流构建完成")

        # 3. 异步缓冲队列：缓解高并发压力，保护 LLM 接口
        self.news_queue = asyncio.Queue(maxsize=1000)
        print("新闻缓冲队列就绪")

        # 4. 保存后台 Task 引用，防止被垃圾回收
        self._background_tasks: set = set()

    # ========== 第一层：路由节点 ==========
    async def _router_node(self, state: AgentState):
        """路由 Agent 节点逻辑"""
        news = state["raw_content"]
        formatted_text = (
            f"时间: {news.get('time')}\n"
            f"来源: {news.get('source')}\n"
            f"内容: {news.get('content')}"
        )
        
        # 调用具体的路由 Agent 模型逻辑
        result = await self.router.check_relevance(formatted_text)
        print(f"[Router] event_id={news.get('event_id')} | 判断: {result.get('reasoning', 'N/A')}")
        
        return {
            "dispatch_result": result,
            "final_reports": [result]
        }

    # ========== 第二层：并行分发逻辑 ==========
    def dispatch_router(self, state: AgentState) -> List[Send]:
        """
        条件路由逻辑：根据路由 Agent 的输出决定后续流向
        一条新闻可以同时触发多个 Analyst Agent 并行执行
        """
        dispatch = state.get("dispatch_result", {}).get('dispatch', {})
        active_sends = []

        # 检查各个金融领域的开关，为每个激活的领域创建 Send
        # if dispatch.get('gold'):
        #     active_sends.append(Send("gold_agent_node", state))
        #     print(f"[Dispatch] → gold_agent_node | event_id={state['raw_content'].get('event_id')}")

        if dispatch.get('btc'):
            active_sends.append(Send("btc_agent_node", state))
            print(f"[Dispatch] → btc_agent_node | event_id={state['raw_content'].get('event_id')}")

        # 如果没有匹配到任何领域，则结束流程
        return active_sends if active_sends else [Send("end_node", state)]
    
    # ========== 第三层：各 Analyst 节点 ==========
    async def _gold_analyst_node(self, state: AgentState):
        """黄金分析 Agent 节点逻辑"""
        news = state["raw_content"]
        event_id = news.get("event_id", "unknown")
        formatted_text = (
            f"时间: {news.get('time')}\n"
            f"来源: {news.get('source')}\n"
            f"内容: {news.get('content')}"
        )
        
        start_time = time.time()
        print(f"[Gold] 开始分析 | event_id={event_id}")
        result = await self.gold_analyst.Gold_analyse(formatted_text)
        latency_ms = int((time.time() - start_time) * 1000)
        print(f"[Gold] 分析完成 | event_id={event_id} | 耗时={latency_ms}ms")
        
        signal_data = self._extract_signal_from_report(result)
        signal_data = self._sanitize_signal_data(signal_data)

        db_record = {
        "event_id": event_id,
        "agent_name": "gold",
        "tickers": [AGENT_TO_COIN.get("gold")],
        "source": news.get("source"),
        "content_snippet": news.get("content", "")[:200],
        "signal": signal_data.get("signal"),
        "confidence": signal_data.get("confidence"),
        "timeframe": signal_data.get("timeframe"),
        "entry_price": signal_data.get("entry_price"),
        "stop_loss": signal_data.get("stop_loss"),
        "take_profit": signal_data.get("take_profit"),
        "position_size": signal_data.get("position_size"),
        "key_drivers": signal_data.get("key_drivers", []),
        "risk_factors": signal_data.get("risk_factors", []),
        "reasoning": state.get("dispatch_result", {}).get("reasoning"),
        "tools_used": signal_data.get("tools_used", []),
        "raw_report": result,
        "latency_ms": latency_ms
        }

        try:
            signal_id = await asyncio.to_thread(self.db.insert_signal, db_record)
            print(f"[DB] 信号已入库 | signal_id={signal_id} | event_id={event_id}")
        except Exception as e:
            print(f"[DB] 入库失败 | event_id={event_id} | {e}")
            import traceback
            traceback.print_exc()
        
        return {
            "dispatch_result": {"gold_report": result},
            "final_reports": [{"agent": "gold", "event_id": event_id, "report": result}]
        }

    async def _btc_analyst_node(self, state: AgentState):
        """比特币分析 Agent 节点逻辑"""
        news = state["raw_content"]
        event_id = news.get("event_id", "unknown")
        formatted_text = (
            f"时间: {news.get('time')}\n"
            f"来源: {news.get('source')}\n"
            f"内容: {news.get('content')}"
        )
        
        start_time = time.time()
        print(f"[BTC] 开始分析 | event_id={event_id}")
        result = await self.btc_analyst.BTC_analyse(formatted_text)
        latency_ms = int((time.time() - start_time) * 1000)
        print(f"[BTC] 分析完成 | event_id={event_id} | 耗时={latency_ms}ms")
        
        signal_data = self._extract_signal_from_report(result)
        signal_data = self._sanitize_signal_data(signal_data)

        db_record = {
        "event_id": event_id,
        "agent_name": "btc",
        "tickers": [AGENT_TO_COIN.get("btc")],
        "source": news.get("source"),
        "content_snippet": news.get("content", "")[:200],
        "signal": signal_data.get("signal"),
        "confidence": signal_data.get("confidence"),
        "timeframe": signal_data.get("timeframe"),
        "entry_price": signal_data.get("entry_price"),
        "stop_loss": signal_data.get("stop_loss"),
        "take_profit": signal_data.get("take_profit"),
        "position_size": signal_data.get("position_size"),
        "key_drivers": signal_data.get("key_drivers", []),
        "risk_factors": signal_data.get("risk_factors", []),
        "reasoning": state.get("dispatch_result", {}).get("reasoning"),
        "tools_used": signal_data.get("tools_used", []),
        "raw_report": result,
        "latency_ms": latency_ms
        }

        try:
            signal_id = await asyncio.to_thread(self.db.insert_signal, db_record)
            print(f"[DB] 信号已入库 | signal_id={signal_id} | event_id={event_id}")
        except Exception as e:
            print(f"[DB] 入库失败 | event_id={event_id} | {e}")
            import traceback
            traceback.print_exc()
        
        return {
            "dispatch_result": {"btc_report": result},
            "final_reports": [{"agent": "btc", "event_id": event_id, "report": result}]
        }
    
    def _sanitize_signal_data(self, signal_data: Dict[str, Any]) -> Dict[str, Any]:
        """对 Agent 输出的信号数据进行类型清洗，防止字符串数字导致后续计算报错"""
        if not signal_data:
            return {}
        
        float_fields = ["entry_price", "stop_loss", "take_profit"]
        for field in float_fields:
            val = signal_data.get(field)
            if val is not None:
                try:
                    signal_data[field] = float(val)
                except (ValueError, TypeError):
                    signal_data[field] = None
        
        int_fields = ["confidence"]
        for field in int_fields:
            val = signal_data.get(field)
            if val is not None:
                try:
                    # 钳制在 0-10，防止 CHECK 约束失败
                    signal_data[field] = max(0, min(10, int(val)))
                except (ValueError, TypeError):
                    signal_data[field] = None

        # timeframe 只允许 SCALP/INTRADAY/SWING/TREND，LLM 偶发输出中文或变体时归一化，否则置 None 防入库失败
        tf = signal_data.get("timeframe")
        if tf is not None:
            tf_norm = str(tf).strip().upper()
            TF_ALIAS = {
                "超短线": "SCALP", "短线": "SCALP",
                "日内": "INTRADAY", "日内交易": "INTRADAY",
                "波段": "SWING", "波段交易": "SWING",
                "趋势": "TREND", "趋势交易": "TREND",
            }
            tf_norm = TF_ALIAS.get(str(tf).strip(), tf_norm)
            if tf_norm not in ("SCALP", "INTRADAY", "SWING", "TREND"):
                print(f"[Sanitize] 无效 timeframe '{tf}'，已置为 None")
                tf_norm = None
            signal_data["timeframe"] = tf_norm

        return signal_data

    # ========== 结束节点 ==========
    async def _end_node(self, state: AgentState):
        """结束节点：汇总报告"""
        reports = state.get("final_reports", [])
        event_id = state["raw_content"].get("event_id", "unknown")
        print(f"[End] event_id={event_id} | 共 {len(reports)} 份报告")
        return {} 

    # ========== 构建工作流 ==========
    def _create_workflow(self):
        """构建 LangGraph 状态机"""
        workflow = StateGraph(AgentState)

        # 添加处理节点
        workflow.add_node("router_node", self._router_node)
        workflow.add_node("gold_agent_node", self._gold_analyst_node)
        workflow.add_node("btc_agent_node", self._btc_analyst_node)
        workflow.add_node("end_node", self._end_node)

        # 设置入口点
        workflow.set_entry_point("router_node")

        # 配置条件跳转
        workflow.add_conditional_edges(
            "router_node",
            self.dispatch_router,
            [
                "gold_agent_node",
                "btc_agent_node",
                "end_node"
            ]
        )

        workflow.add_edge("gold_agent_node", "end_node")
        workflow.add_edge("btc_agent_node", "end_node")

        return workflow

    # ========== 工具：从 Agent 报告提取 JSON 信号 ==========
    def _extract_signal_from_report(self, report_text: str) -> Dict[str, Any]:
        """
        从 Gold Analyst 返回的 Markdown + JSON 混合字符串中提取 JSON 信号块。
        解析失败返回空字典，由调用方处理。
        """
        if not report_text:
            return {}
        
        try:
            match = re.search(r'```json\s*(\{.*\})\s*```', report_text, re.DOTALL)
            if match:
                return json.loads(match.group(1))

            match = re.search(r'```\s*(\{.*\})\s*```', report_text, re.DOTALL)
            if match:
                return json.loads(match.group(1))

            match = re.search(r'(\{.*"signal".*\})', report_text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
                
        except json.JSONDecodeError as e:
            print(f"[SignalExtract] JSON 解析失败: {e}")
        except Exception as e:
            print(f"[SignalExtract] 提取异常: {e}")
        
        return {}

    # ========== 队列管理 ==========
    async def put_news_to_queue(self, raw_news):
        """WebSocket 回调函数，将收到的原始新闻存入队列"""
        try:
            await self.news_queue.put(raw_news)
        except Exception as e:
            print(f"队列入库异常: {e}")

    async def process_news_loop(self):
        """持续消费队列中的新闻并触发任务"""
        print("启动新闻处理")
        while True:
            raw_news = await self.news_queue.get()
            # 使用 create_task 实现多条新闻并发处理
            task = asyncio.create_task(self._process_and_done(raw_news))
            # 保存 Task 引用，防止被垃圾回收
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    async def _process_and_done(self, raw_news):
        """执行工作流并正确标记队列完成"""
        try:
            await self._run_workflow(raw_news)
        finally:
            self.news_queue.task_done()
            
    async def _run_workflow(self, raw_news):
        """执行单个工作流"""
        event_id = raw_news.get("event_id", "unknown")
        try:
            initial_state = {"raw_content": raw_news}
            result = await self.app.ainvoke(initial_state)
            print(f"[Workflow] 完成 | event_id={event_id}")
        except Exception as e:
            print(f"[Workflow] 报错 | event_id={event_id} | {e}")
            import traceback
            traceback.print_exc()

    # ========== 新增：Executor 定时轮询 ==========
    async def executor_loop(self):
        """定时轮询执行未执行信号"""
        print("[ExecutorLoop] 启动交易执行轮询...")
        # 延迟 10 秒启动，等系统初始化完成
        await asyncio.sleep(10)

        # 传入共享的 DB 实例，避免多实例并发写入
        executor = TradeExecutor(db=self.db)

        while True:
            try:
                # print(f"\n{'='*60}")
                # print("[ExecutorLoop] 开始扫描未执行信号...")
                # 用 to_thread 避免同步方法阻塞事件循环
                await asyncio.to_thread(executor.run, limit=10)
                # print("[ExecutorLoop] 本轮扫描完成")
            except Exception as e:
                print(f"[ExecutorLoop] 异常: {e}")
                import traceback
                traceback.print_exc()

            # 每 30 秒扫描一次
            await asyncio.sleep(30)

async def main():
    system = FinancialAgentSystem()
    
    # 同时运行 WebSocket 监听，新闻处理循环和 trade 执行器
    await asyncio.gather(
        listen_to_market(callback=system.put_news_to_queue),
        system.process_news_loop(),
        system.executor_loop()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("程序已停止")

