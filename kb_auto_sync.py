# -*- coding: utf-8 -*-
# [KB-SYNC] 严格镜像模式：kb_inbox/ 目录 = Chatchat 知识库唯一真源
#
# 语义：
#   - 目录里有的文件   -> 知识库里必须有（新增/覆盖更新）
#   - 目录里没有的文件 -> 知识库中删除（含 content/ 源文件）
#
# 运行：python kb_auto_sync.py   （常驻后台，与 chatchat / ollama 平级）
#
# 流程：等待 chatchat 就绪 -> 校验知识库存在 -> 种子初始化 -> 全量对账 -> 增量轮询
# 纯标准库实现，无第三方依赖。

import asyncio
import json
import shutil
import time
import logging
from pathlib import Path
from typing import Dict, List, Set

import httpx

import chatchat_config as config
from api_client import ChatchatAPIClient


logger = logging.getLogger("KBSync")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)

DELETE_DELAY = int(getattr(config, "KB_SYNC_DELETE_DELAY", 30) or 30)
DELETE_CONTENT = bool(getattr(config, "KB_SYNC_DELETE_CONTENT", True))
POLL_INTERVAL = int(getattr(config, "KB_SYNC_POLL_INTERVAL", 3) or 3)
UPLOAD_TIMEOUT = int(getattr(config, "KB_SYNC_TIMEOUT", 300) or 300)
KB_NAME = config.KNOWLEDGE_BASE_NAME


# ─────────────────────────── 路径与工具 ───────────────────────────


def _dirs() -> tuple:
    project_root = Path(__file__).resolve().parent
    inbox = (project_root / config.KB_INBOX_DIR).resolve()
    return inbox, inbox / config.KB_SYNC_MANIFEST_NAME


def _kb_content_dir() -> Path:
    project_root = Path(__file__).resolve().parent
    return (
        project_root
        / "Langchain-Chatchat" / "libs" / "chatchat-server"
        / "data" / "knowledge_base" / KB_NAME / "content"
    )


def _supported_exts() -> Set[str]:
    exts = getattr(config, "KB_SYNC_SUPPORTED_EXTS", None)
    if not exts:
        return {".md", ".txt", ".docx", ".doc", ".pdf", ".xlsx", ".xls", ".pptx", ".ppt", ".csv"}
    return {e.lower() if e.startswith(".") else "." + e.lower() for e in exts}


def _fingerprint(path: Path) -> Dict:
    st = path.stat()
    return {"size": st.st_size, "mtime": int(st.st_mtime)}


def _scan_dir(inbox: Path, supported: Set[str]) -> Dict[str, Dict]:
    """扫描目录根下支持的扁平文件，返回 {文件名: 指纹}，排除清单等点号文件。"""
    result = {}
    for f in inbox.iterdir():
        if not f.is_file():
            continue
        if f.name.startswith("."):
            continue
        if f.suffix.lower() in supported:
            result[f.name] = _fingerprint(f)
    return result


def _load_manifest(manifest_path: Path) -> Dict:
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text("utf-8"))
            if isinstance(data, dict) and isinstance(data.get("files"), dict):
                return data
        except Exception as e:
            logger.error(f"清单解析失败，将重建: {e}")
    return {"files": {}}


def _save_manifest(manifest_path: Path, manifest: Dict):
    try:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8"
        )
    except Exception as e:
        logger.error(f"清单保存失败: {e}")


async def _wait_file_stable(path: Path, timeout: float = 30.0) -> bool:
    size = -1
    stable = 0
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            cur = path.stat().st_size
        except OSError:
            return False
        if cur == size and cur > 0:
            stable += 1
        else:
            stable = 0
        size = cur
        if stable >= 2:
            return True
        await asyncio.sleep(1.0)
    return False


# ─────────────────────────── 核心操作 ───────────────────────────


