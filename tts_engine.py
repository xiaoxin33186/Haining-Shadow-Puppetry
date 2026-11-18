# -*- coding: utf-8 -*-
"""海宁皮影戏 · 本地 Piper TTS 引擎
piper 主程序与语音模型位于 PIPER_EXE / VOICE_MODEL 所指路径。
注意：piper 必须放在【无中文】的路径下，否则 espeak-ng 会报 "Illegal byte sequence" 错误。
用法：
    import tts_engine
    tts_engine.speak_async("要朗读的文本")   # 异步播报，不阻塞主线程
"""
import os
import subprocess
import tempfile
import threading
from pathlib import Path

# piper 程序目录位于项目根目录下的 piper/（随项目一起部署）
_PROJECT_ROOT = Path(__file__).resolve().parent

# ============ piper 位于项目根目录下 piper/ 文件夹 ============
PIPER_EXE = str(_PROJECT_ROOT / "piper" / "piper" / "piper.exe")          # piper.exe 完整路径
VOICE_MODEL = str(_PROJECT_ROOT / "piper" / "zh_CN-huayan-medium.onnx")  # 中文语音模型
PIPER_DIR = str(_PROJECT_ROOT / "piper" / "piper")                        # piper 所在目录（用于定位 espeak-ng-data）
# ================================================================


def is_available() -> bool:
    """检查 piper 和语音模型是否就绪"""
    return os.path.exists(PIPER_EXE) and os.path.exists(VOICE_MODEL)


def _clean(text: str) -> str:
    """去除回复中的 </STYLE> 标记及评分信息，只保留正文"""
    import re
    text = re.sub(r"</?STYLE>.*", "", text, flags=re.DOTALL).strip()
    return text


def speak(text: str, seq=None):
    """合成并播放一段中文文本（阻塞式，请勿在主线程直接调用）

    seq 为调用时的停止序号：若播放期间用户点了"停止"（序号变化），则立即中断。
    """
    if not is_available():
        print("[TTS] Piper 不可用，跳过语音")
        return
    text = _clean(text)
    if not text or len(text) < 2:
        return

    print(f"[TTS] 播报: {text[:50]}...")
    wav = tempfile.mktemp(suffix=".wav")
    try:
        # 文本通过 stdin 传入，-f 指定输出 wav 文件
        proc = subprocess.run(
            [PIPER_EXE, "-m", VOICE_MODEL, "-f", wav],
            input=text.encode("utf-8"),
            capture_output=True,
            cwd=PIPER_DIR,
            timeout=60,
        )
        if proc.returncode != 0 or not os.path.exists(wav):
            print(f"[TTS] Piper 合成失败: {proc.stderr.decode('utf-8', errors='replace')[:200]}")
            return
        # 合成期间可能已经被停止，直接丢弃
        if seq is not None and seq != _stop_seq:
            print("[TTS] 已停止，跳过本次播报")
            return

        # 用 pygame 播放
        import pygame
        import time
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.load(wav)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            if seq is not None and seq != _stop_seq:
                pygame.mixer.music.stop()
                break
            time.sleep(0.1)
        pygame.mixer.music.unload()
    except Exception as e:
        print(f"[TTS] 播报失败: {e}")
    finally:
        try:
            if os.path.exists(wav):
                os.remove(wav)
        except Exception:
            pass


# ===== 播报串行化 =====
# pygame.mixer.music 是全局单例：多个 TTS 线程并发 load/play/unload 会互相把对方正在播放的
# 音频释放掉（实测 4 个并发 speak 仅 2.7s 就"全部结束"，而单次完整播放需 1.5s），
# 严重时会卡死在 SDL_mixer 内部锁上，把 pygame 主循环一起拖住（表现为界面死机）。
# 这里改为「一个常驻工作线程」串行播放，所有 mixer 调用都只发生在这一个线程里。
_cond = threading.Condition()
_pending = None
_worker = None
_stop_seq = 0          # 停止序号：递增后正在合成/播放的语音会自行退出


def _worker_loop():
    """常驻播报线程：串行取用待播文本，保证同一时刻只有一段语音。"""
    global _pending
    while True:
        with _cond:
            while _pending is None:
                _cond.wait()
            text = _pending
            _pending = None
            seq = _stop_seq
        try:
            speak(text, seq)
        except Exception as e:  # noqa: BLE001
            print(f"[TTS] 播报异常（已忽略，线程继续）: {e}")


def speak_async(text: str):
    """异步播报（不阻塞主线程），同一时刻只会有一段语音在播放。

    连发手势时不会堆积：若队列里已有未开始播放的请求，直接用本次覆盖它（只保留最新一条）。
    """
    global _pending, _worker
    if not is_available():
        return
    with _cond:
        _pending = text
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_loop, daemon=True)
            _worker.start()
        _cond.notify()


def stop():
    """立即停止当前播报并丢弃未播内容（停止按钮 / 功能互斥时调用）。"""
    global _pending, _stop_seq
    with _cond:
        _pending = None
        _stop_seq += 1
    try:
        import pygame
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
    except Exception as e:  # noqa: BLE001
        print(f"[TTS] 停止失败: {e}")


if __name__ == "__main__":
    if is_available():
        print("Piper TTS 可用，测试合成...")
        speak("海宁皮影戏语音测试成功")
    else:
        print(f"Piper 未找到，请检查路径：\n  {PIPER_EXE}\n  {VOICE_MODEL}")
