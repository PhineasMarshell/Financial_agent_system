import os 
import re
import json
from openai import AsyncOpenAI
from dotenv import load_dotenv


load_dotenv()

class Finance_Information_Router:
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL")
        )

    async def check_relevance(self, news_text):
        system_prompt = """
        你是一个高水平的金融分析前置处理专家，专门负责从复杂的实时社交媒体（如Twitter/X）及新闻流中，识别、分析并分拣出具有交易价值的信息。你擅长通过隐含逻辑推导，判断一条碎片信息可能影响的金融标的。

        ## 可交易标的（严格限制）
        你只判断以下两个标的，输出中只能用对应的领域开关，禁止输出任何其他标的：
        - 黄金: PAXG → 领域 gold
        - 比特币: BTC → 领域 btc

        Logic Derivation Rules (逻辑推导准则):
        你需要根据内容进行深层联想，而不只是关键词匹配：
        1. **人物关联**:
        - Elon Musk → 触发加密市场情绪，与 BTC 关联
        - Trump → 宏观流动性 / 加密政策预期，与 BTC 关联
        - 鲍威尔/耶伦 → 宏观流动性，与 BTC、黄金(PAXG) 关联

        2. **事件关联**:
        - 战争/地缘动荡/避险 → PAXG（黄金）
        - 央行政策/降息加息 → 宏观流动性，BTC 与 PAXG 均可能
        - ETF/机构入场/监管/交易所安全事件 → BTC

        3. **宏观联动**:
        - 重大政策/地缘波动 → 多选，同时激活 BTC（流动性）和 PAXG（避险）

        Task Requirements:
        1. **分类判断**: 将信息映射到以下两个维度：[**BTC**: 比特币, **gold**: 黄金（PAXG）]。
        2. **多选逻辑**: 若信息同时影响多个标的（如：机构买入BTC），相应标签均设为 `true`。
        3. **噪声过滤**: 对于无直接金融交易价值的内容（如：日常生活分享、无实质影响力的政治寒暄、非关键民生新闻），请将所有标签设为 `false`，主程序将自动丢弃。
        4. **输出格式**: 必须严格输出 JSON 格式，不得包含任何解释性文字。

        Output Schema (JSON Only):
        {
        "reasoning": "简短的一句话推导逻辑",
        "dispatch": {
            "btc": boolean,
            "gold": boolean
        },
        }
        
        ## Examples:
        - **输入**: "摩根士丹利比特币现货ETF已购入8360万美元BTC"
        - **输出**:
        {
            "reasoning": "主流金融机构增持BTC现货ETF，直接利好加密货币市场。",
            "dispatch": {"btc": true, "gold": false},
        }

        - **输入**: "突发：特朗普在宾州集会遭遇枪击，市场避险情绪急剧升温"
        - **输出**:
        {
            "reasoning": "重大地缘政治事件触发避险需求，利好黄金。",
            "dispatch": {"btc": false, "gold": true},
        }

        - **输入**: "美联储宣布降息50个基点，市场流动性预期改善"
        - **输出**:
        {
            "reasoning": "降息释放流动性，利好风险资产和避险资产。",
            "dispatch": {"btc": true, "gold": true},
        }

        - **输入**: "哥斯达黎加附近海域发生5.7级地震"
        - **输出**:
        {
            "reasoning": "普通地缘灾害，对 Hyperliquid 可交易品种无直接显著影响。",
            "dispatch": {"btc": false, "gold": false},
        }
        """

        try:
            # 使用 temperature=0.0 保证输出结果的确定性和逻辑一致性
            response = await self.client.chat.completions.create(
                model="qwen3.7-plus-2026-05-26",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": news_text}
                ],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            raw_content = response.choices[0].message.content.strip()
            
            # 处理 Markdown 格式及冗余字符
            clean_content = re.sub(r'^```json\s*|```$', '', raw_content, flags=re.MULTILINE).strip()
            
            return json.loads(clean_content)
        except Exception as e:
            print(f"守门员 Agent 报错: {e}")
            return {
                "reasoning": "系统解析异常",
                "dispatch": {"btc": False, "gold": False}
            }
    
if __name__ == "__main__":
    import asyncio
    async def run_tests():
        gatekeeper = Finance_Information_Router()
        
        test1 = "ID: 999001 来源: Reuters 内容: 【美联储主席鲍威尔：通胀数据好于预期，降息窗口已打开，市场对流动性增加表示乐观】"

        test2 = "ID: 999002 来源: elonmusk 内容: Grok 3 training is going incredibly well. The compute cluster of 100k H100s is humming. Real intelligence is coming this year."

        test3 = "ID: 999003 来源: APNews 内容: 【突发：中东某产油区遭遇无人机袭击，当地石油出口设施部分受损，地区局势骤然紧张】"

        test4 = "ID: 999004 来源: THE BLOCK 内容: BlackRock expands its tokenized fund BUIDL to multiple blockchains including Avalanche and Aptos, aiming to provide institutional-grade government bond yields to on-chain investors."

        test5 = "ID: 999005 来源: xinlang 内容: 【今日立秋，北方多地气温小幅回落，专家建议市民注意预防季节性流感】"

        print(await gatekeeper.check_relevance(test1))

    asyncio.run(run_tests())