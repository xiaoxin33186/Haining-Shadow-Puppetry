# -*- coding: utf-8 -*-
"""
功能：
提供两种输入模式，可通过右下角按钮切换：
    1. 文字模式: 点击输入框或按 Tab 激活，输入文本，按 Enter 或 Tab 提交。
    2. 语音模式: 按 'Space' 键开始/停止录音，识别结果提交给 Agent。
语音识别结果会短暂显示在屏幕右上角。
"""

import threading
import asyncio
import queue
import json
import os
import re
from pathlib import Path
import time

try:
    import tkinter as tk
    HAS_TKINTER = True
except ImportError:
    HAS_TKINTER = False

# 显示系统输入法候选框（SDL IME UI）。
# 必须在 import pygame / SDL 创建窗口（set_mode）之前设置，否则不生效。
os.environ["SDL_IME_SHOW_UI"] = "1"

import pygame
import pygame.freetype


import sounddevice as sd
import numpy as np
from vosk import Model, KaldiRecognizer

# [TTS] 本地 Piper 语音合成
try:
    import tts_engine
    HAS_TTS = tts_engine.is_available()
except Exception as e:
    HAS_TTS = False
    tts_engine = None
if HAS_TTS:
    print("[TTS] 本地 Piper 语音合成已就绪")
else:
    print("[TTS] Piper 未安装（项目内 piper/ 目录），语音播报禁用")

# [BGM] 空场伴奏（音频由海宁文化馆录制，根目录 33.全景台.MP3）
try:
    import stage_bgm
    HAS_BGM = True
except Exception as e:  # noqa: BLE001
    HAS_BGM = False
    stage_bgm = None
    print(f"[BGM] 伴奏模块不可用: {e}")

# [GESTURE] 摄像头抽象手势识别（按需启动），缺依赖时优雅禁用
try:
    from gesture_recognizer import GestureRecognizer, is_available as gesture_is_available
    HAS_GESTURE = gesture_is_available()
    if not HAS_GESTURE:
        print("[GESTURE] 摄像头手势功能不可用（缺 mediapipe tasks 或 opencv）。")
except ImportError:
    HAS_GESTURE = False
    gesture_is_available = lambda: False
    print("[GESTURE] 警告: 未找到 gesture_recognizer.py，摄像头手势功能不可用。")

# [CHATCHAT] Chatchat 后端对接开关：True=使用 LangChain-Chatchat，False=使用原 AutoGen
USE_CHATCHAT = True  # [CHATCHAT]
# [HYBRID] 三级混合检索开关：True=Skill优先+Chatchat兜底+模型直答，False=纯Chatchat
USE_HYBRID_THREE_LEVEL = True  # [HYBRID]

try:
    from digital_human_agents_v2 import DigitalHumanAgentSystem
    import pygame_Alpha as pg_base
    if USE_CHATCHAT:  # [CHATCHAT]
        try:  # [CHATCHAT]
            if USE_HYBRID_THREE_LEVEL:  # [HYBRID]
                from skill_chatchat_hybrid import HybridAgentSystem as ChatchatAgentSystem  # [HYBRID]
                print("[HYBRID] 已启用三级混合检索：Skill知识库 → Chatchat → 模型直答")  # [HYBRID]
            else:  # [HYBRID]
                from chatchat_adapter import ChatchatAgentSystem  # [CHATCHAT]
                print("[CHATCHAT] 已启用 LangChain-Chatchat 后端对接模式")  # [CHATCHAT]
        except ImportError as e_chatchat:  # [CHATCHAT]
            print(f"[CHATCHAT] 警告: 无法导入适配器，将回退到原 Agent 系统: {e_chatchat}")  # [CHATCHAT]
            USE_CHATCHAT = False  # [CHATCHAT]
except ImportError as e:
    print(f"[致命错误] 无法导入必要的自定义模块: {e}")
    print("请确保 'digital_human_agents_v2.py' 和 'pygame_Alpha_base.py' 文件存在于同一目录。")
    exit()

# [剧本集成] 导入剧本系统（剧目库调用/播放）
try:
    from script_player import create_script_system, ScriptUI
    SCRIPT_SYSTEM_AVAILABLE = True
    print("[剧本] 剧本系统模块加载成功")
except ImportError as e:
    print(f"[剧本] 警告: 无法导入剧本系统: {e}，剧本功能将不可用")
    SCRIPT_SYSTEM_AVAILABLE = False

    class ScriptUI:  # 占位类，避免 NameError
        def __init__(self, *args, **kwargs):
            pass
        def list_plays(self, *args, **kwargs):
            return "剧本系统不可用"
        def load_and_play(self, *args, **kwargs):
            pass
        def generate_and_play(self, *args, **kwargs):
            pass
        def play(self, *args, **kwargs):
            pass
        def pause(self, *args, **kwargs):
            pass
        def stop(self, *args, **kwargs):
            pass
        @property
        def current_script(self):
            return None
        @property
        def player(self):
            class Dummy:
                is_playing = False
                is_paused = False
                def get_status(self):
                    return {}
            return Dummy()

    def create_script_system(*args, **kwargs):
        return ScriptUI()

# [视频集成] 导入视频播放系统（剧本模式下用带“视频”的指令触发）
try:
    from video_player_system import create_video_player_system, VideoPlayerSystem
    VIDEO_SYSTEM_AVAILABLE = True
    print("[视频] 视频播放系统模块加载成功")
except Exception as e:  # noqa: BLE001
    print(f"[视频] 警告: 无法导入视频播放系统: {e}，视频功能将不可用")
    print("       常见原因：缺少 opencv-python / requests / numpy")
    VIDEO_SYSTEM_AVAILABLE = False
    VideoPlayerSystem = None

    def create_video_player_system(*args, **kwargs):
        return None

# [分享卡片] 导入分享系统（预制底图 + 模型生成文字）
try:
    from share_card import create_share_system, ShareCardSystem
    SHARE_SYSTEM_AVAILABLE = True
    print("[分享] 分享卡片系统模块加载成功")
except ImportError as e:
    print(f"[分享] 警告: 无法导入分享卡片系统: {e}，分享功能将不可用")
    SHARE_SYSTEM_AVAILABLE = False
    ShareCardSystem = None

    def create_share_system(*args, **kwargs):
        return None

ACTION_MAP = {
    "拱手礼": "greet",
    "跳舞":   "dance",
    "常态":   "normal",
    "行礼":   "dun",
    "跳跃":   "jump",
    "抓耳挠腮": "scratch",
    "向前走":   "forward",
    "向后走":   "back",
    "空闲":   "idle",
    "出拳":   "punch",
    "踢腿":   "kick",
    "格挡":   "block",
    "前冲":   "lunge",
    "后撤":   "retreat",
    "受击":   "hit",
}

# [GESTURE] 摄像头抽象手势 -> 皮影动作映射（四个单手手势）
# 单手手势 left/right -> 对应左右人偶
GESTURE_ACTION_MAP = {
    "fist":  ("actions/punch.json",   "吃我一拳！"),
    "kick":  ("actions/kick.json",    "看我的飞踢！"),
    "push":  ("actions/forward.json", "向前走！"),
    "pinch": ("actions/back.json",    "向后走！"),
}

# 原地类动作（播放结束后人物归位到界面中央）；
# 移动类动作（forward/back 向前走/向后走）结束后保持当前位置；
# 攻击类动作（punch/kick/block/hit）保持当前位置——便于"走到对手面前出拳/踢腿"
CENTER_RESET_ACTIONS = {"dance", "dun", "greet", "jump", "scratch", "normal", "idle"}
FPS = 45

REC_FS = 16000
REC_CHANNELS = 1
REC_BLOCKSIZE = 4000
VOSK_MODEL_PATH = "model-cn/vosk-model-small-cn-0.22"


UI_COLORS = {
    "background": (245, 235, 220),
    "red": (180, 30, 30),
    "dark_red": (100, 10, 10),
    "light_red": (220, 120, 120),
    "gold": (217, 164, 91),
    "button_text": (100, 10, 10),
    "text": (50, 20, 10),
    "text_light": (245, 245, 240),
    "input_bg": (240, 217, 181, 200),
    "output_bg": (245, 225, 190, 220),
    "border": (180, 80, 60),
    "corner": (160, 60, 40),
    "recording_active": (0, 180, 0),
    "recording_idle": (120, 120, 120),
    "hint_text": (100, 80, 60, 180),
    "recognized_text": (255, 255, 255),
    "script_status": (50, 100, 50),  # [剧本集成] 剧本状态颜色
}

FONT_PATH = "font.ttf"

FALLBACK_FONT = "simhei"
INPUT_HINT = "点击或按Tab输入问题..."
RECOGNIZED_TEXT_DURATION = FPS * 4
OUTPUT_FADE_DURATION = FPS // 3

# [拖拽] 手势位移 -> 角色平移参数
MOVE_SCALE = 420.0      # 手归一化位移 -> 角色像素位移
MOVE_DEAD_ZONE = 0.012  # 手位移死区（防抖）
DRAG_EASE = 0.18        # 拖拽缓动系数（越大越跟手）
MIN_GAP = 220.0         # 两偶最小间距（防重叠）

action_queue = queue.Queue()
# [剧本集成] 右偶专用队列：剧本播放时按角色把动作/台词投给右偶；
# 非双偶布局时没有消费者，ScriptPlayer 会自动把右偶台词回落到左队列。
action_queue_right = queue.Queue()
last_question_time = time.time()
# 是否正在等待回答：提问后到回复显示前为 True，用于界面显示"正在思考"
pending_question = False
# 提问序号：新提问或点"停止"时递增，使旧回答失效
question_seq = 0
dh_system = None
gesture_recognizer = None   # [GESTURE]
gesture_mode_on = False     # [GESTURE] 手势控制开关（按钮控制）

# [双人偶] 两只人偶的角色素材与全局人偶列表（下标0=左，1=右）
CHAR_PUPPET0 = "xinwukong_material"        # [双人偶] 左偶默认：新悟空
CHAR_PUPPET1 = "bull_demon_king_material"  # [双人偶] 右偶默认：牛魔王
PUPPETS = []                               # [双人偶]
PUPPET_ROLES_PATH = Path("puppet_roles.json")  # [角色切换] 持久化文件

# [剧本集成] 剧本模式开关与全局剧本系统实例
SCRIPT_MODE_ENABLED = True  # True=拦截剧本命令，False=全部交给 Agent
script_system = None

# [视频集成] 全局视频播放系统实例（首次用到“视频”指令时懒加载联网）
video_system = None

# [分享卡片] 全局分享系统实例与开关（默认关闭，点击底部“分享卡片”按钮开启）
share_system = None
SHARE_MODE_ENABLED = False

# [BGM] 空场伴奏开关（主界面 ♪ 图标切换，默认开启）
bgm_enabled = True


