# -*- coding: utf-8 -*-
"""
视频播放系统 VideoPlayerSystem
- 点击"视频"按钮时申请网络：无网络 -> 提示"请连接网络"
- 有网络 -> 访问 shadow.caa.edu.cn 抓取"海宁"相关视频标题，存本地缓存
- "目录"命令：列出所有已获取的视频标题
- "播放+（名称）"命令：模糊匹配并播放
- 视频在 pygame 主界面内嵌显示，皮影人站在两侧
- 支持暂停 / 快进 / 快退 / 进度条 / 右上角退出
- 下载：后台线程 + 左上角小进度条 + 终端进度行（不依赖 tqdm）
"""

import json
import time
import hashlib
import tempfile
import urllib.request
import sys
import threading
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False


BASE_URL = "https://shadow.caa.edu.cn"
API_URL = f"{BASE_URL}/api/programme.json"
CACHE_FILE = Path("video_cache.json")
TMP_DIR = Path(tempfile.gettempdir()) / "shadow_videos"
DEFAULT_KEYWORD = "海宁"
PAGE_SIZE = 12
NETWORK_TIMEOUT = 5


# ---------------- 数据类 ----------------

@dataclass
class VideoItem:
    id: str
    title: str
    path: str
    region: str = ""

    @property
    def url(self) -> str:
        if self.path.startswith("http"):
            return self.path
        p = self.path if self.path.startswith("/") else "/" + self.path
        return f"{BASE_URL}{p}"


@dataclass
class CurrentVideo:
    title: str
    url: str = ""
    video_id: str = ""


# ---------------- 网络检查 ----------------

def is_online() -> bool:
    try:
        if HAS_REQUESTS:
            r = requests.get(BASE_URL, timeout=NETWORK_TIMEOUT,
                             headers={"User-Agent": "Mozilla/5.0"})
            return r.status_code < 500
        req = urllib.request.Request(BASE_URL, headers={"User-Agent": "Mozilla/5.0"})
        urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT)
        return True
    except Exception as e:
        print(f"[网络] 检查失败: {e}")
        return False


# ---------------- 视频抓取 ----------------

class VideoFetcher:
    def __init__(self):
        self.session = None
        if HAS_REQUESTS:
            self.session = requests.Session()
            self.session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": f"{BASE_URL}/",
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded",
            })
            try:
                self.session.get(BASE_URL, timeout=NETWORK_TIMEOUT)
                print("[网络] 会话建立成功")
            except Exception as e:
                print(f"[网络] 建立会话失败: {e}")

    def fetch_all(self, keyword: str = DEFAULT_KEYWORD) -> List[VideoItem]:
        if self.session is None:
            print("[抓取] requests 不可用")
            return []
        results: List[VideoItem] = []
        page = 0
        region_param = f"*{keyword}*"
        while page < 50:
            params = [
                ("order-by", "sort"),
                ("order-by", "id"),
                ("page", page),
                ("page-size", PAGE_SIZE),
                ("hidden", "false"),
                ("unpaged", "false"),
                ("region", region_param),
                ("_", int(time.time() * 1000)),
            ]
            try:
                r = self.session.get(API_URL, params=params, timeout=15)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                print(f"[抓取] 第 {page} 页失败: {e}")
                break
            if page == 0:
                try:
                    print(f"[抓取] 原始返回片段: {json.dumps(data, ensure_ascii=False)[:600]}")
                except Exception:
                    pass
            items = self._extract_items(data)
            if not items:
                break
            for item in items:
                title = item.get("title") or item.get("name") or item.get("programmeName") or ""
                path = (item.get("path") or item.get("videoPath") or item.get("url")
                        or item.get("filePath") or item.get("video") or "")
                if title:
                    results.append(VideoItem(
                        id=str(item.get("id", "")),
                        title=title,
                        path=path,
                        region=item.get("region") or item.get("area") or "",
                    ))
            print(f"[抓取] 第 {page} 页 {len(items)} 条，累计 {len(results)} 条")
            total = self._extract_total(data)
            if total is not None and len(results) >= total:
                break
            if len(items) < PAGE_SIZE:
                break
            page += 1
        return results

    def _extract_items(self, data: Any) -> List[Dict]:
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return []
        for key in ("content", "list", "rows", "items", "records"):
            if isinstance(data.get(key), list):
                return data[key]
        inner = data.get("data")
        if isinstance(inner, list):
            return inner
        if isinstance(inner, dict):
            for key in ("content", "list", "rows", "items", "records"):
                if isinstance(inner.get(key), list):
                    return inner[key]
        return []

    def _extract_total(self, data: Any) -> Optional[int]:
        if not isinstance(data, dict):
            return None
        for key in ("totalElements", "total", "count"):
            if key in data:
                try:
                    return int(data[key])
                except (TypeError, ValueError):
                    pass
        inner = data.get("data")
        if isinstance(inner, dict):
            for key in ("totalElements", "total", "count"):
                if key in inner:
                    try:
                        return int(inner[key])
                    except (TypeError, ValueError):
                        pass
        return None


