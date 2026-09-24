# -*- coding: utf-8 -*-
# [HYBRID] 三级混合知识检索适配器
# 第一级：Skill 知识库（QA问答库优先 + BM25知识检索 + 抽取式回答/润色）
# 第二级：Chatchat 向量知识库（语义检索兜底）
# 第三级：模型直接回答（保底，纯对话）
# 接口与 ChatchatAgentSystem 完全一致，可直接替换使用

import re
import logging
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from local_knowledge_qa import KnowledgeQAConfig, KnowledgeQAEngine

# ── 复用原有 Chatchat 系统 ──
from chatchat_adapter import ChatchatAgentSystem

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

import chatchat_config as config


logger = logging.getLogger("HybridSkillChatchat")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)


NO_INFO_KEYWORDS = [
    "未找到", "无法回答", "暂无法回答", "抱歉", "找不到",
    "没有相关", "没有找到", "不太清楚", "不知道", "无法根据",
    "知识范围", "超出", "暂未收录",
]

# ── 动作指令直通表：纯动作指令（如"来一段舞蹈""行个礼"）不进入问答链路 ──
# 路径需与 actions/ 目录文件名一致（与 main.py ACTION_MAP 的映射相同）
ACTION_COMMANDS = {
    "跳舞": ("actions/dance.json", "好的，这就为您献上一段舞蹈！"),
    "舞蹈": ("actions/dance.json", "好的，这就为您献上一段舞蹈！"),
    "行礼": ("actions/dun.json", "好的，向您行礼！"),
    "拱手礼": ("actions/greet.json", "拱手见礼！"),
    "跳跃": ("actions/jump.json", "跳起来啦！"),
    "抓耳挠腮": ("actions/scratch.json", "看我抓耳挠腮！"),
    "向前": ("actions/forward.json", "好的，向前走两步。"),
    "向后": ("actions/back.json", "好的，向后退两步。"),
    "向前走": ("actions/forward.json", "好的，向前走两步。"),
    "向后走": ("actions/back.json", "好的，向后退两步。"),
    "常态": ("actions/normal.json", "好的，回到常态。"),
    "空闲": ("actions/idle.json", "好的，休息一下。"),
    # [GESTURE] 抽象手势触发：与摄像头识别保持一致（挥手->拱手礼，捏合/搓手->抓耳挠腮）
    "挥手": ("actions/greet.json", "挥手致意，这就拱手见礼！"),
    "捏合": ("actions/scratch.json", "捏合搓手，瞧我抓耳挠腮！"),
    "搓手": ("actions/scratch.json", "捏合搓手，瞧我抓耳挠腮！"),
    # [FIGHT] 打戏六动作
    "出拳": ("actions/punch.json", "吃我一拳！"),
    "踢腿": ("actions/kick.json", "看我的飞踢！"),
    "格挡": ("actions/block.json", "接招，格挡防御！"),
    "前冲": ("actions/lunge.json", "看招，前冲！"),
    "后撤": ("actions/retreat.json", "后撤躲闪！"),
    "受击": ("actions/hit.json", "哎呀，被打中了！"),
}


class HybridResponse(NamedTuple):
    actions: List[str]
    answer: str
    match_type: str
    confidence: float