def load_puppet_roles():  # [角色切换]
    """读取 puppet_roles.json，返回 (left, right)；缺失/非法时回退默认常量。"""
    left, right = CHAR_PUPPET0, CHAR_PUPPET1
    try:
        with open(PUPPET_ROLES_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            if data.get("left"):
                left = data["left"]
            if data.get("right"):
                right = data["right"]
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        pass
    valid = getattr(pg_base, "CHARACTERS", []) if 'pg_base' in globals() else []
    if valid:
        if left not in valid:
            left = CHAR_PUPPET0 if CHAR_PUPPET0 in valid else valid[0]
        if right not in valid:
            right = CHAR_PUPPET1 if CHAR_PUPPET1 in valid else valid[-1]
    return left, right


def save_puppet_roles(left, right):  # [角色切换]
    """把当前左右偶角色写回 puppet_roles.json。"""
    try:
        with open(PUPPET_ROLES_PATH, 'w', encoding='utf-8') as f:
            json.dump({"left": left, "right": right}, f, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001
        print(f"[角色切换] 保存角色失败: {e}")


def enqueue_gesture_action(hand: str, gesture_name: str):  # [GESTURE]
    """摄像头检测到手势后回调：把动作投入目标人偶的队列（线程安全）。

    hand: "left"/"right"/"unknown"。
    left/unknown -> 左偶（新悟空）；right -> 右偶（牛魔王）。
    """
    entry = GESTURE_ACTION_MAP.get(gesture_name)
    if not entry:
        return
    action_path, reply = entry
    # 先取快照再索引：主线程重建布局时会 clear()+append()，直接按索引读会越界打死手势线程
    puppets = list(PUPPETS)
    target = (puppets[1] if len(puppets) > 1 and hand == "right"
              else puppets[0] if puppets else None)
    if target is None:
        print(f"[GESTURE] {hand}手 手势 '{gesture_name}' 触发动作，但人偶未就绪，已忽略: {action_path}")
        return
    print(f"[GESTURE] {hand}手 手势 '{gesture_name}' 触发动作: {action_path} -> {target.name}")
    target.queue.put(([action_path], reply))

is_recording = False
audio_buffers = []
rec_stream = None
vosk_model = None
last_recognized_text = ""
recognized_text_timer = 0

def initialize_agent_system():
    """初始化 DigitalHumanAgentSystem（或 ChatchatAgentSystem）。"""
    global dh_system
    try:
        action_paths = {
            k: f"actions/{v}.json" for k, v in ACTION_MAP.items() if v
        }
        valid_paths = {}
        for name, path in action_paths.items():
            if Path(path).exists():
                valid_paths[name] = path
            else:
                print(f"[警告] 动作文件未找到，将忽略: {path}")

        if not valid_paths:
             print("[错误] 未找到任何有效的动作文件。请检查 'actions' 目录和 ACTION_MAP")
             dh_system = None
        else:
             print(f"初始化 Agent 系统，使用动作: {list(valid_paths.keys())}")
             if USE_CHATCHAT and 'ChatchatAgentSystem' in globals():  # [CHATCHAT]
                 print("[CHATCHAT] 使用 ChatchatAgentSystem（LangChain-Chatchat 后端）")  # [CHATCHAT]
                 dh_system = ChatchatAgentSystem(valid_paths)  # [CHATCHAT]
             else:  # [CHATCHAT]
                 print("[CHATCHAT] 使用 DigitalHumanAgentSystem（原 AutoGen 系统）")  # [CHATCHAT]
                 dh_system = DigitalHumanAgentSystem(valid_paths)  # [CHATCHAT]

    except Exception as e:
        print(f"[错误] 初始化 Agent 系统失败: {e}")
        dh_system = None

# [剧本集成] 初始化剧本系统
def initialize_script_system():
    global script_system
    if not SCRIPT_SYSTEM_AVAILABLE:
        print("[剧本] 剧本系统不可用")
        script_system = None
        return
    try:
        engine = getattr(dh_system, "skill_engine", None) if dh_system else None
        llm_client = getattr(dh_system, "_ollama_client", None) if dh_system else None
        script_system = create_script_system(ACTION_MAP, action_queue, FPS,
                                             engine=engine, llm_client=llm_client,
                                             right_queue=action_queue_right)
        script_system.player.has_right_puppet = len(PUPPETS) > 1
        print("[剧本] 剧本系统初始化完成")
    except Exception as e:
        print(f"[剧本] 初始化失败: {e}")
        script_system = None

# [视频集成] 初始化视频播放系统（不联网，首次用到“视频”指令时才抓取）
def initialize_video_system():
    global video_system
    if not VIDEO_SYSTEM_AVAILABLE:
        print("[视频] 视频系统不可用")
        video_system = None
        return
    try:
        engine = getattr(dh_system, "skill_engine", None) if dh_system else None
        llm_client = getattr(dh_system, "_ollama_client", None) if dh_system else None
        video_system = create_video_player_system(ACTION_MAP, action_queue, FPS,
                                                  engine=engine, llm_client=llm_client)
        print("[视频] 视频播放系统初始化完成")
    except Exception as e:
        print(f"[视频] 初始化失败: {e}")
        video_system = None


def _video_active() -> bool:
    """视频是否正在加载/播放（用于切换双偶布局与接管视频控件）。

    必须带上 is_extracting_audio：抽音轨阶段 has_video 和 is_downloading 都是 False，
    漏了它 render() 就不会被调用，player.update() 也跟着不跑，视频永远打不开。
    """
    if video_system is None:
        return False
    player = getattr(video_system, "player", None)
    if player is None:
        return False
    return bool(getattr(player, "has_video", False)
                or getattr(player, "is_downloading", False)
                or getattr(player, "is_extracting_audio", False))


def _video_sound_on() -> bool:
    """视频音频是否正在响（播放中且未暂停）。

    用于掐断 TTS：视频声音走 pygame 独立 Channel，TTS 走 mixer.music 全局单例，
    两条管线互不知情，同时出声会叠在一起。这里让视频播放期间 TTS 让路。
    注意不能用 _video_active()：它把"下载中"也算作活跃，而下载阶段没有声音，TTS 应当照常。
    """
    if video_system is None:
        return False
    player = getattr(video_system, "player", None)
    if player is None:
        return False
    playing = bool(getattr(player, "is_playing", False))
    paused = bool(getattr(player, "is_paused", False))
    return playing and not paused


def _script_active() -> bool:
    """剧本是否正在播放/暂停（用于播放期间切换到双偶布局）。"""
    if script_system is None:
        return False
    player = getattr(script_system, "player", None)
    if player is None:
        return False
    return bool(getattr(player, "is_playing", False) or getattr(player, "is_paused", False))


def _bgm_should_play() -> bool:
    """空场伴奏的播放条件：只在没有正式内容时循环。

    视频、剧本、思考中、分享卡片弹层、TTS 播报任意一项活跃时都让路，
    其余时间（含手势体验、观众上手操作）都视为“空场”，可以放伴奏。
    """
    if _video_active() or _script_active():
        return False
    if pending_question:
        return False
    if share_system is not None and share_system.is_card_visible():
        return False
    if HAS_TTS:
        try:
            if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
                return False
        except Exception:
            pass
    return True


def stop_other_features(*, keep_script=False, keep_video=False):
    """功能互斥：开始一项功能前，停掉其它正在播放/生成的内容，避免画面与声音叠加。"""
    if not keep_script and _script_active():
        try:
            script_system.stop()
            print("[打断] 已停止剧本播放")
        except Exception as e:  # noqa: BLE001
            print(f"[打断] 停止剧本失败: {e}")
    if not keep_video and _video_active():
        try:
            video_system.stop()
            print("[打断] 已停止视频播放")
        except Exception as e:  # noqa: BLE001
            print(f"[打断] 停止视频失败: {e}")
    if HAS_TTS and tts_engine is not None:
        try:
            tts_engine.stop()
        except Exception as e:  # noqa: BLE001
            print(f"[打断] 停止语音失败: {e}")


def reset_runtime_state():
    """停止按钮：中断剧本/视频/语音/待答问题，清空动作队列，人偶回到初始站位。"""
    global pending_question, question_seq
    question_seq += 1          # 让在途回答失效，避免停止后又冒出来
    pending_question = False
    stop_other_features()
    if script_system is not None:
        try:
            # 播放已在 stop_other_features() 中停止，这里只清剧本信息
            script_system.current_script = None
            script_system.current_play_name = None
        except Exception as e:  # noqa: BLE001
            print(f"[停止] 重置剧本失败: {e}")
    # 清空待执行动作（含排队中的回答），避免停止后继续播
    for q in (action_queue, action_queue_right):
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                break
    for p in list(PUPPETS):
        try:
            p.reset_pose()
        except Exception as e:  # noqa: BLE001
            print(f"[停止] 重置人偶失败: {e}")


def handle_video_command(text: str) -> bool:
    """处理含“视频”关键词的指令；返回 True 表示已接管。

    指令形如：视频目录 / 播放视频 <名称> / 视频本地目录 / 播放本地视频 <文件名> /
    暂停视频 / 继续视频 / 停止视频 / 视频状态。
    首次调用会懒加载联网（检查网络 + 抓取或加载缓存）；本机视频指令纯本地、不联网。
    """
    if video_system is None:
        action_queue.put((["idle"], "视频系统未初始化或不可用"))
        return True

    cmd = text.replace("视频", "").strip()

    # 本机视频（download_videos/）：纯本地。必须放在联网检查之前，
    # 否则断网时那句"请连接网络"会顺手把本机播放一起拦掉。
    if "本地" in cmd:
        rest = cmd.replace("本地", "").replace("播放", "").replace("表演", "").strip()
        try:
            if not rest or any(k in rest for k in ("目录", "列表")):
                action_queue.put((["idle"], video_system.list_local_videos()))
            else:
                stop_other_features(keep_video=True)   # 视频优先：打断剧本/语音
                video_system.load_local_file(rest)
        except Exception as e:  # noqa: BLE001
            print(f"[视频错误] {e}")
            action_queue.put((["idle"], f"❌ 本机视频操作失败: {str(e)[:30]}"))
        return True

    # 首次使用：联网检查 + 抓取/加载本地缓存
    if not getattr(video_system, "_network_checked", False):
        try:
            ok, msg = video_system.initialize_network()
        except Exception as e:  # noqa: BLE001
            ok, msg = False, f"视频系统异常: {e}"
        if not ok:
            action_queue.put((["idle"], msg or "请连接网络"))
            return True

    try:
        if any(k in cmd for k in ("目录", "列表")) or cmd == "":
            action_queue.put((["idle"], video_system.list_plays()))
            return True
        if "暂停" in cmd:
            if video_system.player.is_playing:
                video_system.pause()
                action_queue.put((["idle"], "⏸️ 已暂停"))
            else:
                action_queue.put((["idle"], "⚠️ 没有播放中的视频"))
            return True
        if "继续" in cmd:
            if video_system.player.is_paused:
                video_system.play()
                action_queue.put((["idle"], "▶️ 继续播放"))
            else:
                action_queue.put((["idle"], "⚠️ 没有暂停的视频"))
            return True
        if "停止" in cmd or "退出" in cmd:
            video_system.stop()
            action_queue.put((["idle"], "⏹️ 已停止视频"))
            return True
        if "状态" in cmd:
            if video_system.current_script:
                status = video_system.get_status()
                msg = f"《{video_system.current_script.title}》"
                if status.get("is_playing"):
                    msg += f" ▶️ {int(status.get('progress', 0) * 100)}%"
                elif status.get("is_paused"):
                    msg += " ⏸️ 暂停"
                else:
                    msg += " ⏹️ 停止"
                action_queue.put((["idle"], msg))
            else:
                action_queue.put((["idle"], "📭 没有视频"))
            return True
        # 播放/表演（放在暂停/继续/停止之后，避免“继续播放”被误判为换片）
        if any(k in cmd for k in ("播放", "表演")):
            name = cmd.replace("播放", "").replace("表演", "").strip()
            stop_other_features(keep_video=True)   # 视频优先：打断剧本/语音
            if name:
                video_system.load_and_play(name)
            elif video_system.current_script:
                video_system.play()
                action_queue.put((["idle"], f"▶️ 继续播放《{video_system.current_script.title}》"))
            else:
                action_queue.put((["idle"], "请指定视频名称，例如“播放视频 闹龙宫”"))
            return True
        # 只说了“视频”未带子指令
        action_queue.put((["idle"],
                          "视频指令：视频目录 / 播放视频 <名称> / 视频本地目录 / "
                          "播放本地视频 <文件名> / 暂停视频 / 继续视频 / 停止视频 / 视频状态"))
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[视频错误] {e}")
        action_queue.put((["idle"], f"❌ 视频操作失败: {str(e)[:30]}"))
        return True

# [分享卡片] 初始化分享系统（优先复用 Agent 的知识库与大模型客户端）
def initialize_share_system():
    global share_system
    if not SHARE_SYSTEM_AVAILABLE:
        print("[分享] 分享系统不可用")
        share_system = None
        return
    try:
        engine = getattr(dh_system, "skill_engine", None) if dh_system else None
        llm_client = getattr(dh_system, "_ollama_client", None) if dh_system else None
        if llm_client is None:
            try:
                from openai import OpenAI
                import chatchat_config as _cfg
                llm_client = OpenAI(base_url=_cfg.OLLAMA_BASE_URL, api_key=_cfg.OLLAMA_API_KEY)
            except Exception:  # noqa: BLE001
                llm_client = None
        share_system = create_share_system(action_queue, engine=engine, llm_client=llm_client)
        print("[分享] 分享系统初始化完成")
    except Exception as e:
        print(f"[分享] 初始化失败: {e}")
        share_system = None

def initialize_vosk_model():
    global vosk_model
    model_path = Path(VOSK_MODEL_PATH)
    if not model_path.exists():
        print(f"[错误] Vosk 模型路径未找到: '{VOSK_MODEL_PATH}'")
        print("请下载 Vosk 中文模型并解压到指定路径。语音识别功能将不可用。")
        vosk_model = None
        return

    try:
        print(f"加载 Vosk 模型: {VOSK_MODEL_PATH} ...")
        vosk_model = Model(str(model_path))
        print("Vosk 模型加载成功。")
    except Exception as e:
        print(f"[错误] 加载 Vosk 模型失败: {e}")
        vosk_model = None

def initialize_fonts():
    global main_font, input_font, status_font
    try:
        pygame.freetype.init()
        main_font = pygame.freetype.Font(FONT_PATH, 22)
        input_font = pygame.freetype.Font(FONT_PATH, 18)
        status_font = pygame.freetype.Font(FONT_PATH, 16)
        print(f"自定义字体 '{FONT_PATH}' 加载成功")
    except Exception as e:
        print(f"[警告] 加载自定义字体失败 ({FONT_PATH}): {e}.")
        print(f"尝试使用系统字体 '{FALLBACK_FONT}'...")
        try:
            main_font = pygame.freetype.SysFont(FALLBACK_FONT, 24)
            input_font = pygame.freetype.SysFont(FALLBACK_FONT, 20)
            status_font = pygame.freetype.SysFont(FALLBACK_FONT, 18)
            print(f"系统字体 '{FALLBACK_FONT}' 加载成功")
        except Exception as e_fallback:
            print(f"[错误] 加载系统字体 '{FALLBACK_FONT}' 也失败: {e_fallback}.")
            print("将使用 Pygame 默认字体")
            main_font = pygame.freetype.SysFont(None, 26)
            input_font = pygame.freetype.SysFont(None, 22)
            status_font = pygame.freetype.SysFont(None, 20)

def create_styled_box(width, height, bg_color, border_color, corner_color, border_radius=8, border_width=2, corner_size=12):

    # 确保宽度和高度是整数且为正
    width, height = max(1, int(width)), max(1, int(height))
    surface = pygame.Surface((width, height), pygame.SRCALPHA)

    pygame.draw.rect(surface, bg_color, (0, 0, width, height), border_radius=border_radius)

    pygame.draw.rect(surface, border_color, (0, 0, width, height), width=border_width, border_radius=border_radius)

    line_width = border_width

    corner_size = min(corner_size, width // 2, height // 2, border_radius + line_width)

    if corner_size > line_width:
        # 左上角
        pygame.draw.line(surface, corner_color, (0, corner_size), (0, 0 + line_width//2), line_width)
        pygame.draw.line(surface, corner_color, (0 + line_width//2, 0), (corner_size, 0), line_width)
        # 右上角
        pygame.draw.line(surface, corner_color, (width - corner_size, 0), (width - line_width//2, 0), line_width)
        pygame.draw.line(surface, corner_color, (width, 0 + line_width//2), (width, corner_size), line_width)
        # 左下角
        pygame.draw.line(surface, corner_color, (0, height - corner_size), (0, height - line_width//2), line_width)
        pygame.draw.line(surface, corner_color, (0 + line_width//2, height), (corner_size, height), line_width)
        # 右下角
        pygame.draw.line(surface, corner_color, (width - corner_size, height), (width - line_width//2, height), line_width)
        pygame.draw.line(surface, corner_color, (width, height - line_width//2), (width, height - corner_size), line_width)

    return surface

def create_input_box(width, height):
    return create_styled_box(width, height, UI_COLORS["input_bg"], UI_COLORS["border"], UI_COLORS["corner"], border_radius=8, corner_size=12)

def create_output_box(width, height):

    return create_styled_box(width, height, UI_COLORS["output_bg"], UI_COLORS["border"], UI_COLORS["corner"], border_radius=10, corner_size=15)



def process_and_enqueue(text: str):

    global last_recognized_text, recognized_text_timer, last_question_time, script_system, SCRIPT_MODE_ENABLED, share_system, SHARE_MODE_ENABLED, pending_question

    my_seq = question_seq          # 本次提问序号：被"停止"或新提问打断后作废
    pending_question = False       # 命令类输入不进入思考态；仅 Agent 问答会重新置位
    text = text.strip()
    if not text:
        print("[Agent] 输入为空，忽略。")
        return

    # ========== 1. 视频命令处理（含“视频”且带子指令时优先，剧本模式下同样可用） ==========
    _VIDEO_CMDS = ("目录", "列表", "播放", "表演", "暂停", "继续", "停止", "退出", "状态", "本地")
    if (video_system is not None and "视频" in text
            and (text.strip() == "视频" or any(k in text for k in _VIDEO_CMDS))):
        print(f"[视频] 拦截命令: {text}")
        handle_video_command(text)
        last_recognized_text = ""
        recognized_text_timer = 0
        return

    # ========== 1.5 分享卡片（按钮开启后可触发；剧本模式下说出关键词同样可触发） ==========
    if share_system is not None and (SHARE_MODE_ENABLED or SCRIPT_MODE_ENABLED) \
            and share_system.is_share_intent(text):
        print(f"[分享] 触发分享生成: {text}")
        _cur_script = script_system.current_script if script_system is not None else None
        try:
            share_system.handle(text, _cur_script)
        except Exception as e:  # noqa: BLE001
            print(f"[分享错误] {e}")
            action_queue.put((["idle"], f"❌ 分享生成失败: {str(e)[:30]}"))
        last_recognized_text = ""
        recognized_text_timer = 0
        return

    # ========== 2. 剧本命令处理（最高优先级） ==========
    if script_system is not None and SCRIPT_MODE_ENABLED:
        is_script = any(kw in text for kw in
                        ["剧本", "播放", "表演", "暂停", "继续", "停止", "状态", "目录", "列表"])
        if is_script:
            print(f"[剧本] 拦截命令: {text}")
            stop_other_features(keep_script=True)   # 剧本优先：打断视频/语音
            try:
                # ---------- 目录/列表命令 ----------
                if "目录" in text or "列表" in text:
                    action_queue.put((["idle"], script_system.list_plays()))
                    last_recognized_text = ""
                    recognized_text_timer = 0
                    return
                # ---------- 生成剧本命令（根据用户话语现场生成） ----------
                if any(k in text for k in ["生成剧本", "创作剧本", "编个剧本", "编一个剧本",
                                           "编一段", "写个剧本", "写一个剧本", "生成故事"]):
                    prompt = text
                    for kw in ["生成剧本", "创作剧本", "编个剧本", "编一个剧本",
                               "编一段", "写个剧本", "写一个剧本", "生成故事"]:
                        prompt = prompt.replace(kw, "")
                    prompt = prompt.strip()
                    if not prompt:
                        action_queue.put((["idle"], "请说明想生成的剧目主题，例如“生成剧本 孙悟空三借芭蕉扇”"))
                    else:
                        script_system.generate_and_play(prompt)
                    last_recognized_text = ""
                    recognized_text_timer = 0
                    return
                # ---------- 剧本命令（从剧目库调取，未收录则现场生成） ----------
                if "剧本" in text:
                    prompt = text.replace("剧本", "").strip()
                    if not prompt:
                        action_queue.put((["idle"], "请指定剧名或主题，例如“剧本 闹龙宫”或“剧本 西游记”"))
                    else:
                        script_system.load_and_play(prompt)
                    last_recognized_text = ""
                    recognized_text_timer = 0
                    return

                # ---------- 播放/表演命令 ----------
                elif "播放" in text or "表演" in text:
                    cmd = "播放" if "播放" in text else "表演"
                    play_name = text.replace(cmd, "").strip()
                    if play_name:
                        script_system.load_and_play(play_name)
                    else:
                        if script_system.current_script:
                            script_system.play()
                            action_queue.put((["idle"], f"▶️ 继续播放《{script_system.current_script.title}》"))
                        else:
                            action_queue.put((["idle"], "⚠️ 没有加载的剧本，请先使用“剧本 剧名”或“播放 剧名”"))
                    last_recognized_text = ""
                    recognized_text_timer = 0
                    return

                elif "暂停" in text:
                    if script_system.player.is_playing:
                        script_system.pause()
                        action_queue.put((["idle"], "⏸️ 已暂停"))
                    else:
                        action_queue.put((["idle"], "⚠️ 没有播放中的剧本"))
                    last_recognized_text = ""
                    recognized_text_timer = 0
                    return

                elif "继续" in text:
                    if script_system.player.is_paused:
                        script_system.play()
                        action_queue.put((["idle"], "▶️ 继续播放"))
                    else:
                        action_queue.put((["idle"], "⚠️ 没有暂停的播放"))
                    last_recognized_text = ""
                    recognized_text_timer = 0
                    return

                elif "停止" in text:
                    script_system.stop()
                    action_queue.put((["idle"], "⏹️ 已停止"))
                    last_recognized_text = ""
                    recognized_text_timer = 0
                    return

                elif "状态" in text:
                    if script_system.current_script:
                        status = script_system.player.get_status()
                        msg = f"《{script_system.current_script.title}》"
                        if status["is_playing"]:
                            msg += f" ▶️ {int(status['progress']*100)}%"
                        elif status["is_paused"]:
                            msg += " ⏸️ 暂停"
                        else:
                            msg += " ⏹️ 停止"
                        action_queue.put((["idle"], msg))
                    else:
                        action_queue.put((["idle"], "📭 没有剧本"))
                    last_recognized_text = ""
                    recognized_text_timer = 0
                    return
            except Exception as e:
                print(f"[剧本错误] {e}")
                action_queue.put((["idle"], f"❌ 操作失败: {str(e)[:30]}"))
                last_recognized_text = ""
                recognized_text_timer = 0
                return

    # ========== 2. 原有 Agent 处理 ==========
    if not dh_system:
        print("[警告] Agent 系统未初始化，无法处理输入。")
        action_queue.put((['idle'], "Agent系统似乎出了一些问题。"))
        return

    stop_other_features()          # 问答优先：打断正在播放的剧本/视频/语音
    last_question_time = time.time()
    pending_question = True

    try:

        acts, reply = asyncio.run(dh_system.process_input(text))
        elapsed = time.time() - last_question_time
        print(f"[Agent] 输入: '{text}' -> 回复: '{reply}' | 动作: {acts}")
        print(f"[计时] 提问到获取回复耗时: {elapsed:.2f} 秒")

        if not isinstance(acts, list):
            print(f"[警告] Agent 返回的动作不是列表: {acts}, 使用 'idle' 代替。")
            acts = ['idle']
        if not acts:
             acts = ['idle']

        if my_seq != question_seq:
            print("[中止] 该回答已被停止或新提问打断，丢弃")
            return
        action_queue.put((acts, reply))

        last_recognized_text = ""
        recognized_text_timer = 0

    except Exception as e:
        print(f"[Agent] 调用失败: {e}")
        action_queue.put((['idle'], "抱歉，我处理时遇到点麻烦。"))

def audio_callback(indata: np.ndarray, frames: int, time_info, status):

    if status:
        print(f"[录音警告] {status}")
    if is_recording:
        audio_buffers.append(indata.copy())

def recognize_and_enqueue(pcm_data: np.ndarray):

    global last_recognized_text, recognized_text_timer
    if not vosk_model:
        print("[警告] Vosk 模型未加载，无法进行语音识别。")
        last_recognized_text = "语音识别不可用"
        recognized_text_timer = RECOGNIZED_TEXT_DURATION # 显示消息
        return

    try:
        print(f"开始识别 {len(pcm_data) / REC_FS:.2f} 秒的音频...")
        recognizer = KaldiRecognizer(vosk_model, REC_FS)
        recognizer.AcceptWaveform(pcm_data.tobytes())
        result_json = recognizer.FinalResult()
        res = json.loads(result_json)
        recognized_text = res.get("text", "").strip()

        print(f"[Vosk 离线识别] 结果: '{recognized_text if recognized_text else '<无结果>'}'")

        last_recognized_text = recognized_text if recognized_text else "未识别到内容"
        recognized_text_timer = RECOGNIZED_TEXT_DURATION

        if recognized_text:

            process_and_enqueue(recognized_text)
        # else:
            # action_queue.put((['thinking'], ""))

    except json.JSONDecodeError as e:
        print(f"[识别错误] 无法解析 Vosk 返回的 JSON: {result_json} - {e}")
        last_recognized_text = "识别结果解析错误"
        recognized_text_timer = RECOGNIZED_TEXT_DURATION
    except Exception as e:
        print(f"[识别失败] 处理音频时发生错误: {e}")
        last_recognized_text = "识别过程出错"
        recognized_text_timer = RECOGNIZED_TEXT_DURATION

def toggle_recording():
    global is_recording, rec_stream, audio_buffers, last_recognized_text, recognized_text_timer
    if not vosk_model:
        print("[警告] Vosk 模型未加载，无法启动录音。")
        return

    if not is_recording:
        print("[录音] 开始...")
        audio_buffers = []
        try:
            rec_stream = sd.InputStream(
                samplerate=REC_FS,
                channels=REC_CHANNELS,
                blocksize=REC_BLOCKSIZE,
                dtype="int16",
                callback=audio_callback
            )
            rec_stream.start()
            is_recording = True
            print("[录音] 录音流已启动。按 'Space' 停止。")
        except sd.PortAudioError as e:
             print(f"[录音错误] PortAudio 错误: {e}")
             print("请检查音频设备是否连接/可用，或尝试重启程序。")
             is_recording = False
             rec_stream = None
        except Exception as e:
            print(f"[录音错误] 无法启动录音流: {e}")
            is_recording = False
            rec_stream = None
    else:
        print("[录音] 停止...")
        is_recording = False
        if rec_stream:
            try:
                rec_stream.stop()
                rec_stream.close()
                print("[录音] 录音流已停止并关闭。")
            except Exception as e:
                print(f"[录音错误] 关闭录音流时出错: {e}")
            finally:
                 rec_stream = None

        if not audio_buffers:
            print("[录音] 未录制到有效音频数据。")
            last_recognized_text = "未录到声音"
            recognized_text_timer = RECOGNIZED_TEXT_DURATION // 2
            return

        try:
            full_audio_data = np.concatenate(audio_buffers, axis=0)
            audio_buffers = []

            print(f"[录音] 音频数据合并完毕 ({len(full_audio_data)} 样本), 准备识别...")

            threading.Thread(
                target=recognize_and_enqueue,
                args=(full_audio_data,),
                daemon=True
            ).start()

        except ValueError as e:
            print(f"[录音处理错误] 合并音频块失败 (可能为空或格式错误): {e}")
            audio_buffers = []
        except Exception as e:
            print(f"[录音处理错误] 处理音频数据时发生未知错误: {e}")
            audio_buffers = []


def load_frames(json_list):
    frames = []
    init_joints = {}
    first = True
    w = h = 0

    for p_str in json_list:
        p = Path(p_str)
        if not p.exists():
            print(f"[WARN] 动作文件未找到: {p_str}")
            continue

        try:
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"[WARN] 无法读取或解析动作文件 {p_str}: {e}")
            continue

        cur = data.get('frames', [])
        if not cur:

            print(f"[WARN] 动作文件 {p_str} 没有帧数据。")
            continue

        video_info = data.get('video_info', {})
        w, h = video_info.get('resolution', (w, h))

        for fr in cur:
            j = fr.get('joints', {})
            if pg_base.uses_joint_fix():
                if 'right_elbow' in j:     j['right_elbow']['y'] += 15
                if 'left_elbow'  in j:     j['left_elbow']['y']  += 15
                if 'right_shoulder' in j and 'left_shoulder' in j:
                    mid = (j['right_shoulder']['x'] + j['left_shoulder']['x']) / 2
                    j['right_shoulder']['x'], j['left_shoulder']['x'] = mid - 30, mid
                    j['left_shoulder']['x'] = mid

        if first:
            first_frame = cur[0] if cur else {}
            pelvis = first_frame.get('joints', {}).get('pelvis')

            if pelvis:
                offx, offy = w//2 - pelvis['x'], h//2 - pelvis['y']
                for fr in cur:
                     for pos in fr.get('joints', {}).values():
                        pos['x'] += offx; pos['y'] += offy
                first_frame_joints_centered = cur[0].get('joints', {})
                for name,pos in first_frame_joints_centered.items():
                    init_joints[name] = {'x':pos['x'],'y':pos['y']}
                first = False

            else:
                print(f"[WARN] 首段动作文件 {p_str} 的第一帧没有 'pelvis' 关节，无法居中和记录基线。后续拼接可能受影响。")
                first = False

        else:

            last_pelvis = frames[-1].get('joints', {}).get('pelvis') if frames else None
            start_pelvis = cur[0].get('joints', {}).get('pelvis')

            if last_pelvis and start_pelvis:
                dx, dy = last_pelvis['x']-start_pelvis['x'], last_pelvis['y']-start_pelvis['y']
                for fr in cur:
                    for pos in fr.get('joints', {}).values():
                        pos['x'] += dx; pos['y'] += dy
            elif frames:
                 print(f"[WARN] 动作文件 {p_str} 无法与上一段对齐：缺少骨盆关节信息。")

        frames.extend(cur)

    if not init_joints and frames:
         first_valid_joints = next((fr.get('joints', {}) for fr in frames if fr.get('joints')), None)
         if first_valid_joints:
             for name,pos in first_valid_joints.items():
                 init_joints[name] = {'x':pos['x'],'y':pos['y']}
             print("[WARN] 未能使用骨盆关节设置初始基线 (init_joints)，使用首个有效帧的关节作为近似基线。动画定位和缩放可能不准确。")
         else:
              print("[ERROR] 无法确定初始关节基线。请检查动作文件是否包含关节数据。")


    return frames, init_joints, w, h

def _mirror_clip_motion(frames):
    """把整段片段（待机或动作）的水平位移取反，供镜像朝向的人偶使用。

    以片段首帧骨盆 x 为轴，逐帧把全部关节的 x 平移 -2Δ(t)（Δ 为该帧骨盆相对首帧的位移）。
    片段内部「关节相对骨盆」的形态完全不变，所以渲染时的整体镜像照旧负责翻转朝向；
    只有骨盆的行进轨迹被反向——否则镜像人偶做「向前走」会朝背面走（倒着走）。
    """
    if not frames:
        return
    first = frames[0].get('joints', {}).get('pelvis')
    if not first:
        return
    x0 = first['x']
    prev_dx = 0.0
    for fr in frames:
        joints = fr.get('joints', {})
        pelvis = joints.get('pelvis')
        dx = 2.0 * (pelvis['x'] - x0) if pelvis else prev_dx
        prev_dx = dx
        if not dx:
            continue
        for pos in joints.values():
            if pos:
                pos['x'] -= dx


class Puppet:
    """单个人偶状态机：负责 idle 循环、动作播放、位移与手势/文本队列。

    把原先 pygame_loop 里的一套扁平状态变量（state/idle_idx/action_frames/
    current_offset/last_pelvis_abs/action_queue）封装成实例，每个人偶独立一份，
    从而实现左右手分别操控两个人偶。
    """

    def __init__(self, name, mats, cfg, idle_file, x_shift=0.0, q=None, motion_mirror=False):
        self.name = name
        self.mats = mats                # 该人偶的角色素材
        self.cfg = cfg                  # 该人偶的渲染配置快照（枢轴/旋转/镜像）
        self.x_shift = x_shift          # 起始站位水平偏移（左偶偏左/右偶偏右）
        self.center_reset = CENTER_RESET_ACTIONS
        self.motion_mirror = bool(motion_mirror)  # 渲染朝向为镜像时，动作的水平位移需一并取反

        frames, init_joints, fw, fh = load_frames([str(idle_file)])
        if not frames:
            raise ValueError(f"{name}: Idle 动画加载失败")
        if self.motion_mirror:
            _mirror_clip_motion(frames)   # 首帧 Δ=0，不影响 init_joints 基线
        self.idle_frames = frames
        self.init_joints_idle = init_joints
        self.center_x, self.center_y = fw // 2, fh // 2

        self.state = 'idle'
        self.idle_idx = 0
        self.action_idx = 0
        self.action_frames = []
        self.action_initial_joints = {}
        self.json_list = []
        self.render_action = None  # [KICK] 当前动作名，渲染层据此做踢腿重映射
        self.queue = q if q is not None else queue.Queue()

        initial_pelvis = init_joints.get('pelvis', {'x': self.center_x, 'y': self.center_y})
        self.initial_offset = {
            'x': self.center_x + self.x_shift - initial_pelvis['x'],
            'y': self.center_y - initial_pelvis['y'],
        }
        self.current_offset = {'x': self.initial_offset['x'], 'y': self.initial_offset['y']}
        self.last_pelvis_abs = {'x': self.center_x + self.x_shift, 'y': self.center_y}
        # [拖拽] 独立平移层：手部位移驱动的角色平移（与动作播放解耦）
        self.drag_offset = {'x': 0.0, 'y': 0.0}
        self.drag_target = {'x': 0.0, 'y': 0.0}
        self._drag_lock = threading.Lock()  # 手势线程与主线程共享 drag_* 的互斥锁

    def nudge(self, dx, dy=0.0):  # [拖拽]
        """累加目标拖拽位移（由手势线程调用，线程安全）。"""
        with self._drag_lock:
            self.drag_target['x'] += dx
            self.drag_target['y'] += dy

    def shift_drag(self, dx):  # [拖拽]
        """整体平移拖拽偏移与目标（由主线程间距约束调用，线程安全）。"""
        with self._drag_lock:
            self.drag_offset['x'] += dx
            self.drag_target['x'] += dx

    def effective_x(self):  # [拖拽]
        """当前渲染用的骨盆绝对横坐标（含拖拽偏移）。"""
        with self._drag_lock:
            return self.last_pelvis_abs['x'] + self.drag_offset['x']

    def reset_pose(self):
        """停止按钮：清空动作与拖拽，回到初始站位。"""
        self.state = 'idle'
        self.action_frames = []
        self.json_list = []
        self.action_idx = 0
        self.action_initial_joints = {}
        self.current_offset = dict(self.initial_offset)
        self.last_pelvis_abs = {'x': self.center_x + self.x_shift, 'y': self.center_y}
        with self._drag_lock:
            self.drag_offset = {'x': 0.0, 'y': 0.0}
            self.drag_target = {'x': 0.0, 'y': 0.0}

    def _dequeue_action(self, w, h):
        """空闲时若有请求则加载动作进入 action 状态。返回 (是否处理, 回复文本)。

        w/h 为窗口尺寸，用于动作结束后的位置钳制（不能用动作文件的 resolution：
        动作 JSON 是 720x1280 的素材尺寸，用它钳制会把人物卡在窗口中心/边缘）。
        """
        if self.state != 'idle' or self.queue.empty():
            return False, None
        acts, reply = self.queue.get()
        json_list = []
        valid_action_found = False
        for action_name in acts:
            action_name_clean = action_name.strip().strip("'\"")
            p = Path(action_name_clean)
            potential_path = None
            if p.suffix.lower() == '.json':
                potential_path = p
            elif p.parent != Path('.'):
                potential_path = p.with_suffix('.json')
            else:
                direct_path = Path(f"actions/{action_name_clean}.json")
                if direct_path.exists():
                    potential_path = direct_path
                else:
                    mapped_filename = ACTION_MAP.get(action_name_clean)
                    if mapped_filename:
                        mapped_path = Path(f"actions/{mapped_filename}.json")
                        if mapped_path.exists():
                            potential_path = mapped_path
                        else:
                            print(f"[警告] 动作 '{action_name_clean}' 映射的文件未找到: {mapped_path}")
                    else:
                        print(f"[警告] 动作 '{action_name_clean}' 既不是有效文件名也不是映射名。")
            if potential_path and potential_path.exists():
                json_list.append(str(potential_path))
                valid_action_found = True
            elif potential_path:
                print(f"[警告] 构造的动作文件路径不存在: {potential_path}")

        if json_list and valid_action_found:
            print(f"[{self.name}] 加载新动作序列: {json_list}")
            loaded_frames, first_segment_joints_original, loaded_w, loaded_h = load_frames(json_list)
            if loaded_frames:
                if self.motion_mirror:
                    _mirror_clip_motion(loaded_frames)   # 镜像偶：位移取反（首帧基线不受影响）
                self.action_frames = loaded_frames
                self.action_initial_joints = first_segment_joints_original
                raw_start_pelvis = self.action_initial_joints.get(
                    'pelvis', {'x': loaded_w // 2, 'y': loaded_h // 2})
                self.current_offset = {
                    'x': self.last_pelvis_abs['x'] - raw_start_pelvis['x'],
                    'y': self.last_pelvis_abs['y'] - raw_start_pelvis['y'],
                }
                last_frame_pelvis = self.action_frames[-1]['joints'].get('pelvis', raw_start_pelvis)
                self.last_pelvis_abs = {
                    'x': last_frame_pelvis['x'] + self.current_offset['x'],
                    'y': last_frame_pelvis['y'] + self.current_offset['y'],
                }
                # 边界钳制：动作结束后的位置不得越出窗口（防连续"向前走/向后走"走出界面）
                self.last_pelvis_abs['x'] = max(0.0, min(float(w), self.last_pelvis_abs['x']))
                self.last_pelvis_abs['y'] = max(0.0, min(float(h), self.last_pelvis_abs['y']))
                self.state = 'action'
                self.action_idx = 0
                self.json_list = json_list
                return True, reply
            print("[警告] 动作加载失败 (返回空帧列表)，返回 Idle。")
            self.state = 'idle'
            self.idle_idx = 0
            return True, "抱歉，我好像动不了了。"
        if not valid_action_found and acts:
            print("[警告] 请求的动作未找到有效的动作文件，保持 Idle。")
            return True, "我好像不认识这个动作。"
        return True, None

    def update(self, w, h):
        """推进一帧；返回 (渲染用关节字典 或 None, 新回复文本 或 None)。"""
        reply_new = None
        loaded, r = self._dequeue_action(w, h)
        if loaded and r:
            reply_new = r

        if self.state == 'idle':
            if not self.idle_frames:
                print(f"[错误] {self.name} Idle 帧丢失!")
                return None, reply_new
            frame_joint_data_original = self.idle_frames[self.idle_idx]['joints']
            self.idle_idx = (self.idle_idx + 1) % len(self.idle_frames)
            idle_origin_pelvis = self.init_joints_idle.get('pelvis', {'x': w // 2, 'y': h // 2})
            self.current_offset = {
                'x': self.last_pelvis_abs['x'] - idle_origin_pelvis['x'],
                'y': self.last_pelvis_abs['y'] - idle_origin_pelvis['y'],
            }
        elif self.state == 'action':
            if not self.action_frames:
                print(f"[错误] {self.name} Action 帧丢失!")
                self.state = 'idle'
                self.idle_idx = 0
                return None, reply_new
            frame_joint_data_original = self.action_frames[self.action_idx]['joints']
            self.action_idx += 1
            if self.action_idx >= len(self.action_frames):
                print(f"[{self.name}] Action 播放完毕，返回 Idle。")
                # 原地类动作结束后归位自身站位；移动类动作保持位置
                if self.json_list and Path(self.json_list[0]).stem in self.center_reset:
                    self.last_pelvis_abs = {'x': self.center_x + self.x_shift, 'y': self.center_y}
                self.state = 'idle'
                self.idle_idx = 0
        else:
            return None, reply_new

        # [拖拽] 缓动逼近目标位移，并把整体位置钳制在窗口内
        with self._drag_lock:
            for ax in ('x', 'y'):
                self.drag_offset[ax] += (self.drag_target[ax] - self.drag_offset[ax]) * DRAG_EASE
            margin = 50.0
            eff_x = self.last_pelvis_abs['x'] + self.drag_offset['x']
            if eff_x < margin:
                self.drag_offset['x'] += margin - eff_x
                self.drag_target['x'] = self.drag_offset['x']
            elif eff_x > w - margin:
                self.drag_offset['x'] -= eff_x - (w - margin)
                self.drag_target['x'] = self.drag_offset['x']
            eff_y = self.last_pelvis_abs['y'] + self.drag_offset['y']
            if eff_y < margin:
                self.drag_offset['y'] += margin - eff_y
                self.drag_target['y'] = self.drag_offset['y']
            elif eff_y > h - margin:
                self.drag_offset['y'] -= eff_y - (h - margin)
                self.drag_target['y'] = self.drag_offset['y']

        joints_for_render = {
            joint_name: {
                # 边界钳制：任何关节渲染坐标不越出窗口（含拖拽平移）
                'x': max(0.0, min(float(w), original_pos['x'] + self.current_offset['x'] + self.drag_offset['x'])),
                'y': max(0.0, min(float(h), original_pos['y'] + self.current_offset['y'] + self.drag_offset['y'])),
            }
            for joint_name, original_pos in frame_joint_data_original.items()
            if original_pos
        }
        # [KICK] 记录当前动作名（只在动作播放中），供渲染层判断是否做踢腿重映射
        self.render_action = (
            Path(self.json_list[0]).stem
            if (self.state == 'action' and self.json_list) else None
        )
        return joints_for_render, reply_new


def _fit_window_size(logical_w: int, logical_h: int) -> tuple:
    """按当前桌面大小算一个能完整放下的初始窗口尺寸（保持逻辑画布宽高比）。

    解决高分屏/小屏上窗口高出屏幕、底部按钮看不见的问题。
    """
    try:
        sizes = pygame.display.get_desktop_sizes()
        dw, dh = sizes[0] if sizes else (logical_w, logical_h)
    except Exception:  # noqa: BLE001
        dw, dh = logical_w, logical_h
    # 预留任务栏与窗口边框空间
    max_w = max(320, int(dw * 0.94))
    max_h = max(240, int(dh * 0.90))
    scale = min(max_w / logical_w, max_h / logical_h, 1.0)
    return max(320, int(logical_w * scale)), max(240, int(logical_h * scale))


def pygame_loop():
    global is_recording, last_recognized_text, recognized_text_timer, input_text, typing, ime_composing_text, \
           gesture_mode_on, gesture_recognizer, PUPPETS, CHAR_PUPPET0, CHAR_PUPPET1, \
           script_system, SCRIPT_MODE_ENABLED, share_system, SHARE_MODE_ENABLED, bgm_enabled

    print("初始化 Pygame 及资源...")
    pygame.init()
    initialize_fonts()
    print("加载 Idle 动作...")
    try:
        idle_action_key = "空闲" if "空闲" in ACTION_MAP else ("常态" if "常态" in ACTION_MAP else None)
        if not idle_action_key or not ACTION_MAP.get(idle_action_key):
            raise ValueError("ACTION_MAP 中未定义有效的 '空闲' 或 '常态' 动作。")

        idle_json_name = ACTION_MAP[idle_action_key]
        idle_file = Path(f"actions/{idle_json_name}.json")
        if not idle_file.exists():
            raise FileNotFoundError(f"必需的 Idle/Normal 动画文件未找到: {idle_file}")

        idle_frames, initial_joints_idle_original, w, h = load_frames([str(idle_file)])

        if not idle_frames:
            raise ValueError("Idle 动画加载失败或为空。")
        print(f"Idle 动作加载成功 ({len(idle_frames)} 帧), 窗口尺寸: {w}x{h}")

    except Exception as e:
        print(f"[致命错误] 加载 Idle 动画失败: {e}")
        pygame.quit()
        return

    print("设置 Pygame 窗口和资源...")
    # [窗口自适应] 逻辑画布固定为动作分辨率，所有布局按逻辑坐标绘制；
    # 实际窗口按桌面大小缩放到合适尺寸并允许自由拖拽，最后统一缩放呈现。
    logical_w, logical_h = w, h
    win_w, win_h = _fit_window_size(logical_w, logical_h)
    display = pygame.display.set_mode((win_w, win_h), pygame.RESIZABLE)
    pygame.display.set_caption("皮影数字人 (文本: Tab/回车, 语音: Space)")
    screen = pygame.Surface((logical_w, logical_h)).convert()
    print(f"[窗口] 逻辑画布 {logical_w}x{logical_h}，初始窗口 {win_w}x{win_h}（可拖拽缩放）")

    # [BGM] 空场伴奏：演出空场/观众体验时循环，正式内容开始时自动淡出
    bgm_player = None
    if HAS_BGM and stage_bgm is not None:
        try:
            bgm_player = stage_bgm.StageBGM()
            if bgm_player.available:
                print(f"[BGM] 空场伴奏已就绪：{stage_bgm.DEFAULT_AUDIO}（海宁文化馆录制）")
        except Exception as e:  # noqa: BLE001
            print(f"[BGM] 空场伴奏初始化失败，已跳过: {e}")
            bgm_player = None

    _scaled_surface = None
    _scaled_size = (0, 0)

    def _view_transform():
        """返回 (缩放比例, X偏移, Y偏移)：逻辑画布 -> 实际窗口的等比映射。"""
        dw, dh = display.get_size()
        scale = min(dw / logical_w, dh / logical_h)
        return scale, (dw - logical_w * scale) / 2.0, (dh - logical_h * scale) / 2.0

    def _to_logical(pos):
        """窗口坐标 -> 逻辑坐标（用于鼠标命中的反向映射）。"""
        scale, ox, oy = _view_transform()
        if scale <= 0:
            return pos
        return ((pos[0] - ox) / scale, (pos[1] - oy) / scale)

    def _logical_rect_to_window(rect):
        """逻辑矩形 -> 窗口矩形（用于输入法候选框定位）。"""
        scale, ox, oy = _view_transform()
        return pygame.Rect(int(ox + rect.x * scale), int(oy + rect.y * scale),
                           max(1, int(rect.w * scale)), max(1, int(rect.h * scale)))

    def _present():
        """把逻辑画布等比缩放并居中呈现到实际窗口（多余区域留黑边）。"""
        nonlocal _scaled_surface, _scaled_size
        dw, dh = display.get_size()
        scale = min(dw / logical_w, dh / logical_h)
        sw, sh = max(1, int(logical_w * scale)), max(1, int(logical_h * scale))
        if _scaled_surface is None or (sw, sh) != _scaled_size:
            _scaled_surface = pygame.Surface((sw, sh)).convert()
            _scaled_size = (sw, sh)
        if (sw, sh) == (logical_w, logical_h):
            _scaled_surface.blit(screen, (0, 0))
        else:
            pygame.transform.smoothscale(screen, (sw, sh), _scaled_surface)
        display.fill((0, 0, 0))
        display.blit(_scaled_surface, ((dw - sw) // 2, (dh - sh) // 2))
    _tk_root = None
    if HAS_TKINTER:
        try:
            _tk_root = tk.Tk()
            _tk_root.withdraw()
            print("剪贴板模块初始化成功 (tkinter)。")
        except Exception as e:
            print(f"[警告] 剪贴板模块初始化失败: {e}，粘贴功能将不可用。")
            _tk_root = None
    else:
        print("[警告] tkinter 不可用，粘贴功能将不可用。")
    try:
        bg_image_path = pg_base.BACKGROUND_IMG
        background_img = pygame.image.load(bg_image_path).convert()
        bg = pygame.transform.smoothscale(background_img, (w, h))
        print(f"背景图片 '{bg_image_path}' 加载成功。")
    except Exception as e:
        print(f"[警告] 加载背景图片失败 ({pg_base.BACKGROUND_IMG}): {e}。使用纯色背景。")
        bg = pygame.Surface((w, h))
        bg.fill(UI_COLORS["background"])

    try:
        mats = pg_base.load_materials()
        print(f"加载角色素材.{mats}")
        pg_base.finalize_pivots(mats)
        print(f"角色素材加载成功 ({len(mats)} 个部位)。")
    except Exception as e:
        print(f"[错误] 加载角色素材失败: {e}")
        mats = {}

    clock = pygame.time.Clock()


    # ===== 底部区域：左侧输入框 + 右侧按钮列（两者同高同底，一起撑满底部）=====
    input_box_img = None
    input_box_rect = None
    input_box_x = input_box_y = input_box_width = input_box_height = 0
    input_text_rect = None
    input_text_offset_x = 15
    input_text_offset_y = 16  # 文本靠上，底部留给按钮行
    toolbar_h = 52
    toolbar_y = toolbar_x = 0
    gesture_button_rect = script_button_rect = share_button_rect = stop_button_rect = None
    bgm_button_rect = None
    button_rect = cur_char_rect = left_char_rect = right_char_rect = None

    def _layout_bottom():
        """重算底部：左侧整块输入框；右侧「人偶」叠在「文字模式」正上方，文字模式与框等高。"""
        nonlocal input_box_img, input_box_rect, input_box_x, input_box_y, \
            input_box_width, input_box_height, input_text_rect, \
            toolbar_y, toolbar_x, gesture_button_rect, script_button_rect, \
            share_button_rect, stop_button_rect, bgm_button_rect, button_rect, cur_char_rect, left_char_rect, right_char_rect
        margin = 15
        gap = 12
        pad_x = 18
        input_box_height = 120
        input_box_y = h - input_box_height - margin

        # 右侧按钮：文字模式与输入框等高且右对齐；人偶/左右偶叠在其正上方
        btn_w = 120
        char_h = 48
        btn_x = w - margin - btn_w
        button_rect = pygame.Rect(btn_x, input_box_y, btn_w, input_box_height)
        if gesture_mode_on:
            left_char_rect = pygame.Rect(btn_x, input_box_y - 2 * (char_h + gap), btn_w, char_h)
            right_char_rect = pygame.Rect(btn_x, input_box_y - (char_h + gap), btn_w, char_h)
            cur_char_rect = left_char_rect
        else:
            cur_char_rect = pygame.Rect(btn_x, input_box_y - char_h - gap, btn_w, char_h)
            left_char_rect = cur_char_rect
            right_char_rect = cur_char_rect

        # 输入框：从左边距排到按钮列左侧
        input_box_x = margin
        input_box_width = btn_x - gap - input_box_x
        input_box_img = create_input_box(input_box_width, input_box_height)
        input_box_rect = pygame.Rect(input_box_x, input_box_y, input_box_width, input_box_height)

        # 框内左下角工具栏：手势交互 / 剧本 / 分享卡片
        toolbar_h_ = 52
        toolbar_y = input_box_y + input_box_height - toolbar_h_ - 12
        toolbar_x = input_box_x + 15
        _gw = input_font.get_rect("手势交互：关").width + pad_x * 2
        _sw = input_font.get_rect("剧本：关").width + pad_x * 2
        _shw = input_font.get_rect("分享卡片").width + pad_x * 2
        gesture_button_rect = pygame.Rect(toolbar_x, toolbar_y, _gw, toolbar_h_)
        script_button_rect = pygame.Rect(gesture_button_rect.right + gap, toolbar_y, _sw, toolbar_h_)
        share_button_rect = pygame.Rect(script_button_rect.right + gap, toolbar_y, _shw, toolbar_h_)
        _stopw = input_font.get_rect("停止").width + pad_x * 2
        stop_button_rect = pygame.Rect(share_button_rect.right + gap, toolbar_y, _stopw, toolbar_h_)
        # 空场伴奏开关：正方形图标，紧跟在“停止”右侧
        bgm_button_rect = pygame.Rect(stop_button_rect.right + gap, toolbar_y, toolbar_h_, toolbar_h_)

        # 文本输入可点击区域（不含底部按钮行），避免点按钮时误激活输入
        input_text_rect = pygame.Rect(input_box_x, input_box_y, input_box_width,
                                      input_box_height - toolbar_h_ - 12)

    _layout_bottom()

    output_box_img = None
    output_box_rect = None
    output_visible = False
    output_fade_timer = 0

    # 共享输入/回复状态
    text_reply = ""
    typing = False
    input_text = ""
    ime_composing_text = ""  # 输入法组合中的拼音（TEXTEDITING 事件）
    input_mode = "text" # 当前输入模式: "text" 或 "voice"

    def _clean_reply(text):
        """清理回复中的 markdown 特殊符号，只保留可读正文（UI 与 TTS 共用）。"""
        if not text:
            return text or ""
        t = str(text)
        t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)   # 图片 ![alt](url)
        t = re.sub(r"\[([^\[\]]*)\]\([^)]*\)", r"\1", t)  # 链接 [text](url) -> text
        t = re.sub(r"`{1,3}", "", t)                   # 代码块/行内代码 `或者 ```
        t = re.sub(r"\*{1,3}", "", t)                  # *** / ** / *
        t = re.sub(r"[_~]+", "", t)                    # 下划线 / 删除线 ~~
        t = re.sub(r"^#{1,6}\s*", "", t, flags=re.M)   # 标题 # ## ###
        t = re.sub(r"^>\s*", "", t, flags=re.M)        # 引用 >
        t = re.sub(r"[|\[\]]", "", t)                  # 表格分隔符与残留括号
        return t.strip()

    def _show_reply(reply):
        """某个人偶触发的回复：更新共享输出框并 TTS 播报。"""
        nonlocal text_reply, output_visible, output_fade_timer
        global pending_question
        pending_question = False      # 回复已到达，结束"正在思考"状态
        reply = _clean_reply(reply)
        text_reply = reply or ""
        output_visible = bool(reply)
        output_fade_timer = OUTPUT_FADE_DURATION if output_visible else 0
        if reply and reply.strip() and HAS_TTS and tts_engine is not None:
            if _video_sound_on():
                print("[TTS] 视频播放中，仅显示文字，跳过语音（避免与视频声音重叠）")
            else:
                tts_engine.speak_async(reply)  # TTS 播报

    def _submit_text(text):
        """提交问题：清空上一条回答，再后台处理（思考提示由 pending_question 驱动）。"""
        nonlocal text_reply, output_visible, output_fade_timer
        global question_seq
        text = text.strip()
        if not text:
            return
        question_seq += 1          # 新提问让上一条在途回答失效
        text_reply = ""
        output_visible = False
        output_fade_timer = 0
        threading.Thread(target=process_and_enqueue, args=(text,), daemon=True).start()

    def _stop_all():
        """停止按钮：中断剧本/视频/语音/待答问题，人偶回初始站位，清空输入与回答。"""
        nonlocal text_reply, output_visible, output_fade_timer
        global input_text, typing, ime_composing_text
        reset_runtime_state()
        text_reply = ""
        output_visible = False
        output_fade_timer = 0
        input_text = ""
        ime_composing_text = ""
        if typing:
            typing = False
            pygame.key.stop_text_input()
        print("[停止] 已中断当前功能并回到初始状态")

    # [拖拽] 连续手部位置 -> 对应偶平移（相对增量，松手保持）
    hand_prev = {"left": None, "right": None, "unknown": None}

    def _on_hand_move(hand, x, y):
        """手势线程每帧回调：手相对上一帧的横向位移 -> 对应偶平移。"""
        if x is None:
            hand_prev[hand] = None
            return
        prev = hand_prev.get(hand)
        hand_prev[hand] = (x, y)
        if prev is None or not PUPPETS:
            return
        dx = x - prev[0]
        if abs(dx) < MOVE_DEAD_ZONE:
            return
        # 手别分派：left/right 按手；unknown 按手在画面的左右位置分派
        if hand == "right" and len(PUPPETS) > 1:
            pup = PUPPETS[1]
        elif hand == "left":
            pup = PUPPETS[0]
        elif len(PUPPETS) > 1 and x >= 0.5:
            pup = PUPPETS[1]
        else:
            pup = PUPPETS[0]
        pup.nudge(dx * MOVE_SCALE, 0.0)

    # ===== 人偶布局 =====
    # 常态：单个皮影（居中）用于问答；开启手势控制：左右两只对峙
    gap_x = max(60, min(360, w // 4))
    CHAR_PUPPET0, CHAR_PUPPET1 = load_puppet_roles()  # [角色切换] 读取持久化角色
    puppet0 = None
    puppet1 = None
    mirror1 = True

    def _build_puppet(name, side, x_shift, motion_mirror=None):  # [角色切换]
        """按角色名构建一只偶；side=0 左 / 1 右。失败返回 None。

        motion_mirror: 该偶渲染朝向是否为镜像；None 表示按角色自身配置判断（左偶）。
        镜像朝向的偶需把动作的水平位移一并取反，否则"向前走"会背对对手走。
        """
        setup = pg_base.character_setup(name)
        if not setup:
            return None
        if motion_mirror is None:
            motion_mirror = bool(setup[1].get("scene_mirror", False))
        q = action_queue if side == 0 else action_queue_right
        return Puppet(f"{'左' if side == 0 else '右'}/{name}", setup[0], setup[1],
                      idle_file=idle_file, x_shift=x_shift, q=q,
                      motion_mirror=motion_mirror)

    def _build_layout():  # [布局]
        """按 gesture_mode_on / 视频播放 / 剧本播放 重建布局：常态=单个居中；双人=左右对峙。"""
        nonlocal puppet0, puppet1, mirror1
        va = _video_active()
        sa = _script_active()
        dual_mode = gesture_mode_on or va or sa
        # 视频播放时把两偶拉得更开，避免挡住中间的视频画面
        gap = max(gap_x, int(w * 0.36)) if va else gap_x
        left_shift = -gap if dual_mode else 0.0
        p0 = _build_puppet(CHAR_PUPPET0, 0, left_shift)
        if p0 is None:
            print(f"[布局] 左偶角色 '{CHAR_PUPPET0}' 不可用，回退当前角色")
            fb_cfg = pg_base.capture_config()
            p0 = Puppet(f"左/{CHAR_PUPPET0}", mats, fb_cfg,
                        idle_file=idle_file, x_shift=left_shift, q=action_queue,
                        motion_mirror=bool(fb_cfg.get("scene_mirror", False)))
        puppet0 = p0
        puppet1 = None
        if dual_mode:
            p1 = _build_puppet(CHAR_PUPPET1, 1, gap,
                               motion_mirror=not bool(puppet0.cfg["scene_mirror"]))
            if p1 is None:
                print(f"[布局] 警告: 未找到右偶角色 '{CHAR_PUPPET1}'，仅单人偶运行。")
            puppet1 = p1
        PUPPETS.clear()
        PUPPETS.append(puppet0)
        if puppet1:
            PUPPETS.append(puppet1)
        mirror1 = not bool(puppet0.cfg["scene_mirror"])
        # [剧本集成] 同步右偶可用性，供播放器决定台词是否投右队列
        if script_system is not None:
            script_system.player.has_right_puppet = puppet1 is not None
        print(f"[布局] {'双人偶' if dual_mode else '单人偶（常态）'}，左偶={CHAR_PUPPET0}")

    def _cycle_character(side, delta):  # [角色切换]
        """在 CHARACTERS 中循环切换指定侧的偶角色，并持久化后重建布局。"""
        global CHAR_PUPPET0, CHAR_PUPPET1
        chars = list(getattr(pg_base, "CHARACTERS", []))
        if not chars:
            print("[角色切换] 无可用角色列表")
            return
        cur = CHAR_PUPPET0 if side == 0 else CHAR_PUPPET1
        idx = (chars.index(cur) + delta) % len(chars) if cur in chars else 0
        name = chars[idx]
        if side == 0:
            CHAR_PUPPET0 = name
        else:
            CHAR_PUPPET1 = name
        save_puppet_roles(CHAR_PUPPET0, CHAR_PUPPET1)
        _build_layout()
        print(f"[角色切换] {'左' if side == 0 else '右'}偶 -> {name}")

    try:
        _build_layout()
    except Exception as e:
        print(f"[布局] 初始化失败: {e}")
        pygame.quit()
        return

    print("初始化完成，开始主循环...")
    running = True
    card_disp = None  # [分享卡片] 弹层缩放缓存 (原始Surface, 缩放后Surface, x, y)
    video_was_active = False  # [视频集成] 用于检测视频开始/结束并切换布局
    script_was_active = False  # [剧本集成] 用于检测剧本开始/结束并切换布局
    # [GESTURE] 摄像头预览位置与拖动状态
    preview_pos = [10, 60]
    preview_rect = None
    preview_dragging = False
    preview_drag_offset = (0, 0)
    while running:
        delta_time = clock.tick(FPS) / 1000.0 

        # [GESTURE] 手势线程若已结束（摄像头异常/被关闭），自动关闭开关，保持 UI 同步
        if gesture_mode_on and gesture_recognizer is not None and not gesture_recognizer.is_alive():
            print("[GESTURE] 手势线程已结束，自动关闭手势控制。")
            gesture_mode_on = False
            gesture_recognizer = None

        # [视频集成] 视频开始/结束 → 重建布局（播放时双偶拉开，结束后恢复）
        _va = _video_active()
        if _va != video_was_active:
            video_was_active = _va
            try:
                _build_layout()
            except Exception as e:  # noqa: BLE001
                print(f"[布局] 视频模式切换失败: {e}")

        # [剧本集成] 剧本开始/结束 → 重建布局（播放时切双偶，左右角色分演）
        _sa = _script_active()
        if _sa != script_was_active:
            script_was_active = _sa
            try:
                _build_layout()
            except Exception as e:  # noqa: BLE001
                print(f"[布局] 剧本模式切换失败: {e}")

        for ev in pygame.event.get():
            # [窗口自适应] 用户拖拽改变窗口大小：重建显示表面（逻辑画布不变）
            if ev.type == pygame.VIDEORESIZE:
                try:
                    display = pygame.display.set_mode((max(320, ev.w), max(240, ev.h)),
                                                      pygame.RESIZABLE)
                except Exception as e:  # noqa: BLE001
                    print(f"[窗口] 调整大小失败: {e}")
                continue

            # [窗口自适应] 鼠标事件坐标从窗口映射回逻辑画布，保证按钮命中正确
            if ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION) \
                    and hasattr(ev, "pos"):
                try:
                    ev.pos = _to_logical(ev.pos)
                except Exception:  # noqa: BLE001
                    pass

            # [分享卡片] 弹层打开时，点击或 Esc 关闭并吞掉该事件
            if share_system is not None and share_system.is_card_visible():
                if (ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE) or \
                   (ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1):
                    share_system.close_card()
                    continue

            if ev.type == pygame.QUIT:
                running = False
                break

            # [GESTURE] 摄像头预览拖动（最优先处理，避免误触其它按钮）
            if gesture_mode_on and preview_rect is not None:
                if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 and preview_rect.collidepoint(ev.pos):
                    preview_dragging = True
                    preview_drag_offset = (ev.pos[0] - preview_pos[0], ev.pos[1] - preview_pos[1])
                    continue
                if ev.type == pygame.MOUSEMOTION and preview_dragging:
                    preview_pos[0] = ev.pos[0] - preview_drag_offset[0]
                    preview_pos[1] = ev.pos[1] - preview_drag_offset[1]
                    continue
                if ev.type == pygame.MOUSEBUTTONUP and ev.button == 1 and preview_dragging:
                    preview_dragging = False
                    continue

            if ev.type == pygame.MOUSEBUTTONDOWN:
                # [视频集成] 视频控件优先处理（暂停/快退/快进/退出/进度条点击）
                if _video_active() and video_system is not None and hasattr(video_system, "handle_mouse_click"):
                    try:
                        if video_system.handle_mouse_click(ev.pos, w, h):
                            continue
                    except Exception as e:  # noqa: BLE001
                        print(f"[视频] 鼠标处理异常: {e}")

                if ev.button == 1:
                    if input_mode == "text":
                        if input_text_rect.collidepoint(ev.pos):
                            if not typing:
                                typing = True
                                input_text = ""
                                ime_composing_text = ""
                                pygame.key.start_text_input()
                                pygame.key.set_text_input_rect(_logical_rect_to_window(input_box_rect))  # 候选框跟随输入框
                                print("输入框已激活。")
                        elif typing:
                            typing = False
                            pygame.key.stop_text_input()
                            ime_composing_text = ""
                            print("输入框已取消激活。")


                    if button_rect.collidepoint(ev.pos):
                        if input_mode == "text":
                            input_mode = "voice"
                            print("切换到 -> 语音模式")
                            if typing:
                                typing = False
                                pygame.key.stop_text_input()
                                input_text = ""
                                ime_composing_text = ""
                        else:
                            input_mode = "text"
                            print("切换到 -> 文字模式")
                            if is_recording:
                                toggle_recording()
                        last_recognized_text = ""
                        recognized_text_timer = 0

                    if gesture_button_rect.collidepoint(ev.pos):  # [GESTURE]
                        gesture_mode_on = not gesture_mode_on
                        for _k in hand_prev:  # 切换时清空手部历史，避免重新开启瞬间跳变
                            hand_prev[_k] = None
                        if gesture_mode_on:
                            if HAS_GESTURE:
                                _build_layout()  # 切到双人偶对峙布局
                                if gesture_recognizer is None:
                                    # 预览窗口涉及 OpenCV GUI，默认关闭以避免子线程调用 imshow
                                    gesture_recognizer = GestureRecognizer(
                                        callback=enqueue_gesture_action,
                                        move_callback=_on_hand_move,
                                        show_preview=True)  # 显示摄像头预览窗口（q/Esc 关闭）
                                    if not gesture_recognizer.start():
                                        gesture_recognizer = None
                                        gesture_mode_on = False
                                        _build_layout()  # 启动失败，回退常态单人居中
                            else:
                                gesture_mode_on = False
                                print("[GESTURE] 手势功能启动失败（依赖缺失或模型不可用）")
                        else:
                            if gesture_recognizer is not None:
                                gesture_recognizer.stop()
                                gesture_recognizer = None
                            _build_layout()  # 回到常态：单个居中人偶
                        print(f"[GESTURE] 手势控制: {'开启' if gesture_mode_on else '关闭'}")
                        _layout_bottom()  # 重排右侧按钮列与输入框（手势模式多一行角色按钮）

                    # [角色切换] 常态=切换当前（单个）人偶；手势模式=左右偶分别切换（Shift+点击反向）
                    _rev = -1 if (pygame.key.get_mods() & pygame.KMOD_SHIFT) else 1
                    if gesture_mode_on:
                        if left_char_rect.collidepoint(ev.pos):
                            _cycle_character(0, _rev)
                        elif right_char_rect.collidepoint(ev.pos):
                            _cycle_character(1, _rev)
                    else:
                        if cur_char_rect.collidepoint(ev.pos):
                            _cycle_character(0, _rev)

                    # [剧本集成] 剧本模式切换按钮（底部按钮行）
                    if script_button_rect.collidepoint(ev.pos):
                        SCRIPT_MODE_ENABLED = not SCRIPT_MODE_ENABLED
                        mode_text = "启用" if SCRIPT_MODE_ENABLED else "禁用"
                        print(f"[剧本] 剧本模式已{mode_text}")
                        last_recognized_text = f"剧本模式已{mode_text}"
                        recognized_text_timer = RECOGNIZED_TEXT_DURATION

                    # [分享卡片] 按钮=开启/关闭分享功能（不直接生成；生成需说出关键词）
                    if share_button_rect.collidepoint(ev.pos):
                        if share_system is None:
                            print("[分享] 分享系统不可用")
                            last_recognized_text = "分享卡片功能不可用"
                        else:
                            SHARE_MODE_ENABLED = not SHARE_MODE_ENABLED
                            if SHARE_MODE_ENABLED:
                                last_recognized_text = "分享卡片已开启：说“生成分享卡片”即可制作"
                            else:
                                last_recognized_text = "分享卡片已关闭"
                            print(f"[分享] 分享功能已{'开启' if SHARE_MODE_ENABLED else '关闭'}")
                        recognized_text_timer = RECOGNIZED_TEXT_DURATION

                    # [停止] 一键中断：剧本/视频/语音/待答问题，回到初始状态
                    if stop_button_rect is not None and stop_button_rect.collidepoint(ev.pos):
                        _stop_all()

                    # [BGM] 背景伴奏开关（♪ 图标）
                    if bgm_button_rect is not None and bgm_button_rect.collidepoint(ev.pos):
                        bgm_enabled = not bgm_enabled
                        print(f"[BGM] 空场伴奏已{'开启' if bgm_enabled else '关闭'}")

            elif ev.type == pygame.KEYDOWN:
                # [视频集成] 视频控件优先处理（Esc 退出播放 / ←→ 快退快进）
                if _video_active() and video_system is not None and hasattr(video_system, "handle_key"):
                    try:
                        if video_system.handle_key(ev):
                            continue
                    except Exception as e:  # noqa: BLE001
                        print(f"[视频] 键盘处理异常: {e}")

                if input_mode == "voice" and ev.key == pygame.K_SPACE:
                    toggle_recording()

                elif input_mode == "text" and ev.key == pygame.K_TAB:
                     typing = not typing
                     if typing:
                         input_text = ""
                         ime_composing_text = ""
                         pygame.key.start_text_input()
                         pygame.key.set_text_input_rect(_logical_rect_to_window(input_box_rect))  # 候选框跟随输入框
                         print("输入框已激活 (Tab)。")
                     else:
                         pygame.key.stop_text_input()
                         ime_composing_text = ""
                         print("输入框已取消激活 (Tab)。")
                         if input_text.strip():
                             print(f"提交文本 (Tab): {input_text.strip()}")
                             _submit_text(input_text)
                         input_text = ""

                elif typing and input_mode == "text":
                    if ev.key == pygame.K_RETURN or ev.key == pygame.K_KP_ENTER:
                        print(f"提交文本 (Enter): {input_text.strip()}")
                        pygame.key.stop_text_input()
                        typing = False
                        ime_composing_text = ""
                        if input_text.strip():
                            _submit_text(input_text)
                        input_text = ""
                    elif ev.key == pygame.K_BACKSPACE:
                        input_text = input_text[:-1]
                    elif ev.key == pygame.K_ESCAPE:
                        print("取消输入 (Escape)。")
                        typing = False
                        pygame.key.stop_text_input()
                        input_text = ""
                        ime_composing_text = ""
                    elif ev.key == pygame.K_v and pygame.key.get_mods() & pygame.KMOD_CTRL:
                        try:
                            if _tk_root:
                                paste_text = _tk_root.clipboard_get()
                                if paste_text:
                                    paste_text = paste_text.replace("\r\n", "\n").replace("\r", "\n")
                                    input_text += paste_text
                                    print(f"粘贴文本: {paste_text[:50]}{'...' if len(paste_text) > 50 else ''}")
                            else:
                                print("[警告] 剪贴板未初始化，无法粘贴。")
                        except tk.TclError:
                            print("[警告] 剪贴板中没有文本内容。")
                        except Exception as e:
                            print(f"[警告] 粘贴失败: {e}")

            elif ev.type == pygame.TEXTINPUT and typing and input_mode == "text":
                input_text += ev.text

            elif ev.type == pygame.TEXTEDITING and typing and input_mode == "text":
                ime_composing_text = ev.text

        if not running: break

        # [BGM] 空场伴奏：无正式内容时循环，视频/剧本/播报/思考时淡出；♪ 图标可关闭
        if bgm_player is not None:
            bgm_player.update(bgm_enabled and _bgm_should_play(), delta_time)

        # 两人偶各自推进一帧状态机；触发回复时更新共享输出框并 TTS 播报
        j0 = j1 = None
        if puppet0:
            j0, r0 = puppet0.update(w, h)
            if r0:
                _show_reply(r0)
        if puppet1:
            j1, r1 = puppet1.update(w, h)
            if r1:
                _show_reply(r1)

        # [拖拽] 两偶最小间距约束：防止拖拽/动作导致两人偶重叠
        if puppet0 and puppet1:
            gap = puppet1.effective_x() - puppet0.effective_x()
            if gap < MIN_GAP:
                push = (MIN_GAP - gap) / 2.0
                puppet0.shift_drag(-push)
                puppet1.shift_drag(push)

        screen.blit(bg, (0, 0))

        # 按各自角色素材与渲染配置分别绘制两人偶（背景只画一次）
        if puppet0 and puppet0.mats and j0:
            pg_base.render(
                screen, bg, puppet0.mats,
                j0,
                puppet0.init_joints_idle,
                frame_idx=0,
                cfg=puppet0.cfg,
                draw_bg=False,
                action=puppet0.render_action,
            )
        if puppet1 and puppet1.mats and j1:
            pg_base.render(
                screen, bg, puppet1.mats,
                j1,
                puppet1.init_joints_idle,
                frame_idx=0,
                cfg=puppet1.cfg,
                draw_bg=False,
                mirror=mirror1,  # 与左偶反向→对峙
                action=puppet1.render_action,
            )

        # [视频集成] 渲染视频画面与控件（居中覆盖，两侧留给人偶）
        if _video_active() and video_system is not None and hasattr(video_system, "render"):
            try:
                video_system.render(screen, w, h, main_font, status_font, UI_COLORS)
            except Exception as e:  # noqa: BLE001
                print(f"[视频] 渲染失败: {e}")

        if output_visible and text_reply and not pending_question:
            max_text_width = w * 0.95
            padding_x = 20
            padding_y = 15
            line_spacing = 6

            wrapped_lines = []
            current_line = ""
            for char in text_reply:
                test_line = current_line + char
                rect = main_font.get_rect(test_line)
                if rect.width < max_text_width:
                    current_line = test_line
                else:
                    wrapped_lines.append(current_line)
                    current_line = char
            if current_line:
                wrapped_lines.append(current_line)

            text_rects = [main_font.get_rect(line) for line in wrapped_lines]
            total_text_height = sum(r.height for r in text_rects) + max(0, len(wrapped_lines) - 1) * line_spacing
            max_line_width = max(r.width for r in text_rects) if text_rects else 0

            output_box_width_needed = max_line_width + 2 * padding_x
            output_box_height_needed = total_text_height + 2 * padding_y

            final_output_box_width = min(max(output_box_width_needed, 150), w - 40)
            final_output_box_height = min(max(output_box_height_needed, 50), h // 2.5)
            final_output_box_x = (w - final_output_box_width) // 2
            final_output_box_y = 20
            output_box_rect = pygame.Rect(final_output_box_x, final_output_box_y, final_output_box_width, final_output_box_height)

            output_box_img = create_output_box(final_output_box_width, final_output_box_height)
            alpha = int(min(1.0, output_fade_timer / OUTPUT_FADE_DURATION) * UI_COLORS["output_bg"][3])
            output_box_img.set_alpha(alpha)

            screen.blit(output_box_img, output_box_rect.topleft)

            if alpha > 200:
                current_y = final_output_box_y + padding_y
                for i, line in enumerate(wrapped_lines):
                     main_font.render_to(screen,
                                         (final_output_box_x + padding_x, current_y),
                                         line,
                                         UI_COLORS["text"])
                     current_y += text_rects[i].height + line_spacing

        elif output_visible and output_fade_timer > 0 and not pending_question:
            output_fade_timer -= 1
            if output_box_img and output_box_rect:
                 alpha = int(min(1.0, output_fade_timer / OUTPUT_FADE_DURATION) * UI_COLORS["output_bg"][3])
                 output_box_img.set_alpha(alpha)
                 screen.blit(output_box_img, output_box_rect.topleft)
            if output_fade_timer <= 0:
                 output_visible = False
                 output_box_img = None
                 output_box_rect = None


        # ===== 思考中提示：提问后、回答出现前显示，避免误以为没反应 =====
        if pending_question:
            think_text = "正在思考" + "." * (int(time.time() * 2) % 4)
            think_rect = main_font.get_rect(think_text)
            think_w, think_h = think_rect.width + 44, think_rect.height + 26
            think_x, think_y = (w - think_w) // 2, 22
            think_img = create_output_box(think_w, think_h)
            screen.blit(think_img, (think_x, think_y))
            main_font.render_to(screen, (think_x + 22, think_y + 13), think_text, UI_COLORS["text"])

        # ===== 输入框：始终绘制（文本区 + 底部按钮行）=====
        screen.blit(input_box_img, input_box_rect.topleft)
        text_render_x = input_box_rect.x + input_text_offset_x
        text_render_y = input_box_rect.y + input_text_offset_y

        if input_mode == "text":
            if typing:
                input_font.render_to(screen, (text_render_x, text_render_y), input_text, UI_COLORS["text"])
                if ime_composing_text:
                    comp_x = text_render_x + input_font.get_rect(input_text).width
                    prev_underline = input_font.underline
                    input_font.underline = True
                    input_font.render_to(screen, (comp_x, text_render_y), ime_composing_text, UI_COLORS["text"])
                    input_font.underline = prev_underline
                if int(time.time() * 2) % 2 == 0:
                    text_surface, text_rect = input_font.render(input_text + ime_composing_text)
                    cursor_x = text_render_x + text_rect.width + 1
                    cursor_y1 = text_render_y
                    cursor_y2 = text_render_y + input_font.get_sized_height(18)
                    pygame.draw.line(screen, UI_COLORS["dark_red"], (cursor_x, cursor_y1), (cursor_x, cursor_y2), 2)
            elif not input_text:
                input_font.render_to(screen, (text_render_x, text_render_y), INPUT_HINT, UI_COLORS["hint_text"])
        else:  # 语音模式：文本区显示录音状态
            rec_indicator_x = text_render_x + 6
            rec_indicator_y = text_render_y + 12
            rec_color = UI_COLORS["recording_active"] if is_recording else UI_COLORS["recording_idle"]
            pygame.draw.circle(screen, rec_color, (rec_indicator_x, rec_indicator_y), 7)
            rec_status_text = "录音中..." if is_recording else "按 '空格' 开始录音"
            status_font.render_to(screen, (rec_indicator_x + 14, rec_indicator_y - 10),
                                  rec_status_text, rec_color)

        # 右侧：文字/语音模式按钮
        button_color = UI_COLORS["gold"] if input_mode == "voice" else UI_COLORS["button_text"]
        pygame.draw.rect(screen, button_color, button_rect, border_radius=8)
        button_text = "语音模式" if input_mode == "voice" else "文字模式"
        text_surf, text_rect = input_font.render(button_text, UI_COLORS["text_light"])
        text_rect.center = button_rect.center
        screen.blit(text_surf, text_rect)

        # 右侧：角色切换按钮（常态 1 个“人偶”；手势模式 左偶/右偶）
        if gesture_mode_on:
            for _rect, _side, _name in ((left_char_rect, 0, CHAR_PUPPET0),
                                        (right_char_rect, 1, CHAR_PUPPET1)):
                pygame.draw.rect(screen, UI_COLORS["gold"], _rect, border_radius=8)
                _disp = pg_base.CHAR_DISPLAY.get(_name, _name)
                _label = f"{'左' if _side == 0 else '右'}偶：{_disp}"
                _surf, _r = input_font.render(_label, UI_COLORS["button_text"])
                _r.center = _rect.center
                screen.blit(_surf, _r)
        else:
            pygame.draw.rect(screen, UI_COLORS["gold"], cur_char_rect, border_radius=8)
            _disp = pg_base.CHAR_DISPLAY.get(CHAR_PUPPET0, CHAR_PUPPET0)
            _surf, _r = input_font.render(f"人偶：{_disp}", UI_COLORS["button_text"])
            _r.center = cur_char_rect.center
            screen.blit(_surf, _r)

        # 底部按钮行：手势交互 / 剧本 / 分享卡片
        gesture_color = UI_COLORS["red"] if gesture_mode_on else UI_COLORS["button_text"]
        pygame.draw.rect(screen, gesture_color, gesture_button_rect, border_radius=8)
        gesture_text = "手势交互：开" if gesture_mode_on else "手势交互：关"
        g_surf, g_rect = input_font.render(gesture_text, UI_COLORS["text_light"])
        g_rect.center = gesture_button_rect.center
        screen.blit(g_surf, g_rect)

        script_btn_color = (100, 180, 100) if SCRIPT_MODE_ENABLED else (180, 100, 100)
        pygame.draw.rect(screen, script_btn_color, script_button_rect, border_radius=8)
        pygame.draw.rect(screen, (80, 80, 80), script_button_rect, width=2, border_radius=8)
        script_btn_text = "剧本：开" if SCRIPT_MODE_ENABLED else "剧本：关"
        s_surf, s_rect = input_font.render(script_btn_text, UI_COLORS["text_light"])
        s_rect.center = script_button_rect.center
        screen.blit(s_surf, s_rect)

        if share_system is None:
            share_btn_color = (150, 150, 150)
        else:
            share_btn_color = (100, 180, 100) if SHARE_MODE_ENABLED else (180, 100, 100)
        pygame.draw.rect(screen, share_btn_color, share_button_rect, border_radius=8)
        pygame.draw.rect(screen, (80, 80, 80), share_button_rect, width=2, border_radius=8)
        share_btn_text = "分享：开" if SHARE_MODE_ENABLED else "分享：关"
        sh_surf, sh_rect = input_font.render(share_btn_text, UI_COLORS["text_light"])
        sh_rect.center = share_button_rect.center
        screen.blit(sh_surf, sh_rect)

        # 停止按钮：中断所有正在播放/生成的内容并回到初始状态
        pygame.draw.rect(screen, (190, 70, 55), stop_button_rect, border_radius=8)
        pygame.draw.rect(screen, (80, 80, 80), stop_button_rect, width=2, border_radius=8)
        st_surf, st_rect = input_font.render("停止", UI_COLORS["text_light"])
        st_rect.center = stop_button_rect.center
        screen.blit(st_surf, st_rect)

        # 空场伴奏开关：音符图标（字体缺 ♪ 字形，直接用图元绘制），关闭时叠加斜杠
        bgm_color = (100, 180, 100) if bgm_enabled else (120, 120, 120)
        pygame.draw.rect(screen, bgm_color, bgm_button_rect, border_radius=8)
        pygame.draw.rect(screen, (80, 80, 80), bgm_button_rect, width=2, border_radius=8)
        _note = pygame.Surface((26, 30), pygame.SRCALPHA)
        pygame.draw.ellipse(_note, (250, 245, 235), (0, 18, 15, 10))
        pygame.draw.line(_note, (250, 245, 235), (13, 22), (13, 2), 3)
        pygame.draw.line(_note, (250, 245, 235), (13, 2), (24, 8), 3)
        screen.blit(_note, _note.get_rect(center=bgm_button_rect.center))
        if not bgm_enabled:
            pygame.draw.line(screen, (250, 245, 235),
                             (bgm_button_rect.left + 10, bgm_button_rect.top + 10),
                             (bgm_button_rect.right - 10, bgm_button_rect.bottom - 10), 3)

        # 状态提示（按钮行上方）：手势提示右对齐 / 剧目状态左对齐
        if gesture_mode_on:
            _l = pg_base.CHAR_DISPLAY.get(CHAR_PUPPET0, CHAR_PUPPET0)
            _r = pg_base.CHAR_DISPLAY.get(CHAR_PUPPET1, CHAR_PUPPET1)
            hint = f"左手={_l} 右手={_r} 手势: 握拳=出拳 剪刀V=踢腿 手刀=前进 捏合=后退" if HAS_GESTURE else "手势不可用"
            _hw = status_font.get_rect(hint).width
            status_font.render_to(screen, (input_box_rect.right - 15 - _hw, toolbar_y - 20),
                                  hint, UI_COLORS["text"])
        if script_system and script_system.current_script:
            script_info = f"📜 {script_system.current_script.title}"
            if script_system.player.is_playing:
                script_info += " ▶"
            elif script_system.player.is_paused:
                script_info += " ⏸"
            status_font.render_to(screen, (toolbar_x, toolbar_y - 20),
                                  script_info, UI_COLORS["script_status"])

        if recognized_text_timer > 0:
             recognized_text_timer -= 1
             if last_recognized_text:
                 rec_text_surf, rec_text_rect = status_font.render(f"识别: {last_recognized_text}", UI_COLORS["recognized_text"])
                 rec_text_rect.topright = (w - 15, 15)
                 screen.blit(rec_text_surf, rec_text_rect)

        # [GESTURE] 摄像头预览：主线程绘制到 preview_pos（可拖动，避免子线程开 OpenCV 窗口卡死）
        if gesture_mode_on and gesture_recognizer is not None:
            pv = gesture_recognizer.get_preview_rgb()
            if pv is not None:
                try:
                    ph, pw = pv.shape[:2]
                    # 转成连续内存，避免非连续视图被按错误步长读取导致花屏
                    rgb = np.ascontiguousarray(pv)
                    pv_surf = pygame.image.frombuffer(rgb.tobytes(), (pw, ph), "RGB").copy()
                    pv_w = 400
                    pv_h = int(ph * pv_w / pw)
                    pv_surf = pygame.transform.smoothscale(pv_surf, (pv_w, pv_h))
                    preview_pos[0] = max(0, min(w - pv_w, preview_pos[0]))
                    preview_pos[1] = max(0, min(h - pv_h, preview_pos[1]))
                    preview_rect = pygame.Rect(preview_pos[0], preview_pos[1], pv_w, pv_h)
                    screen.blit(pv_surf, preview_rect.topleft)
                    pygame.draw.rect(screen, UI_COLORS["gold"], preview_rect, 3)
                    # 标签放在预览框底部，避免与手势识别文字重叠
                    status_font.render_to(screen, (preview_rect.x + 4, preview_rect.bottom - 22),
                                          "摄像头预览", (255, 255, 255))
                except Exception as e:
                    print(f"[GESTURE] 预览渲染失败: {e}")
            else:
                # 还没收到帧：画占位框
                preview_rect = pygame.Rect(preview_pos[0], preview_pos[1], 400, 300)
                pygame.draw.rect(screen, (60, 60, 60), preview_rect)
                pygame.draw.rect(screen, UI_COLORS["gold"], preview_rect, 3)
                status_font.render_to(screen, (preview_rect.x + 4, preview_rect.y + 4),
                                      "等待摄像头画面...", (255, 255, 255))
        else:
            preview_rect = None

        # [分享卡片] 生成完成后在主线程渲染并弹层显示
        if share_system is not None:
            card_surf = share_system.poll_card()
            if card_surf is not None:
                if card_disp is None or card_disp[0] is not card_surf:
                    try:
                        cw, ch = card_surf.get_size()
                        scale = min(int(w * 0.9) / cw, int(h * 0.92) / ch, 1.0)
                        if scale < 1.0:
                            disp = pygame.transform.smoothscale(
                                card_surf, (max(1, int(cw * scale)), max(1, int(ch * scale))))
                        else:
                            disp = card_surf
                        dw, dh = disp.get_size()
                        card_disp = (card_surf, disp, (w - dw) // 2, (h - dh) // 2)
                    except Exception as e:  # noqa: BLE001
                        print(f"[分享] 弹层缩放失败: {e}")
                        card_disp = None
                if card_disp is not None:
                    _, disp, dx, dy = card_disp
                    dw, dh = disp.get_size()
                    shade = pygame.Surface((w, h), pygame.SRCALPHA)
                    shade.fill((0, 0, 0, 150))
                    screen.blit(shade, (0, 0))
                    screen.blit(disp, (dx, dy))
                    pygame.draw.rect(screen, UI_COLORS["gold"], (dx, dy, dw, dh), 3)
                    status_font.render_to(screen, (dx + 12, dy + dh - 26), "点击或按 Esc 关闭", (255, 255, 255))
            else:
                card_disp = None

        # ===== [帮助] 左上角小"?"标志：平时静默，鼠标悬停时展开操作说明 =====
        help_rect = pygame.Rect(15, 15, 34, 34)
        _help_lines = [
            "文字输入：点击输入框或按 Tab 激活，Enter 提交",
            "语音输入：按 Space 开始 / 停止录音",
            "视频指令：视频目录 · 播放视频 <名称> · 停止视频",
            "剧本指令：剧本目录 · 播放剧本 <名称> · 生成剧本 <主题>",
            "分享卡片：说“生成分享卡片即可制作",
            "空场伴奏：海宁文化馆录制，♪ 图标可开关",
            "底部按钮：手势交互 · 剧本 · 分享 · 人偶切换",
        ]
        _mouse_logical = _to_logical(pygame.mouse.get_pos())
        _hover_help = help_rect.collidepoint(_mouse_logical)

        # 画图标（半透明圆 + 金边 + "?"）
        _help_icon = pygame.Surface((help_rect.w, help_rect.h), pygame.SRCALPHA)
        pygame.draw.circle(_help_icon, (80, 60, 40, 180),
                           (help_rect.w // 2, help_rect.h // 2), help_rect.w // 2 - 2)
        pygame.draw.circle(_help_icon, UI_COLORS["gold"],
                           (help_rect.w // 2, help_rect.h // 2), help_rect.w // 2 - 2, 2)
        screen.blit(_help_icon, help_rect.topleft)
        _q_surf, _q_rect = status_font.render("?", (255, 255, 255))
        _q_rect.center = help_rect.center
        screen.blit(_q_surf, _q_rect)

        # 悬停时弹出说明面板
        if _hover_help:
            _pad = 14
            _line_h = status_font.get_sized_height() + 6
            _title_h = status_font.get_sized_height() + 10
            _line_widths = [status_font.get_rect(line).width for line in _help_lines]
            _panel_w = max(_line_widths) + _pad * 2
            _panel_h = _pad * 2 + _title_h + len(_help_lines) * _line_h
            _panel_x = help_rect.right + 8
            _panel_y = help_rect.top
            # 防越界
            if _panel_x + _panel_w > w - 10:
                _panel_x = max(10, w - _panel_w - 10)
            if _panel_y + _panel_h > h - 10:
                _panel_y = max(10, h - _panel_h - 10)
            _panel_rect = pygame.Rect(_panel_x, _panel_y, _panel_w, _panel_h)
            _panel_surf = pygame.Surface((_panel_w, _panel_h), pygame.SRCALPHA)
            pygame.draw.rect(_panel_surf, (40, 25, 15, 240),
                             (0, 0, _panel_w, _panel_h), border_radius=10)
            pygame.draw.rect(_panel_surf, UI_COLORS["gold"],
                             (0, 0, _panel_w, _panel_h), 2, border_radius=10)
            screen.blit(_panel_surf, _panel_rect.topleft)
            status_font.render_to(screen, (_panel_x + _pad, _panel_y + _pad),
                                  "操作提示", UI_COLORS["gold"])
            pygame.draw.line(screen, UI_COLORS["gold"],
                             (_panel_x + _pad, _panel_y + _pad + _title_h - 6),
                             (_panel_x + _panel_w - _pad, _panel_y + _pad + _title_h - 6), 1)
            _cur_y = _panel_y + _pad + _title_h
            for _line in _help_lines:
                status_font.render_to(screen, (_panel_x + _pad, _cur_y), _line, (240, 230, 210))
                _cur_y += _line_h
        _present()
        pygame.display.flip()


    print("退出 Pygame 主循环...")
    if gesture_recognizer:  # [GESTURE]
        gesture_recognizer.stop()
        gesture_recognizer = None
    if rec_stream:
        try:
            if not rec_stream.closed:
                rec_stream.stop()
                rec_stream.close()
                print("录音流已停止并关闭。")
        except Exception as e:
            print(f"[警告] 关闭录音流时出错: {e}")
    try:
        sd.stop()
        print("Sounddevice 已停止。")
    except Exception as e:
        print(f"[警告] 停止 Sounddevice 时出错: {e}")

    pygame.quit()
    print("Pygame 已退出。程序结束。")




last_check_time = time.time()

if __name__ == "__main__":
    print("程序启动...")
    initialize_agent_system()
    initialize_vosk_model()
    initialize_script_system()  # [剧本集成] 初始化剧本系统
    initialize_video_system()   # [视频集成] 初始化视频播放系统（首次用到视频指令才联网）
    initialize_share_system()  # [分享卡片] 初始化分享系统

    try:
        pygame_loop()
    except Exception as main_e:
        print(f"\n--- 主程序发生未处理的异常 ---")
        print(f"错误类型: {type(main_e).__name__}")
        print(f"错误信息: {main_e}")
        import traceback
        traceback.print_exc()
        print("------------------------------")


    finally:
        if pygame.get_init():
            pygame.quit()
            print("在 finally 块中强制退出 Pygame。")

