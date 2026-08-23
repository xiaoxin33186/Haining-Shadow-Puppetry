# -*- coding: utf-8 -*-
# [CHATCHAT] LangChain-Chatchat HTTP API 客户端封装
# 本文件为新增文件，封装与 LangChain-Chatchat 后端通信的所有 API 调用

import json
import httpx
from pathlib import Path
from typing import List, Dict, Optional, Any
import logging

import chatchat_config as config


logger = logging.getLogger("ChatchatAPI")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)


class ChatchatAPIClient:
    def __init__(self, base_url: str = None, timeout: int = None):
        self.base_url = base_url or config.BASE_URL
        self.timeout = timeout or config.REQUEST_TIMEOUT
        logger.info(f"Chatchat API 客户端初始化，服务地址: {self.base_url}")

    async def close(self):
        pass

    async def _get(self, path: str, params: Dict = None) -> Dict:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params or {})
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"GET {url} 失败: {e}")
            raise

    async def _post(self, path: str, data: Dict = None) -> Any:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=data or {})
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if "application/json" in content_type:
                    return resp.json()
                text = resp.text
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    return text
        except Exception as e:
            logger.error(f"POST {url} 失败: {e}")
            raise

    async def list_knowledge_bases(self) -> List[str]:
        result = await self._get("/knowledge_base/list_knowledge_bases")
        data = result.get("data", [])
        if isinstance(data, list):
            names = []
            for item in data:
                if isinstance(item, dict) and item.get("kb_name"):
                    names.append(item["kb_name"])
            return names
        return []

    async def list_files(self, kb_name: str = None) -> List[str]:
        kb_name = kb_name or config.KNOWLEDGE_BASE_NAME
        params = {"knowledge_base_name": kb_name}
        result = await self._get("/knowledge_base/list_files", params=params)
        data = result.get("data", [])
        if not isinstance(data, list):
            return []
        names = []
        for item in data:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict) and item.get("file_name"):
                names.append(item["file_name"])
        return names

    async def delete_docs(
        self,
        kb_name: str,
        file_names: List[str],
        delete_content: bool = True,
        not_refresh_vs_cache: bool = False,
    ) -> Dict:
        data = {
            "knowledge_base_name": kb_name,
            "file_names": file_names,
            "delete_content": delete_content,
            "not_refresh_vs_cache": not_refresh_vs_cache,
        }
        return await self._post("/knowledge_base/delete_docs", data)

    async def download_doc(self, kb_name: str, file_name: str, save_to: str) -> str:
        kb_name = kb_name or config.KNOWLEDGE_BASE_NAME
        url = f"{self.base_url}/knowledge_base/download_doc"
        params = {"knowledge_base_name": kb_name, "file_name": file_name, "preview": False}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            Path(save_to).write_bytes(resp.content)
        return save_to

    async def create_knowledge_base(
        self,
        kb_name: str,
        vector_store_type: str = "faiss",
        embed_model: str = None,
    ) -> bool:
        data = {
            "knowledge_base_name": kb_name,
            "vector_store_type": vector_store_type,
            "embed_model": embed_model or config.EMBEDDING_MODEL,
        }
        result = await self._post("/knowledge_base/create_knowledge_base", data)
        return result.get("code", -1) == 200

    async def upload_docs(
        self,
        kb_name: str,
        files: List[str],
        override: bool = False,
        to_vector_store: bool = True,
        chunk_size: int = None,
        chunk_overlap: int = None,
        zh_title_enhance: bool = True,
        docs: Dict = None,
        not_refresh_vs_cache: bool = False,
        timeout: int = None,
    ) -> Dict:
        """上传文件到知识库并向量化。

        Chatchat 服务端的 /knowledge_base/upload_docs 是 multipart 表单接口
        （files=文件字节 + 表单字段），不是 JSON。这里按真实接口发送。
        """
        url = f"{self.base_url}/knowledge_base/upload_docs"
        form_data = {
            "knowledge_base_name": kb_name,
            "override": str(override).lower(),
            "to_vector_store": str(to_vector_store).lower(),
            "zh_title_enhance": str(zh_title_enhance).lower(),
            "docs": json.dumps(docs or {}, ensure_ascii=False),
            "not_refresh_vs_cache": str(not_refresh_vs_cache).lower(),
        }
        if chunk_size is not None:
            form_data["chunk_size"] = str(chunk_size)
        if chunk_overlap is not None:
            form_data["chunk_overlap"] = str(chunk_overlap)

        upload_files = []
        for path in files:
            p = Path(path)
            upload_files.append(("files", (p.name, p.read_bytes())))

        timeout = timeout or self.timeout
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, data=form_data, files=upload_files)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"POST {url} 失败: {e}")
            raise

    async def knowledge_base_chat(
        self,
        query: str,
        kb_name: str = None,
        history: List[Dict] = None,
        stream: bool = None,
        model: str = None,
        temperature: float = 0.7,
        top_k: int = 3,
        score_threshold: float = 1.0,
    ) -> Dict:
        kb_name = kb_name or config.KNOWLEDGE_BASE_NAME
        stream = stream if stream is not None else config.USE_STREAM

        data = {
            "query": query,
            "mode": "local_kb",
            "kb_name": kb_name,
            "history": history or [],
            "stream": stream,
            "model": model or config.LLM_MODEL,
            "temperature": temperature,
            "max_tokens": None,
            "prompt_name": "default",
            "top_k": top_k,
            "score_threshold": score_threshold,
        }

        if stream:
            return await self._knowledge_base_chat_stream(data)
        else:
            result = await self._post("/chat/kb_chat", data)
            return self._normalize_kb_chat_response(result)

    async def _knowledge_base_chat_stream(self, data: Dict) -> Dict:
        url = f"{self.base_url}/chat/kb_chat"
        full_answer = ""
        source_documents = []

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", url, json=data) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("data:"):
                            json_str = line[5:].strip()
                            if json_str == "[DONE]":
                                break
                            try:
                                chunk = json.loads(json_str)
                                if "answer" in chunk:
                                    full_answer += chunk["answer"]
                                if "docs" in chunk and chunk["docs"]:
                                    source_documents = chunk["docs"]
                            except json.JSONDecodeError:
                                continue
        except Exception as e:
            logger.error(f"流式问答请求失败: {e}")
            raise

        return {
            "answer": full_answer,
            "source_documents": source_documents,
        }

    async def chat(
        self,
        query: str,
        history: List[Dict] = None,
        model: str = None,
        temperature: float = 0.7,
        stream: bool = None,
    ) -> Dict:
        stream = stream if stream is not None else config.USE_STREAM
        model = model or config.LLM_MODEL
        messages = []
        if history:
            for h in history:
                messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
        messages.append({"role": "user", "content": query})

        data = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "temperature": temperature,
        }

        result = await self._post("/chat/chat/completions", data)
        return self._normalize_chat_response(result)

    def _normalize_kb_chat_response(self, raw: Any) -> Dict:
        if isinstance(raw, str):
            return {"answer": raw, "source_documents": []}
        if not isinstance(raw, dict):
            return {"answer": str(raw), "source_documents": []}
        if "choices" in raw and isinstance(raw.get("choices"), list):
            choices = raw["choices"]
            if choices and len(choices) > 0:
                msg = choices[0].get("message", {})
                content = msg.get("content", "")
                docs = raw.get("docs", raw.get("source_documents", []))
                return {"answer": content, "source_documents": docs}
        if "answer" in raw:
            return raw
        if "data" in raw and isinstance(raw["data"], dict):
            d = raw["data"]
            if "answer" in d:
                return {
                    "answer": d.get("answer", ""),
                    "source_documents": d.get("docs", d.get("source_documents", [])),
                }
            if "choices" in d and isinstance(d.get("choices"), list):
                choices = d["choices"]
                if choices and len(choices) > 0:
                    msg = choices[0].get("message", {})
                    content = msg.get("content", "")
                    return {"answer": content, "source_documents": d.get("docs", [])}
        logger.warning(f"无法识别的知识库响应格式: {list(raw.keys()) if isinstance(raw, dict) else type(raw)}")
        return {"answer": str(raw), "source_documents": []}

    def _normalize_chat_response(self, raw: Dict) -> Dict:
        if "choices" in raw:
            choices = raw["choices"]
            if choices and len(choices) > 0:
                msg = choices[0].get("message", {})
                content = msg.get("content", "")
                return {"answer": content}
        if "answer" in raw:
            return raw
        if "data" in raw and isinstance(raw["data"], dict):
            d = raw["data"]
            return {"answer": d.get("answer", "")}
        return {"answer": str(raw)}

    async def search_docs(
        self,
        query: str,
        kb_name: str = None,
        top_k: int = 3,
        score_threshold: float = 1.0,
    ) -> List[Dict]:
        kb_name = kb_name or config.KNOWLEDGE_BASE_NAME
        data = {
            "query": query,
            "knowledge_base_name": kb_name,
            "top_k": top_k,
            "score_threshold": score_threshold,
        }
        result = await self._post("/knowledge_base/search_docs", data)
        return result.get("data", [])
