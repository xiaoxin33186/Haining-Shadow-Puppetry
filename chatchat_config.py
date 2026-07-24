# -*- coding: utf-8 -*-
# [CHATCHAT] LangChain-Chatchat 后端对接配置
# 本文件为新增文件，用于配置与 LangChain-Chatchat 后端通信的相关参数

BASE_URL = "http://127.0.0.1:7861"

KNOWLEDGE_BASE_NAME = "haining_puppetry"

LLM_MODEL = "gemma3:4b"

EMBEDDING_MODEL = "nomic-embed-text"

REQUEST_TIMEOUT = 60

OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1/"
OLLAMA_API_KEY = "ollama"
ACTION_MODEL = "gemma3:4b"

USE_STREAM = False

ENABLE_SOURCE_DOCUMENTS = False