# ---------------- 视频播放器（OpenCV + pygame，异步下载） ----------------

class VideoPlayer:
    def __init__(self, action_queue, fps: int = 45):
        self.action_queue = action_queue
        self.main_fps = fps
        self.cap = None
        self.is_playing = False
        self.is_paused = False
        self.current_video: Optional[CurrentVideo] = None
        self.video_fps = 25.0
        self.total_frames = 0
        self.current_frame = 0
        self.frame_interval = 1.0 / self.video_fps
        self._last_frame_time = 0.0
        self._current_rgb = None
        self._current_surface = None
        self.video_width = 0
        self.video_height = 0

        # ===== 异步下载 =====
        self.is_downloading = False
        self.download_progress = 0.0
        self.download_done_bytes = 0
        self.download_total_bytes = 0
        self._download_lock = threading.Lock()
        self._download_thread: Optional[threading.Thread] = None
        self._pending_video: Optional[CurrentVideo] = None
        self._downloaded_path: Optional[Path] = None
        self._download_error: Optional[str] = None
        self._just_finished_flag = False

    # ---------- 异步加载 ----------
    def load_async(self, video: CurrentVideo) -> bool:
        if not HAS_CV2:
            print("[播放器] 未安装 opencv-python")
            self.action_queue.put((["idle"], "未安装 opencv，无法播放视频"))
            return False

        self.stop()
        self.current_video = video
        print(f"[播放器] 异步加载: {video.title} -> {video.url}")

        with self._download_lock:
            self.download_progress = 0.0
            self.download_done_bytes = 0
            self.download_total_bytes = 0
            self._downloaded_path = None
            self._download_error = None

        self._pending_video = video
        self.is_downloading = True
        self._just_finished_flag = False

        self._download_thread = threading.Thread(
            target=self._download_worker,
            args=(video.url,),
            daemon=True,
        )
        self._download_thread.start()
        return True

    def _download_worker(self, url: str):
        try:
            TMP_DIR.mkdir(parents=True, exist_ok=True)
            name = hashlib.md5(url.encode()).hexdigest()[:12] + ".mp4"
            path = TMP_DIR / name

            if path.exists() and path.stat().st_size > 1024:
                print(f"[播放器] 复用缓存: {path}")
                with self._download_lock:
                    self.download_progress = 1.0
                    self.download_done_bytes = path.stat().st_size
                    self.download_total_bytes = path.stat().st_size
                    self._downloaded_path = path
                return

            print(f"[播放器] 下载: {url}")
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": f"{BASE_URL}/",
                "Accept": "*/*",
                "X-Requested-With": "XMLHttpRequest",
            }
            downloaded = 0
            total = 0

            if HAS_REQUESTS:
                with requests.get(url, headers=headers, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    total = int(r.headers.get("content-length", 0))
                    with open(path, "wb") as f:
                        for chunk in r.iter_content(1024 * 256):
                            if not chunk:
                                continue
                            f.write(chunk)
                            downloaded += len(chunk)
                            with self._download_lock:
                                self.download_done_bytes = downloaded
                                self.download_total_bytes = total
                                self.download_progress = (downloaded / total) if total > 0 else 0.0
            else:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
                    total = int(r.headers.get("Content-Length", 0))
                    while True:
                        chunk = r.read(1024 * 256)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        with self._download_lock:
                            self.download_done_bytes = downloaded
                            self.download_total_bytes = total
                            self.download_progress = (downloaded / total) if total > 0 else 0.0

            with self._download_lock:
                self.download_progress = 1.0
                self._downloaded_path = path

            mb = path.stat().st_size / 1024 / 1024
            print(f"\n[播放器] 下载完成: {path} ({mb:.1f}MB)")
        except Exception as e:
            with self._download_lock:
                self._download_error = str(e)
            print(f"\n[播放器] 下载失败: {e}")

    # ---------- 主循环每帧调用 ----------
    def update(self):
        if self.is_downloading:
            t = self._download_thread
            if t is not None and not t.is_alive():
                self.is_downloading = False
                with self._download_lock:
                    err = self._download_error
                    path = self._downloaded_path
                if err:
                    print(f"[播放器] 下载出错: {err}")
                    return
                if path is not None:
                    if self._open_local_video(path):
                        self._just_finished_flag = True
            return

        if not self.is_playing or self.is_paused or self.cap is None:
            return
        now = time.time()
        if now - self._last_frame_time < self.frame_interval:
            return
        self._last_frame_time = now
        self._read_next_frame()

    def consume_just_finished(self) -> bool:
        if self._just_finished_flag:
            self._just_finished_flag = False
            return True
        return False

    def _open_local_video(self, path: Path) -> bool:
        cap = self._try_open(str(path))
        if cap is None:
            print("[播放器] 打开本地文件失败")
            return False
        self.cap = cap
        self.video_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        if self.video_fps <= 0 or self.video_fps > 120:
            self.video_fps = 25.0
        self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        self.video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        self.current_frame = 0
        self.frame_interval = 1.0 / self.video_fps
        self._last_frame_time = time.time()
        self.is_playing = True
        self.is_paused = False
        print(f"[播放器] {self.video_width}x{self.video_height}@{self.video_fps:.1f}fps "
              f"{self.total_frames}帧")
        self._read_next_frame()
        return self.has_video

    def _try_open(self, source):
        try:
            cap = cv2.VideoCapture(source)
            if cap and cap.isOpened():
                ok, _ = cap.read()
                if ok:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    return cap
                cap.release()
        except Exception as e:
            print(f"[播放器] 打开失败: {e}")
        return None

    def _read_next_frame(self) -> bool:
        if self.cap is None:
            return False
        try:
            ok, frame = self.cap.read()
            if not ok:
                self.is_playing = False
                return False
            self._current_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w = self._current_rgb.shape[:2]
            self.video_width, self.video_height = w, h
            self._current_surface = None
            self.current_frame += 1
            return True
        except Exception as e:
            print(f"[播放器] 读帧失败: {e}")
            self.is_playing = False
            return False

    def get_frame(self):
        if self._current_rgb is None or not HAS_PYGAME or not HAS_NUMPY:
            return None
        if self._current_surface is None:
            try:
                rgb = np.ascontiguousarray(self._current_rgb)
                h, w = rgb.shape[:2]
                self._current_surface = pygame.image.frombuffer(
                    rgb.tobytes(), (w, h), "RGB"
                ).copy()
            except Exception as e:
                print(f"[播放器] 帧转换失败: {e}")
                return None
        return self._current_surface

    def play(self):
        if self.cap is not None:
            self.is_playing = True
            self.is_paused = False
            self._last_frame_time = time.time()

    def pause(self):
        if self.is_playing:
            self.is_paused = True

    def toggle_pause(self):
        if self.is_paused:
            self.play()
        elif self.is_playing:
            self.pause()

    def stop(self):
        self.is_downloading = False
        self._download_thread = None
        self._pending_video = None
        with self._download_lock:
            self._downloaded_path = None
            self._download_error = None
            self.download_progress = 0.0

        self.is_playing = False
        self.is_paused = False
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
        self._current_rgb = None
        self._current_surface = None
        self.current_video = None
        self.current_frame = 0
        self.total_frames = 0

    def seek_seconds(self, delta: float):
        if self.cap is None or self.total_frames <= 0:
            return
        target = self.current_frame + int(delta * self.video_fps)
        target = max(0, min(self.total_frames - 1, target))
        try:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, target)
            self.current_frame = target
            self._read_next_frame()
            self._last_frame_time = time.time()
        except Exception as e:
            print(f"[播放器] 跳转失败: {e}")

    def seek_ratio(self, ratio: float):
        if self.cap is None or self.total_frames <= 0:
            return
        ratio = max(0.0, min(1.0, ratio))
        target = int(ratio * self.total_frames)
        try:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, target)
            self.current_frame = target
            self._read_next_frame()
            self._last_frame_time = time.time()
        except Exception:
            pass

    def get_progress(self) -> float:
        if self.total_frames <= 0:
            return 0.0
        return min(1.0, self.current_frame / self.total_frames)

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_playing": self.is_playing,
            "is_paused": self.is_paused,
            "progress": self.get_progress(),
            "current_frame": self.current_frame,
            "total_frames": self.total_frames,
            "video_fps": self.video_fps,
        }

    @property
    def has_video(self) -> bool:
        return self.cap is not None and self._current_rgb is not None


