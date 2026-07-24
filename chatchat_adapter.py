# -*- coding: utf-8 -*-
# [CHATCHAT] LangChain-Chatchat 适配器
# 本文件为新增文件，提供 ChatchatAgentSystem 类
# 接口与 DigitalHumanAgentSystem 完全一致，可直接替换使用

import re
import json
import logging
from typing import Dict, List, Tuple, Any

from api_client import ChatchatAPIClient
import chatchat_config as config

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


logger = logging.getLogger("ChatchatAdapter")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)


class ChatchatAgentSystem:
    def __init__(self, action_mapping: Dict[str, str]):
        self.action_mapping = {k.strip().lower(): v for k, v in action_mapping.items()}
        self.api_client = ChatchatAPIClient()

        if HAS_OPENAI:
            self._ollama_client = OpenAI(
                base_url=config.OLLAMA_BASE_URL,
                api_key=config.OLLAMA_API_KEY,
            )
        else:
            self._ollama_client = None

        print(f"[CHATCHAT] 初始化 Chatchat 数字人系统，动作映射: {list(self.action_mapping.keys())}")
        print(f"[CHATCHAT] Chatchat 服务地址: {config.BASE_URL}")
        print(f"[CHATCHAT] 知识库: {config.KNOWLEDGE_BASE_NAME}")
        print(f"[CHATCHAT] 动作判断模型: {config.ACTION_MODEL} (Ollama)")

    async def process_input(self, user_input: str) -> Tuple[List[str], str]:
        try:
            text_reply = await self._query_knowledge_base(user_input)
            logger.info(f"知识库问答结果: {text_reply[:100]}..." if len(text_reply) > 100 else f"知识库问答结果: {text_reply}")

            action_input = self._build_action_context(user_input, text_reply)
            raw_action = await self._generate_action(action_input)
            logger.info(f"动作响应: {raw_action}")
            actions = self._parse_actions(raw_action)

            clean_text = self._clean_text(text_reply)
            return actions, clean_text

        except Exception as e:
            logger.error(f"Chatchat 处理失败: {e}")
            default = self.action_mapping.get("常态", "actions/idle")
            return [default], "系统处理失败，请重试..."

    async def _query_knowledge_base(self, query: str) -> str:
        try:
            result = await self.api_client.knowledge_base_chat(
                query=query,
                kb_name=config.KNOWLEDGE_BASE_NAME,
                stream=config.USE_STREAM,
                temperature=0.3,
            )

            answer = self._extract_answer(result)

            if not answer:
                logger.warning("知识库未返回答案，将回退到普通对话")
                return await self._fallback_chat(query)

            return answer

        except Exception as e:
            logger.warning(f"知识库问答失败，回退到普通对话: {e}")
            return await self._fallback_chat(query)

    def _extract_answer(self, result: Any) -> str:
        logger.info(f"[DEBUG-EXTRACT] 输入类型: {type(result).__name__}")
        if isinstance(result, (str, dict, list)):
            preview = str(result)[:300]
            logger.info(f"[DEBUG-EXTRACT] 输入内容(前300字): {preview}")
        else:
            logger.info(f"[DEBUG-EXTRACT] 输入内容: {result}")

        if result is None:
            logger.info(f"[DEBUG-EXTRACT] 分支: result is None → 返回空")
            return ""

        if isinstance(result, str):
            stripped = result.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    parsed = json.loads(stripped)
                    logger.info(f"[DEBUG-EXTRACT] 分支: 字符串JSON解析成功 → 递归")
                    return self._extract_answer(parsed)
                except (json.JSONDecodeError, TypeError) as e:
                    logger.info(f"[DEBUG-EXTRACT] 字符串JSON解析失败: {e}")
            match = re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', stripped)
            if match:
                content = match.group(1)
                content = content.replace("\\n", "\n").replace('\\"', '"').replace("\\t", "\t")
                logger.info(f"[DEBUG-EXTRACT] 分支: 正则匹配content成功 → {content[:50]}...")
                return content
            logger.info(f"[DEBUG-EXTRACT] 分支: 纯字符串 → 直接返回")
            return stripped

        if isinstance(result, dict):
            if "answer" in result and isinstance(result["answer"], str) and result["answer"].strip():
                ans = result["answer"].strip()
                if (ans.startswith("{") or ans.startswith("[")) and '"content"' in ans:
                    logger.info(f"[DEBUG-EXTRACT] 分支: dict['answer'] 是JSON字符串 → 递归解析")
                    return self._extract_answer(ans)
                logger.info(f"[DEBUG-EXTRACT] 分支: dict['answer'] → {result['answer'][:50]}...")
                return result["answer"]

            if "choices" in result and isinstance(result["choices"], list):
                choices = result["choices"]
                if choices and len(choices) > 0:
                    msg = choices[0].get("message", {})
                    if isinstance(msg, dict):
                        content = msg.get("content", "")
                        if isinstance(content, str) and content.strip():
                            logger.info(f"[DEBUG-EXTRACT] 分支: dict['choices'][0]['message']['content'] → {content[:50]}...")
                            return content
                    delta = choices[0].get("delta", {})
                    if isinstance(delta, dict):
                        content = delta.get("content", "")
                        if isinstance(content, str) and content.strip():
                            logger.info(f"[DEBUG-EXTRACT] 分支: dict['choices'][0]['delta']['content'] → {content[:50]}...")
                            return content

            if "data" in result:
                logger.info(f"[DEBUG-EXTRACT] 分支: dict['data'] → 递归")
                return self._extract_answer(result["data"])

            if "message" in result and isinstance(result["message"], dict):
                logger.info(f"[DEBUG-EXTRACT] 分支: dict['message'] → 递归")
                return self._extract_answer(result["message"])

            for key in ("content", "text", "reply", "response"):
                if key in result and isinstance(result[key], str) and result[key].strip():
                    logger.info(f"[DEBUG-EXTRACT] 分支: dict['{key}'] → {result[key][:50]}...")
                    return result[key]

            logger.info(f"[DEBUG-EXTRACT] 分支: dict所有分支未命中，keys={list(result.keys())}")
            return json.dumps(result, ensure_ascii=False)

        if isinstance(result, list):
            if result:
                logger.info(f"[DEBUG-EXTRACT] 分支: list → 取第一个元素递归")
                return self._extract_answer(result[0])
            logger.info(f"[DEBUG-EXTRACT] 分支: 空list → 返回空")
            return ""

        logger.info(f"[DEBUG-EXTRACT] 分支: 其他类型 → str()")
        return str(result)

    async def _fallback_chat(self, query: str) -> str:
        try:
            if self._ollama_client and HAS_OPENAI:
                resp = self._ollama_client.chat.completions.create(
                    model=config.ACTION_MODEL,
                    messages=[
                        {"role": "system", "content": "你是海宁皮影戏的讲解员，用中文回答用户的问题。"},
                        {"role": "user", "content": query},
                    ],
                    temperature=0.3,
                )
                return resp.choices[0].message.content or "抱歉，我暂时无法回答这个问题。"
            else:
                return "抱歉，知识库暂未检索到相关内容。"
        except Exception as e:
            logger.error(f"回退对话也失败了: {e}")
            return "抱歉，服务暂时不可用，请稍后再试。"

    def _build_action_context(self, user_input: str, text_reply: str) -> str:
        return f"""用户输入：{user_input}
AI 回复：{text_reply}

请根据以上对话内容判断皮影数字人应该执行什么动作。"""

    async def _generate_action(self, context: str) -> str:
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
                logger.error(f"Ollama 动作生成失败: {e}")
                return "动作：[常态]"
        else:
            logger.warning("Ollama 客户端不可用，使用默认动作")
            return "动作：[常态]"

    def _parse_actions(self, response: str) -> List[str]:
        if not response:
            logger.error("动作响应为空")
            default = self.action_mapping.get("常态", "actions/idle")
            return [default]

        raw_actions = re.findall(r"\[([^\]]+?)\]", response)
        if not raw_actions:
            logger.error(f"动作格式解析失败: {response}")
            default = self.action_mapping.get("常态", "actions/idle")
            return [default]

        found = []
        for name in raw_actions:
            path = self.action_mapping.get(name.strip().lower())
            if path:
                found.append(path)
            else:
                logger.warning(f"忽略无效动作: {name}")

        return found[:3] if found else [self.action_mapping.get("常态", "actions/idle")]

    def _clean_text(self, text: str) -> str:
        text = text.strip()
        return text