async def _upload(client: ChatchatAPIClient, path: Path, override: bool = True) -> bool:
    if not await _wait_file_stable(path):
        logger.warning(f"文件仍在写入，暂不处理: {path.name}")
        return False
    result = await client.upload_docs(
        kb_name=KB_NAME,
        files=[str(path)],
        override=override,
        chunk_size=getattr(config, "KB_SYNC_CHUNK_SIZE", None),
        chunk_overlap=getattr(config, "KB_SYNC_CHUNK_OVERLAP", None),
        zh_title_enhance=True,
        timeout=UPLOAD_TIMEOUT,
    )
    code = result.get("code", -1)
    failed = (result.get("data") or {}).get("failed_files") or {}
    if code != 200:
        logger.error(f"服务端返回失败: {result.get('msg')}")
        return False
    if failed:
        logger.warning(f"部分文件处理失败: {failed}")
        return False
    return True


async def _delete(client: ChatchatAPIClient, file_names: List[str]) -> bool:
    result = await client.delete_docs(
        KB_NAME, file_names, delete_content=DELETE_CONTENT
    )
    code = result.get("code", -1)
    failed = (result.get("data") or {}).get("failed_files") or {}
    if code != 200:
        logger.error(f"删除服务端返回失败: {result.get('msg')}")
        return False
    if failed:
        logger.warning(f"部分文件删除失败: {failed}")
        return False
    return True


def _ensure_kb(client: ChatchatAPIClient) -> bool:
    kbs = asyncio.run(client.list_knowledge_bases())
    if KB_NAME in kbs:
        return True
    logger.warning(f"知识库 '{KB_NAME}' 不存在，尝试自动创建...")
    try:
        ok = asyncio.run(client.create_knowledge_base(KB_NAME))
    except Exception as e:
        logger.error(f"创建知识库失败: {e}")
        return False
    if ok:
        logger.info(f"知识库 '{KB_NAME}' 创建成功")
        return True
    # 创建接口返回失败时，再核对一次列表（磁盘上可能已存在该库，服务端返回"已存在"）
    kbs = asyncio.run(client.list_knowledge_bases())
    if KB_NAME in kbs:
        logger.info(f"知识库 '{KB_NAME}' 已在服务端可用")
        return True
    logger.error(f"知识库 '{KB_NAME}' 创建失败，请检查配置")
    return False


def _seed(client: ChatchatAPIClient, inbox: Path, supported: Set[str]) -> Set[str]:
    """把知识库现有文件复制进目录作种子，返回种子文件集合。"""
    logger.info("首次运行，开始种子初始化...")
    try:
        existing = asyncio.run(client.list_files())
    except Exception as e:
        logger.error(f"种子初始化失败（无法获取现有文件清单）: {e}")
        return set()

    if not existing:
        logger.info("知识库当前无文件，跳过种子")
        return set()

    content_dir = _kb_content_dir()
    seeded = set()
    for name in existing:
        target = inbox / name
        if target.exists():
            seeded.add(name)
            continue
        try:
            if content_dir.exists() and (content_dir / name).is_file():
                shutil.copy2(content_dir / name, target)
                logger.info(f"[种子] 复制: {name}")
            else:
                # content 目录不可用时，走 API 下载兜底
                asyncio.run(client.download_doc(KB_NAME, name, str(target)))
                logger.info(f"[种子] 下载: {name}")
            seeded.add(name)
        except Exception as e:
            logger.error(f"[种子] 文件失败: {name} -> {e}")
    return seeded


async def _reconcile_uploads(
    client: ChatchatAPIClient,
    D: Dict[str, Dict],
    L: Set[str],
    files: Dict,
    inbox: Path,
):
    for name, fp in D.items():
        path = inbox / name
        recorded = files.get(name)
        if name not in L:
            # 新增
            if await _upload(client, path, override=True):
                files[name] = {**fp, "synced": True}
                logger.info(f"[入库] {name}")
            else:
                logger.error(f"[入库失败] {name}（下次轮询重试）")
        elif recorded and (
            recorded.get("size") != fp["size"] or recorded.get("mtime") != fp["mtime"]
        ):
            # 内容变化 -> 覆盖更新（override=True 保证向量化的是新内容）
            if await _upload(client, path, override=True):
                files[name] = {**fp, "synced": True}
                logger.info(f"[更新] {name}")
            else:
                logger.error(f"[更新失败] {name}（下次轮询重试）")
        elif not recorded:
            # 目录里新出现、库里已存在（如种子文件）：登记指纹即可，不重复上传
            files[name] = {**fp, "synced": True}


