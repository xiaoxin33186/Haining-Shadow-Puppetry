"""空场伴奏播放器（音频由海宁文化馆录制）。

用途：外出演出「空场」等待时，或让观众上手体验皮影操作时，循环播放氛围伴奏；
正式内容（视频 / 剧本 / 语音播报 / 思考中）一开始就自动淡出，退场后再淡入。

音频文件：根目录 `33.全景台.MP3`，时长约 5 分 21 秒。

音频管线约定（三条管线互相避让，勿随意改动）：
- TTS：`pygame.mixer.music` 全局单例（见 tts_engine.py）；
- 视频声音：`pygame.mixer.Sound.play()` 动态占用空闲通道（见 video_player_system.py）；
- 本伴奏：固定占用 `Channel(0)`，并用 `pygame.mixer.set_reserved(1)` 保留通道 0，
  因此不会和视频声音抢通道，也完全不触碰 mixer.music。
"""

import os
import time

import pygame

DEFAULT_AUDIO = "33.全景台.MP3"
# 现场音量直接调这里：0.0 ~ 1.0（伴奏只作背景，建议 0.10 ~ 0.20）
DEFAULT_VOLUME = 0.15


class StageBGM:
    """循环播放空场伴奏，带淡入/淡出与播放条件节流。"""

    def __init__(self, path=DEFAULT_AUDIO, volume=DEFAULT_VOLUME,
                 fade_in_ms=2500, fade_out_ms=1200, resume_delay=1.2):
        self.path = path
        self.volume = float(volume)
        self.fade_in_ms = int(fade_in_ms)
        self.fade_out_ms = int(fade_out_ms)
        self.resume_delay = float(resume_delay)
        self.sound = None
        self.channel = None
        self.available = False
        self._fading_out = False
        self._resume_at = 0.0
        self._load()

    # ---------- 初始化 ----------
    def _load(self):
        if not os.path.exists(self.path):
            print(f"[BGM] 未找到伴奏文件: {self.path}")
            return
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            if pygame.mixer.get_num_channels() < 8:
                pygame.mixer.set_num_channels(8)
            # 固定保留 0 号通道给伴奏，视频声音只会拿 1 号及以后
            pygame.mixer.set_reserved(1)
            self.channel = pygame.mixer.Channel(0)
            self.sound = pygame.mixer.Sound(self.path)
            # 音量落在 Sound 上：Channel.play(fade_ms=...) 会把通道音量从 0 拉到 1，
            # 用 Sound 音量做乘法才能既保留淡入又保持伴奏不过响。
            self.sound.set_volume(self.volume)
            self.available = True
        except Exception as e:  # noqa: BLE001
            print(f"[BGM] 伴奏加载失败，已跳过: {e}")
            self.sound = None
            self.channel = None

    # ---------- 每帧驱动 ----------
    def update(self, want_play: bool, dt: float = 0.0):
        """want_play 为 False 时淡出；连续 False 达到 resume_delay 后才允许再次淡入。"""
        if not self.available or self.channel is None:
            return
        try:
            busy = bool(self.channel.get_busy())
            if want_play:
                if not busy and not self._fading_out and time.monotonic() >= self._resume_at:
                    self.channel.play(self.sound, loops=-1, fade_ms=self.fade_in_ms)
                    print("[BGM] 空场伴奏淡入")
            else:
                if busy and not self._fading_out:
                    self.channel.fadeout(self.fade_out_ms)
                    self._fading_out = True
                    self._resume_at = time.monotonic() + self.resume_delay
                    print("[BGM] 空场伴奏淡出")
                if not busy:
                    self._fading_out = False
        except Exception as e:  # noqa: BLE001
            print(f"[BGM] 播放异常，已停用伴奏: {e}")
            self.available = False
