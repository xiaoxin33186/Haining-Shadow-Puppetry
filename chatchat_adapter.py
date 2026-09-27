# -*- coding: utf-8 -*-
# [CHATCHAT] LangChain-Chatchat 适配器
# 本文件为新增文件，提供 ChatchatAgentSystem 类 用于生成文本回答

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
            logger.info(f"知识库问答结果: {text_reply}")  
            clean_text = self._clean_text(text_reply)
            return  clean_text

        except Exception as e:
            logger.error(f"Chatchat 处理失败: {e}")

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
                raise Exception("知识库未返回有效答案")  

            return answer

        except Exception as e:
            logger.warning(f"第二级知识库问答失败: {e}")

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

    def _clean_text(self, text: str) -> str:
        if text is None:
            return ""
        text = text.strip()
        return text
