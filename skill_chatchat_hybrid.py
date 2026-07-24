# -*- coding: utf-8 -*-
# [HYBRID] 三级混合知识检索适配器
# 第一级：Skill 知识库（QA问答库 + BM25检索 + 抽取式回答）
# 第二级：Chatchat 向量知识库（语义检索兜底）
# 第三级：模型直接回答（保底，纯对话）
# 接口与 ChatchatAgentSystem 完全一致，可直接替换使用

import sys
import re
import logging
from pathlib import Path
from typing import Dict, List, Tuple

# ── 加载 portable-knowledge-qa Skill ──
SKILL_ROOT = Path(__file__).resolve().parent / "portable-knowledge-qa"
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from portable_knowledge_qa import KnowledgeQAConfig, KnowledgeQAEngine

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
        text = user_input.strip()
        if not text:
            default = self.action_mapping.get("常态", "actions/idle")
            return [default], "你好，有什么想了解的吗？"

        # ═══════ 第一级：Skill 知识库 ═══════
        try:
            skill_result = self.skill_engine.query(text)
            if skill_result.match_type in ("qa", "knowledge"):
                answer = skill_result.answer
                logger.info(f"[Skill] 命中 ({skill_result.match_type}): "
                            f"{answer[:80]}..." if len(answer) > 80 else f"[Skill] 命中 ({skill_result.match_type}): {answer}")
                actions = await self._generate_action(text, answer)
                return actions, answer
            else:
                logger.info("[Skill] 未找到证据，进入第二级 Chatchat")
        except Exception as e:
            logger.error(f"[Skill] 检索异常: {e}，进入第二级 Chatchat")

        # ═══════ 第二级：Chatchat 向量库 ═══════
        try:
            actions, answer = await self.chatchat_system.process_input(text)
            if not self._is_no_info_answer(answer):
                logger.info(f"[Chatchat] 有答案: {answer[:80]}..." if len(answer) > 80 else f"[Chatchat] 有答案: {answer}")
                return actions, answer
            else:
                logger.info("[Chatchat] 无有效答案，进入第三级模型直答")
        except Exception as e:
            logger.error(f"[Chatchat] 调用失败: {e}，进入第三级模型直答")

        # ═══════ 第三级：模型直接回答（保底）═══════
        try:
            answer = await self._direct_llm_answer(text)
            logger.info(f"[模型直答] 回答: {answer[:80]}..." if len(answer) > 80 else f"[模型直答] 回答: {answer}")
            actions = await self._generate_action(text, answer)
            return actions, answer
        except Exception as e:
            logger.error(f"[模型直答] 失败: {e}")
            default = self.action_mapping.get("常态", "actions/idle")
            return [default], "抱歉，我处理时遇到了一些问题，请稍后再试。"

    def _is_no_info_answer(self, answer: str) -> bool:
        """判断回答是否为'找不到信息'类型的无效回答"""
        if not answer or not answer.strip():
            return True
        ans = answer.strip()
        for kw in NO_INFO_KEYWORDS:
            if kw in ans and len(ans) < 60:
                return True
        return False

    async def _direct_llm_answer(self, query: str) -> str:
        """第三级：直接用 Ollama 模型回答（无知识库约束）"""
        if not self._ollama_client or not HAS_OPENAI:
            return "抱歉，我暂时无法回答这个问题。"

        sys_prompt = (
            "你是海宁皮影戏数字人讲解员，名叫'海宁小状元'。"
            "你用中文回答用户的问题，语气友好、自然，符合讲解员的身份。"
            "对于海宁皮影戏相关的问题，请尽量给出准确的回答。"
        )

        try:
            resp = self._ollama_client.chat.completions.create(
                model=config.ACTION_MODEL,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": query},
                ],
                temperature=0.7,
            )
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