class HybridAgentSystem:
    """
    三级回答体系：
    第一级：Skill 知识库（QA问答库优先 + BM25知识检索）
    第二级：Chatchat 向量知识库
    第三级：Ollama 模型直接回答（保底）
    """

    def __init__(self, action_mapping: Dict[str, str]):
        self.action_mapping = {k.strip().lower(): v for k, v in action_mapping.items()}

        # ── 第一级：Skill 引擎 ──
        project_root = Path(__file__).resolve().parent
        skill_config = KnowledgeQAConfig(
            discovery_root=project_root,
            qa_match_threshold=0.45,
            knowledge_match_threshold=0.25,
        )
        self.skill_engine = KnowledgeQAEngine(skill_config)
        skill_health = self.skill_engine.health()
        print(f"[Skill] ====== 知识库加载状态 ======")
        print(f"[Skill] 文档数量: {skill_health.get('documents', 0)}")
        print(f"[Skill] QA问答对: {skill_health.get('qa_pairs', 0)}")
        print(f"[Skill] 知识块数: {skill_health.get('chunks', 0)}")
        print(f"[Skill] 状态: {skill_health.get('status', 'unknown')}")
        print(f"[Skill] QA匹配阈值: {skill_config.qa_match_threshold}")
        print(f"[Skill] 知识匹配阈值: {skill_config.knowledge_match_threshold}")
        if skill_health.get('source_paths'):
            print(f"[Skill] 知识源:")
            for p in skill_health['source_paths']:
                print(f"[Skill]   - {p}")
        if skill_health.get("parse_errors"):
            print(f"[Skill] 解析警告: {skill_health['parse_errors']}")
        print(f"[Skill] =========================")

        # ── 第二级：Chatchat 系统（复用原有）──
        self.chatchat_system = ChatchatAgentSystem(action_mapping)

        # ── 第三级：Ollama 客户端（复用 Chatchat 的）──
        if HAS_OPENAI:
            self._ollama_client = OpenAI(
                base_url=config.OLLAMA_BASE_URL,
                api_key=config.OLLAMA_API_KEY,
            )
        else:
            self._ollama_client = None

        print(f"[Hybrid] 三级回答体系已就绪：Skill → Chatchat → 模型直答")

    async def process_input(self, user_input: str) -> Tuple[List[str], str]:
        result = await self.process_input_with_metadata(user_input)
        return result.actions, result.answer

    async def process_input_with_metadata(self, user_input: str) -> HybridResponse:
        text = user_input.strip()
        if not text:
            default = self.action_mapping.get("常态", "actions/idle")
            return HybridResponse([default], "你好，有什么想了解的吗？", "empty", 0.0)

        direct = self._match_action_command(text)
        if direct:
            action_path, reply = direct
            logger.info(f"[动作直通] 识别动作指令: '{text}' -> {action_path}")
            return HybridResponse([action_path], reply, "action", 1.0)

        # ═══════ 第一级：Skill 知识库（QA 优先 + BM25 知识检索），生成动作（gemma3:4b）═══════
        try:
            skill_result = self.skill_engine.query(text)
            if skill_result.match_type == "qa":
                answer = skill_result.answer
                logger.info(f"第一级[Skill] 命中 (qa): "
                            f"{answer[:80]}..." if len(answer) > 80 else f"[Skill] 命中 (qa): {answer}")
                actions = await self._generate_action(text, answer)
                return HybridResponse(actions, answer, "qa", skill_result.confidence)
            if skill_result.match_type == "knowledge":
                base_answer = skill_result.answer
                retrieval = self.skill_engine.retrieve(text)
                evidence = "\n".join(retrieval.contexts[:3])
                logger.info(f"[Skill] 命中 (knowledge) 原始: {base_answer[:60]}...")
                answer = await self._polish_knowledge_answer(text, evidence, base_answer)
                actions = await self._generate_action(text, answer)
                return HybridResponse(actions, answer, "knowledge", skill_result.confidence)
            logger.info("[Skill] 未找到证据，进入第二级 Chatchat")
        except Exception as e:
            logger.error(f"[Skill] 检索异常: {e}，进入第二级 Chatchat")

        # ═══════ 第二级：Chatchat 向量库回答，生成动作（gemma3:4b） ═══════
        try:
            answer = await self.chatchat_system.process_input(text)
            actions = await self._generate_action(text, answer)
            if not self._is_no_info_answer(answer):
                logger.info(f"第二级[Chatchat] 有答案: {answer}")
                return HybridResponse(actions, answer, "chatchat", 0.0)
            logger.info("[Chatchat] 无有效答案，进入第三级模型直答")
        except Exception as e:
            logger.error(f"[Chatchat] 调用失败: {e}，进入第三级模型直答")

        # ═══════ 第三级：模型直接回答以及生成动作（gemma3:4b）═══════
        try:
            answer = await self._direct_llm_answer(text)
            logger.info(f"第三级[模型直答] 回答: {answer}")
            actions = await self._generate_action(text, answer)
            return HybridResponse(actions, answer, "direct", 0.0)
        except Exception as e:
            logger.error(f"[模型直答] 失败: {e}")
            default = self.action_mapping.get("常态", "actions/idle")
            return HybridResponse([default], "抱歉，我处理时遇到了一些问题，请稍后再试。", "error", 0.0)

    def _match_action_command(self, text: str) -> Optional[Tuple[str, str]]:
        """识别纯动作指令（如"来一段舞蹈""向前走两步"），命中返回 (动作路径, 回复)。

        规则：输入为短指令，去掉语气虚词后以动作关键词开头才直通，
        避免误伤"海宁皮影戏的舞蹈有什么特点"等知识问答。
        """
        if len(text) > 12:
            return None
        stripped = re.sub(
            r"[来给能会请让为把帮我一一下段个次支首再表演吧，。！？!?、；;：:\s]",
            "",
            text,
        ).strip()
        if not stripped or len(stripped) > 8:
            return None
        for keyword, (action_path, reply) in ACTION_COMMANDS.items():
            if stripped.startswith(keyword):
                if Path(action_path).exists():
                    return action_path, reply
        return None

    def _is_no_info_answer(self, answer: str) -> bool:
        """判断回答是否为'找不到信息'类型的无效回答"""
        if not answer or not answer.strip():
            return True
        ans = answer.strip()
        for kw in NO_INFO_KEYWORDS:
            if kw in ans and len(ans) < 60:
                return True
        return False

    async def _polish_knowledge_answer(self, query: str, evidence: str, fallback: str) -> str:
        """第一级知识命中后，用 Ollama 将检索证据润色组织为自然回答（方案A）

        约束：严格依据证据作答，禁止编造；Ollama 不可用时回退为抽取式答案。
        """
        if not self._ollama_client or not HAS_OPENAI:
            return fallback

        sys_prompt = (
            "你是海宁皮影戏数字人讲解员"
            "下面是从知识库检索到的原始资料，请严格依据资料回答用户的问题。\n"
            "要求：\n"
            "1. 只能使用资料中出现的信息，禁止编造、禁止延伸资料之外的内容；\n"
            "2. 将信息组织成自然流畅的1-2段中文讲解，结构清晰、层次分明；\n"
            "3. 若资料信息不足以回答，如实说明资料中提到了什么，不强行作答。"
        )

        user_content = f"用户问题：{query}\n\n参考资料：\n{evidence}"

        try:
            resp = self._ollama_client.chat.completions.create(
                model=config.ACTION_MODEL,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.3,
            )
            polished = (resp.choices[0].message.content or "").strip()
            if polished:
                logger.info(f"[Skill] 润色完成: {polished[:80]}..." if len(polished) > 80 else f"[Skill] 润色完成: {polished}")
                return polished
            return fallback
        except Exception as e:
            logger.error(f"[Skill] 润色失败，使用抽取式答案: {e}")
            return fallback

    async def _direct_llm_answer(self, query: str) -> str:
        """第三级：直接用 Ollama 模型回答（无知识库约束）"""
        if not self._ollama_client or not HAS_OPENAI:
            return "抱歉，我暂时无法回答这个问题。"

        sys_prompt = """传统皮影戏数字人应答规范：
        一、应答格式要求
        输出结构必须为：[文本内容]
        每次回答不超过100字
        二、示例
        用户输入："你是谁？"
        文本回复："您好，我是海宁皮影戏数字人，擅长表演与制作，很高兴为您服务"
        用户输入："皮影起源是什么？"
        文本回复："皮影源于汉代，经历唐宋晋变革与民间创新，至今已有两千多年历史"
        用户输入："你能为我跳个舞吗？"
        文本回复："好的，请看我在灯光与音乐中翩翩起舞，生动传情，这就是皮影戏的表演"
        用户输入："请你向前走两步"
        文本回复："好的，我将按您的指令执行动作"
        三、验证流程
        1. 确认回答长度不超过100字
        """
            
        try:
            resp = self._ollama_client.chat.completions.create(
                model=config.ACTION_MODEL,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": query},
                    {"role": "assistant", "content": "您好，我是海宁皮影戏数字人，擅长表演与问答，很高兴为您服务。"}
    
                ],
                temperature=0.3
            )
            print(f"[模型直答] Ollama 回答: {resp.choices[0].message.content[:80]}..." if len(resp.choices[0].message.content) > 80 else f"[模型直答] Ollama 回答: {resp.choices[0].message.content}")
            return resp.choices[0].message.content or "抱歉，我暂时无法回答这个问题。"
        except Exception as e:
            logger.error(f"[模型直答] Ollama 调用失败: {e}")
            return "抱歉，我暂时无法回答这个问题。"

    async def _generate_action(self, user_input: str, text_reply: str) -> List[str]:
        """生成动作（复用 Chatchat 系统的动作生成逻辑）"""
        try:
            action_input = f"""用户输入：{user_input}
AI 回复：{text_reply}

请根据以上对话内容判断皮影数字人应该执行什么动作。"""
            raw_action = await self._call_action_model(action_input)
            return self._parse_actions(raw_action)
        except Exception as e:
            logger.error(f"[动作生成] 失败: {e}，使用默认动作")
            default = self.action_mapping.get("常态", "actions/idle")
            return [default]

    async def _call_action_model(self, context: str) -> str:
        """调用 Ollama 生成动作"""
        action_list = list(self.action_mapping.keys())
        sys_msg = f"""你是皮影戏数字人的动作生成器。

可用动作列表（只能从以下动作中选择）：
{action_list}

输出格式（严格遵循）：
动作：[动作名称]
多个动作：动作：[跳舞][拱手礼]

示例：
输入："用户正在询问我的名字" → 输出：动作：[拱手礼]
输入："用户正在请求展示舞蹈技能" → 输出：动作：[跳舞]
输入："用户正在请求具体的动作指令：向前走，行礼，向后走" → 输出：动作：[向前走][行礼][向后走]
输入："用户挥了挥手打招呼" → 输出：动作：[拱手礼]
输入："用户做了捏合、搓手的动作" → 输出：动作：[抓耳挠腮]
输入："用户正在请求具体的动作指令：出拳，踢腿" → 输出：动作：[出拳][踢腿]
输入："用户正在请求具体的动作指令：格挡" → 输出：动作：[格挡]
输入："用户正在请求具体的动作指令：前冲，后撤" → 输出：动作：[前冲][后撤]
输入："用户正在请求具体的动作指令：受击" → 输出：动作：[受击]

注意：
1. 只输出动作指令，不要输出任何解释
2. 动作名称必须完全匹配可用动作列表
3. 方括号不能省略
4. 如果无法确定动作，输出"动作：[常态]"
"""

        if self._ollama_client and HAS_OPENAI:
            try:
                resp = self._ollama_client.chat.completions.create(
                    model=config.ACTION_MODEL,
                    messages=[
                        {"role": "system", "content": sys_msg},
                        {"role": "user", "content": context},
                    ],
                    temperature=0.1,
                    max_tokens=200,
                )
                return resp.choices[0].message.content or "动作：[常态]"
            except Exception as e:
                logger.error(f"[动作生成] Ollama 失败: {e}")
                return "动作：[常态]"
        else:
            return "动作：[常态]"

    def _parse_actions(self, response: str) -> List[str]:
        """解析动作响应"""
        if not response:
            default = self.action_mapping.get("常态", "actions/idle")
            return [default]

        raw_actions = re.findall(r"\[([^\]]+?)\]", response)
        if not raw_actions:
            default = self.action_mapping.get("常态", "actions/idle")
            return [default]

        found = []
        for name in raw_actions:
            path = self.action_mapping.get(name.strip().lower())
            if path:
                found.append(path)

        return found[:3] if found else [self.action_mapping.get("常态", "actions/idle")]
