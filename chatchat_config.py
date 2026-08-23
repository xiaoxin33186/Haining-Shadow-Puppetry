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

# [KB-SYNC] 知识库自动同步（投放目录监听）配置
# 把文件放进 KB_INBOX_DIR 目录，kb_auto_sync.py 会自动上传并向量化到 KNOWLEDGE_BASE_NAME
KB_INBOX_DIR = "kb_inbox"
# 轮询投放目录的间隔（秒）
KB_SYNC_POLL_INTERVAL = 3
# 非网络类失败的最大重试次数，超过后文件移入 kb_inbox/failed/
KB_SYNC_MAX_RETRIES = 3
# 是否覆盖知识库中已有的同名文件（镜像模式下目录为真源，统一为 True，同名=覆盖更新）
KB_SYNC_OVERRIDE = True
# 上传+向量化超时（秒），大文件需适当调大
KB_SYNC_TIMEOUT = 300
# 切块参数，None 表示使用 Chatchat 服务端默认值（kb_settings.yaml）
KB_SYNC_CHUNK_SIZE = None
KB_SYNC_CHUNK_OVERLAP = None
# 支持入库的文件类型
KB_SYNC_SUPPORTED_EXTS = [
    ".md", ".txt", ".docx", ".doc", ".pdf",
    ".xlsx", ".xls", ".pptx", ".ppt", ".csv",
]

# [KB-SYNC-MIRROR] 严格镜像模式配置（kb_inbox/ 目录 = 知识库唯一真源）
# 首次运行是否自动把知识库 content/ 里的现有文件复制进 kb_inbox/ 作种子
KB_SYNC_SEED = True
# 某文件从目录消失后，观察多少秒才真正执行删除（防编辑软件临时改名的误判）
KB_SYNC_DELETE_DELAY = 30
# 删除时是否连同 content/ 源文件一起删除
KB_SYNC_DELETE_CONTENT = True
# 镜像状态清单文件名（记录已同步文件的指纹）
KB_SYNC_MANIFEST_NAME = ".manifest.json"