# ---------------- 主类：VideoPlayerSystem ----------------

class VideoPlayerSystem:
    VIDEO_AREA = {"cx": 0.5, "cy": 0.46, "max_w": 0.58, "max_h": 0.52}

    def __init__(self, fetcher: VideoFetcher, player: VideoPlayer):
        self.fetcher = fetcher
        self.player = player
        self.videos: List[VideoItem] = []
        self.current_video: Optional[CurrentVideo] = None
        self._keyword = DEFAULT_KEYWORD
        self._network_checked = False
        self._network_ok = False

        self._status_text: str = ""
        self._status_timer: int = 0
        self._status_is_error: bool = False

        self._video_rect = None
        self._progress_rect = None
        self._btn_pause = None
        self._btn_rewind = None
        self._btn_forward = None
        self._btn_exit = None

    def _set_status(self, text: str, seconds: int = 90, is_error: bool = False):
        self._status_text = text or ""
        self._status_timer = int(seconds * 45)
        self._status_is_error = is_error

    def initialize_network(self) -> Tuple[bool, str]:
        if not is_online():
            self._network_checked = True
            self._network_ok = False
            return False, "请连接网络"
        self._network_checked = True
        self._network_ok = True

        if not self.videos:
            cached = self._load_cache()
            if cached:
                self.videos = cached
                print(f"[视频库] 从缓存加载 {len(cached)} 条")
                return True, f"已从缓存加载 {len(cached)} 条视频"
            print(f"[视频库] 开始抓取 keyword='{self._keyword}'...")
            videos = self.fetcher.fetch_all(self._keyword)
            if not videos:
                return False, "未获取到视频，请检查网络"
            self.videos = videos
            self._save_cache(videos)
            return True, f"已获取 {len(videos)} 条视频"
        return True, f"视频库已就绪（{len(self.videos)} 条）"

    def refresh_library(self) -> str:
        if not is_online():
            return "请连接网络"
        videos = self.fetcher.fetch_all(self._keyword)
        if not videos:
            return "未获取到视频"
        self.videos = videos
        self._save_cache(videos)
        return f"已刷新 {len(videos)} 条视频"

    def _load_cache(self) -> List[VideoItem]:
        if not CACHE_FILE.exists():
            return []
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            items = data.get("videos", []) if isinstance(data, dict) else data
            return [VideoItem(
                id=str(v.get("id", "")),
                title=v.get("title", ""),
                path=v.get("path", ""),
                region=v.get("region", ""),
            ) for v in items if v.get("title")]
        except Exception as e:
            print(f"[缓存] 加载失败: {e}")
            return []

    def _save_cache(self, videos: List[VideoItem]):
        try:
            CACHE_FILE.write_text(
                json.dumps({
                    "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "keyword": self._keyword,
                    "videos": [
                        {"id": v.id, "title": v.title, "path": v.path, "region": v.region}
                        for v in videos
                    ],
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"[缓存] 已保存 {len(videos)} 条到 {CACHE_FILE}")
        except Exception as e:
            print(f"[缓存] 保存失败: {e}")

    def search(self, query: str) -> List[VideoItem]:
        q = query.strip().lower()
        if not q:
            return self.videos
        results = [v for v in self.videos if q in v.title.lower()]
        if not results:
            q2 = q.replace("海宁", "")
            if q2:
                results = [v for v in self.videos if q2 in v.title.lower()]
        return results

    def list_plays(self) -> str:
        if not self.videos:
            return "📭 视频库为空，请先点击模式按钮联网获取视频列表"
        lines = [f"📚 共 {len(self.videos)} 条皮影视频："]
        for i, v in enumerate(self.videos, 1):
            lines.append(f"{i}. {v.title}")
        return "\n".join(lines)

    def load_and_play(self, play_name: str):
        if not self.videos:
            self.player.action_queue.put((["idle"], "请先点击视频按钮联网获取视频列表"))
            return
        name = play_name.strip()
        if not name:
            self.player.action_queue.put((["idle"], "请指定视频名称，例如：播放 闹龙宫"))
            return
        results = self.search(name)
        if not results:
            self.player.action_queue.put((["idle"], f"未找到与“{name}”匹配的视频"))
            return
        target = results[0]
        self.current_video = CurrentVideo(title=target.title, url=target.url, video_id=target.id)
        print(f"[视频] 准备播放: {target.title} -> {target.url}")

        ok = self.player.load_async(self.current_video)
        if not ok:
            self._set_status("视频系统不可用", seconds=8, is_error=True)
            self.player.action_queue.put((["idle"], "⚠️ 视频播放器初始化失败"))
            self.current_video = None

    # 供 main.py 判断：下载中不允许关闭视频模式
    def is_busy_downloading(self) -> bool:
        return bool(getattr(self.player, "is_downloading", False))

    @property
    def current_script(self):
        return self.current_video

    def play(self):
        self.player.play()

    def pause(self):
        self.player.pause()

    def stop(self):
        self.player.stop()
        self.current_video = None
        self._status_text = ""
        self._status_timer = 0

    def get_status(self):
        return self.player.get_status()

    # ==== 渲染 ====

    def render(self, screen, w: int, h: int, main_font, status_font, ui_colors: Dict) -> bool:
        self.player.update()

        if self.player.consume_just_finished() and self.current_video:
            self.player.action_queue.put((["idle"], f"🎬 正在播放《{self.current_video.title}》"))

        # -------- 下载中：左上角小进度条，其它 UI 照常绘制 --------
        if self.player.is_downloading:
            self._draw_download_corner(screen, w, h, main_font, status_font, ui_colors)
            return False

        # -------- 没有视频 --------
        if not self.player.has_video:
            self._video_rect = None
            self._progress_rect = None
            if self._status_timer > 0 and self._status_text:
                self._status_timer -= 1
                self._draw_status_overlay(screen, w, h, main_font, ui_colors)
            return False

        vsurf = self.player.get_frame()
        if vsurf is None:
            return False

        vw, vh = vsurf.get_size()
        max_w = int(w * self.VIDEO_AREA["max_w"])
        max_h = int(h * self.VIDEO_AREA["max_h"])
        scale = min(max_w / vw, max_h / vh, 1.0)
        new_w, new_h = max(1, int(vw * scale)), max(1, int(vh * scale))
        if scale < 1.0:
            vsurf = pygame.transform.smoothscale(vsurf, (new_w, new_h))
        vw, vh = new_w, new_h

        vx = int(w * self.VIDEO_AREA["cx"] - vw / 2)
        vy = int(h * self.VIDEO_AREA["cy"] - vh / 2)
        self._video_rect = pygame.Rect(vx, vy, vw, vh)

        shade = pygame.Surface((vw + 8, vh + 8), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 100))
        screen.blit(shade, (vx - 4, vy - 4))
        screen.blit(vsurf, (vx, vy))
        pygame.draw.rect(screen, ui_colors["gold"], (vx, vy, vw, vh), 3)

        pb_x, pb_y, pb_w, pb_h = vx, vy + vh + 12, vw, 8
        self._progress_rect = pygame.Rect(pb_x, pb_y, pb_w, pb_h)
        pygame.draw.rect(screen, (60, 60, 60), self._progress_rect, border_radius=4)
        prog = self.player.get_progress()
        if prog > 0:
            pygame.draw.rect(screen, ui_colors["gold"],
                             (pb_x, pb_y, max(2, int(pb_w * prog)), pb_h),
                             border_radius=4)

        cur_t = self.player.current_frame / max(1e-6, self.player.video_fps)
        total_t = self.player.total_frames / max(1e-6, self.player.video_fps)
        time_text = f"{self._fmt_time(cur_t)} / {self._fmt_time(total_t)}"
        status_font.render_to(screen, (pb_x, pb_y + pb_h + 6), time_text, ui_colors["text"])

        btn_y = pb_y + pb_h + 30
        btn_size, gap = 44, 20
        total_w = btn_size * 4 + gap * 3
        start_x = vx + (vw - total_w) // 2

        self._btn_rewind = pygame.Rect(start_x, btn_y, btn_size, btn_size)
        self._btn_pause = pygame.Rect(start_x + btn_size + gap, btn_y, btn_size, btn_size)
        self._btn_forward = pygame.Rect(start_x + (btn_size + gap) * 2, btn_y, btn_size, btn_size)
        self._btn_exit = pygame.Rect(start_x + (btn_size + gap) * 3, btn_y, btn_size, btn_size)

        self._draw_button(screen, self._btn_rewind, "<<", ui_colors, status_font)
        pause_label = ">||" if not self.player.is_paused else "> "
        self._draw_button(screen, self._btn_pause, pause_label, ui_colors, status_font)
        self._draw_button(screen, self._btn_forward, ">>", ui_colors, status_font)
        self._draw_button(screen, self._btn_exit, "X", ui_colors, status_font,
                          bg_color=ui_colors["red"])

        if self.current_video:
            title_surf, title_rect = main_font.render(
                self.current_video.title, ui_colors["text_light"])
            title_rect.centerx = vx + vw // 2
            title_rect.bottom = vy - 8
            bg_rect = title_rect.inflate(24, 10)
            bg_surf = pygame.Surface(bg_rect.size, pygame.SRCALPHA)
            bg_surf.fill((0, 0, 0, 130))
            screen.blit(bg_surf, bg_rect.topleft)
            screen.blit(title_surf, title_rect)

        return True

    # ====== 左上角小进度条（不遮挡中部，不阻塞其他 UI）======
    def _draw_download_corner(self, screen, w, h, main_font, status_font, ui_colors):
        panel_w = 360
        panel_h = 88
        panel_x = 12
        panel_y = 12

        panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
        panel.fill((20, 20, 20, 200))
        screen.blit(panel, (panel_x, panel_y))
        pygame.draw.rect(screen, ui_colors["gold"],
                         (panel_x, panel_y, panel_w, panel_h), 2, border_radius=6)

        # 第一行：标题（过长截断）
        title = "正在下载..."
        if self.current_video:
            title = f"正在下载：{self.current_video.title}"
        if len(title) > 22:
            title = title[:21] + "…"
        t_surf, _ = status_font.render(title, ui_colors["text_light"])
        screen.blit(t_surf, (panel_x + 10, panel_y + 8))

        # 第二行：进度条
        with self.player._download_lock:
            prog = float(self.player.download_progress)
            done_b = int(self.player.download_done_bytes)
            total_b = int(self.player.download_total_bytes)

        bar_x = panel_x + 10
        bar_y = panel_y + 34
        bar_w = panel_w - 20
        bar_h = 16

        pygame.draw.rect(screen, (60, 60, 60), (bar_x, bar_y, bar_w, bar_h),
                         border_radius=4)
        fill_w = int(bar_w * max(0.0, min(1.0, prog)))
        if fill_w > 0:
            pygame.draw.rect(screen, ui_colors["gold"],
                             (bar_x, bar_y, fill_w, bar_h), border_radius=4)
        pygame.draw.rect(screen, ui_colors["gold"],
                         (bar_x, bar_y, bar_w, bar_h), 1, border_radius=4)

        # 第三行：百分比 + MB
        mb_done = done_b / 1024 / 1024
        if total_b > 0:
            mb_total = total_b / 1024 / 1024
            pct_text = f"{prog * 100:5.1f}%   {mb_done:.1f} / {mb_total:.1f} MB"
        else:
            pct_text = f"{mb_done:.1f} MB"
        p_surf, _ = status_font.render(pct_text, ui_colors["text_light"])
        screen.blit(p_surf, (panel_x + 10, panel_y + 56))

    def _draw_status_overlay(self, screen, w, h, main_font, ui_colors):
        text = self._status_text
        if not text:
            return
        color = (200, 60, 60) if self._status_is_error else ui_colors["text"]
        surf, rect = main_font.render(text, color)
        pad_x, pad_y = 30, 20
        box_w = min(w - 80, rect.width + pad_x * 2)
        box_h = rect.height + pad_y * 2
        box_x = (w - box_w) // 2
        box_y = int(h * 0.42)
        box = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
        box.fill((0, 0, 0, 170))
        screen.blit(box, (box_x, box_y))
        pygame.draw.rect(screen, ui_colors["gold"], (box_x, box_y, box_w, box_h), 2)
        screen.blit(surf, (box_x + (box_w - rect.width) // 2,
                           box_y + (box_h - rect.height) // 2))

    @staticmethod
    def _draw_button(screen, rect, label, ui_colors, font, bg_color=None):
        color = bg_color or ui_colors["dark_red"]
        pygame.draw.rect(screen, color, rect, border_radius=8)
        pygame.draw.rect(screen, ui_colors["gold"], rect, width=2, border_radius=8)
        surf, r = font.render(label, ui_colors["text_light"])
        r.center = rect.center
        screen.blit(surf, r)

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        seconds = max(0, int(seconds))
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def handle_mouse_click(self, pos, w: int, h: int) -> bool:
        # 下载中：不吞任何点击，让 main.py 处理输入框、按钮等
        # 视频控件此时也没有命中区域（_video_rect 为 None），安全
        if self.player.is_downloading:
            return False
        if not self.player.has_video:
            return False
        if self._btn_exit and self._btn_exit.collidepoint(pos):
            self.stop()
            self.player.action_queue.put((["idle"], "⏹️ 已退出播放"))
            return True
        if self._btn_pause and self._btn_pause.collidepoint(pos):
            self.player.toggle_pause()
            return True
        if self._btn_rewind and self._btn_rewind.collidepoint(pos):
            self.player.seek_seconds(-5)
            return True
        if self._btn_forward and self._btn_forward.collidepoint(pos):
            self.player.seek_seconds(5)
            return True
        if self._progress_rect and self._progress_rect.collidepoint(pos):
            ratio = (pos[0] - self._progress_rect.x) / max(1, self._progress_rect.width)
            self.player.seek_ratio(ratio)
            return True
        if self._video_rect and self._video_rect.collidepoint(pos):
            self.player.toggle_pause()
            return True
        return False

    def handle_key(self, event) -> bool:
        # 下载中：不吞任何按键（打字继续可用）；只处理 Esc 取消下载
        if self.player.is_downloading:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self.stop()
                self.player.action_queue.put((["idle"], "⏹️ 已取消下载"))
                return True
            return False
        if not self.player.has_video:
            return False
        if event.type != pygame.KEYDOWN:
            return False
        if event.key == pygame.K_ESCAPE:
            self.stop()
            self.player.action_queue.put((["idle"], "⏹️ 已退出播放"))
            return True
        if event.key == pygame.K_LEFT:
            self.player.seek_seconds(-5)
            return True
        if event.key == pygame.K_RIGHT:
            self.player.seek_seconds(5)
            return True
        return False


def create_video_player_system(action_map, action_queue, fps: int = 45,
                               engine=None, llm_client=None) -> VideoPlayerSystem:
    fetcher = VideoFetcher()
    player = VideoPlayer(action_queue, fps)
    return VideoPlayerSystem(fetcher, player)