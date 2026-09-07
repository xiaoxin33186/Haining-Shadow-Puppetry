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


def speak(text: str):
    """合成并播放一段中文文本（阻塞式，请勿在主线程直接调用）"""
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

        # 用 pygame 播放
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.load(wav)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            import time
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


def speak_async(text: str):
    """异步播报（不阻塞主线程）"""
    threading.Thread(target=speak, args=(text,), daemon=True).start()


if __name__ == "__main__":
    if is_available():
        print("Piper TTS 可用，测试合成...")
        speak("海宁皮影戏语音测试成功")
    else:
        print(f"Piper 未找到，请检查路径：\n  {PIPER_EXE}\n  {VOICE_MODEL}")