async def _reconcile_deletes(
    client: ChatchatAPIClient,
    D: Dict[str, Dict],
    L: Set[str],
    files: Dict,
    pending_delete: Dict[str, float],
):
    now = time.time()
    for name in L:
        if name in D:
            pending_delete.pop(name, None)
            continue
        # 目录里没有 -> 进入删除防抖
        ts = pending_delete.get(name)
        if ts is None:
            pending_delete[name] = now
            logger.warning(f"[待删除] {name} 已从目录消失，{DELETE_DELAY}s 内未恢复将删除")
        elif now - ts >= DELETE_DELAY:
            if await _delete(client, [name]):
                files.pop(name, None)
                pending_delete.pop(name, None)
                logger.info(f"[已删除] {name}")
            else:
                pending_delete.pop(name, None)  # 失败则重新计时，稍后重试
                logger.error(f"[删除失败] {name}（稍后重试）")


async def _cycle(
    client: ChatchatAPIClient,
    inbox: Path,
    manifest_path: Path,
    manifest: Dict,
    supported: Set[str],
    pending_delete: Dict[str, float],
):
    files = manifest.setdefault("files", {})
    D = _scan_dir(inbox, supported)
    L = set(await client.list_files())

    await _reconcile_uploads(client, D, L, files, inbox)
    await _reconcile_deletes(client, D, L, files, pending_delete)

    _save_manifest(manifest_path, manifest)


def main():
    inbox, manifest_path = _dirs()
    inbox.mkdir(parents=True, exist_ok=True)
    supported = _supported_exts()
    manifest = _load_manifest(manifest_path)
    pending_delete: Dict[str, float] = {}

    logger.info(f"[KB-Sync] ======================================")
    logger.info(f"[KB-Sync] 严格镜像模式已启动")
    logger.info(f"[KB-Sync] 真源目录: {inbox}")
    logger.info(f"[KB-Sync] 目标知识库: {KB_NAME} @ {config.BASE_URL}")
    logger.info(f"[KB-Sync] 轮询间隔: {POLL_INTERVAL}s | 删除防抖: {DELETE_DELAY}s")
    logger.info(f"[KB-Sync] 支持类型: {', '.join(sorted(supported))}")
    logger.info(f"[KB-Sync] 目录即知识库：增/删/改目录文件即增/删/改知识库")
    logger.info(f"[KB-Sync] ======================================")

    client = ChatchatAPIClient()

    # 等待 chatchat 就绪并校验知识库
    ready = False
    while not ready:
        try:
            ready = _ensure_kb(client)
            if ready:
                logger.info(f"知识库校验通过: {KB_NAME}")
        except httpx.HTTPError as e:
            logger.error(f"Chatchat 服务不可达，等待重试... ({e})")
        except Exception as e:
            logger.error(f"校验知识库异常: {e}")
        if not ready:
            time.sleep(POLL_INTERVAL)

    # 种子：清单为空且目录为空时，把知识库现有文件复制进目录作基线
    if not manifest.get("files") and not _scan_dir(inbox, supported):
        _seed(client, inbox, supported)

    while True:
        try:
            asyncio.run(_cycle(client, inbox, manifest_path, manifest, supported, pending_delete))
        except httpx.HTTPError as e:
            logger.error(f"Chatchat 服务不可达，跳过本轮（不执行任何增删）: {e}")
        except KeyboardInterrupt:
            logger.info("[KB-Sync] 已停止。")
            break
        except Exception as e:
            logger.error(f"[KB-Sync] 同步异常: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
