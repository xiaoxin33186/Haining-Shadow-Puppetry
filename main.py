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
    print("[TTS] Piper 未安装（C:\\piper-tts），语音播报禁用")

# [GESTURE] 摄像头抽象手势识别（按需启动），缺依赖时优雅禁用
try:
    from gesture_recognizer import GestureRecognizer, is_available as gesture_is_available
    HAS_GESTURE = gesture_is_available()
    if not HAS_GESTURE:
        print("[GESTURE] 摄像头手势功能不可用（缺 mediapipe/opencv 或手势模型）。")
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

# [GESTURE] 摄像头抽象手势 -> 皮影动作映射（六个打戏动作 + 挥手/捏合）
# 单手手势 left/right -> 对应左右人偶；双手手势 both -> 两人偶同时做
GESTURE_ACTION_MAP = {
    "wave":  ("actions/greet.json",   "挥手致意，这就拱手见礼！"),
    "pinch": ("actions/scratch.json", "捏合搓手，瞧我抓耳挠腮！"),
    "fist":  ("actions/punch.json",   "吃俺老孙一拳！"),
    "kick":  ("actions/kick.json",    "看我的飞踢！"),
    "push":  ("actions/lunge.json",   "看招，前冲！"),
    "pull":  ("actions/retreat.json", "后撤躲闪！"),
    "cross": ("actions/block.json",   "接招，格挡防御！"),
    "guard": ("actions/hit.json",     "哎呀，被打中了！"),
}

# 原地类动作（播放结束后人物归位到界面中央）；
# 移动类动作（forward/back 向前走/向后走）结束后保持当前位置
CENTER_RESET_ACTIONS = {"dance", "dun", "greet", "jump", "scratch", "normal", "idle",
                        "punch", "kick", "block", "hit"}
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
last_question_time = time.time()
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

# [分享卡片] 全局分享系统实例
share_system = None


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

    hand: "left"/"right"/"unknown"/"both"。
    left/unknown -> 左偶（新悟空）；right -> 右偶（牛魔王）；both -> 两只偶同时做。
    """
    entry = GESTURE_ACTION_MAP.get(gesture_name)
    if not entry:
        return
    action_path, reply = entry
    if hand == "both" and len(PUPPETS) >= 2:
        print(f"[GESTURE] 双手手势 '{gesture_name}' 触发动作: {action_path} -> 两人偶")
        for pup in PUPPETS:
            pup.queue.put(([action_path], reply))
        return
    target = (PUPPETS[1] if len(PUPPETS) > 1 and hand == "right"
              else PUPPETS[0] if PUPPETS else None)
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
                                             engine=engine, llm_client=llm_client)
        print("[剧本] 剧本系统初始化完成")
    except Exception as e:
        print(f"[剧本] 初始化失败: {e}")
        script_system = None

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

    global last_recognized_text, recognized_text_timer, last_question_time, script_system, SCRIPT_MODE_ENABLED, share_system

    text = text.strip()
    if not text:
        print("[Agent] 输入为空，忽略。")
        return

    # ========== 1. 剧本命令处理（最高优先级） ==========
    if script_system is not None and SCRIPT_MODE_ENABLED:
        # [分享卡片] 分享意图优先（仅在剧本模式下、关键词触发）
        if share_system is not None and share_system.is_share_intent(text):
            print(f"[分享] 触发分享生成: {text}")
            try:
                share_system.handle(text, script_system.current_script)
            except Exception as e:  # noqa: BLE001
                print(f"[分享错误] {e}")
                action_queue.put((["idle"], f"❌ 分享生成失败: {str(e)[:30]}"))
            last_recognized_text = ""
            recognized_text_timer = 0
            return

        is_script = any(kw in text for kw in
                        ["剧本", "播放", "表演", "暂停", "继续", "停止", "状态", "目录", "列表"])
        if is_script:
            print(f"[剧本] 拦截命令: {text}")
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

    last_question_time = time.time()

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
    global is_recording, rec_stream, audio_buffers
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

class Puppet:
    """单个人偶状态机：负责 idle 循环、动作播放、位移与手势/文本队列。

    把原先 pygame_loop 里的一套扁平状态变量（state/idle_idx/action_frames/
    current_offset/last_pelvis_abs/action_queue）封装成实例，每个人偶独立一份，
    从而实现左右手分别操控两个人偶。
    """

    def __init__(self, name, mats, cfg, idle_file, x_shift=0.0, q=None):
        self.name = name
        self.mats = mats                # 该人偶的角色素材
        self.cfg = cfg                  # 该人偶的渲染配置快照（枢轴/旋转/镜像）
        self.x_shift = x_shift          # 起始站位水平偏移（左偶偏左/右偶偏右）
        self.center_reset = CENTER_RESET_ACTIONS

        frames, init_joints, fw, fh = load_frames([str(idle_file)])
        if not frames:
            raise ValueError(f"{name}: Idle 动画加载失败")
        self.idle_frames = frames
        self.init_joints_idle = init_joints
        self.center_x, self.center_y = fw // 2, fh // 2

        self.state = 'idle'
        self.idle_idx = 0
        self.action_idx = 0
        self.action_frames = []
        self.action_initial_joints = {}
        self.json_list = []
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

    def _dequeue_action(self):
        """空闲时若有请求则加载动作进入 action 状态。返回 (是否处理, 回复文本)。"""
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
                self.last_pelvis_abs['x'] = max(0.0, min(float(loaded_w), self.last_pelvis_abs['x']))
                self.last_pelvis_abs['y'] = max(0.0, min(float(loaded_h), self.last_pelvis_abs['y']))
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
        loaded, r = self._dequeue_action()
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
        return joints_for_render, reply_new


def pygame_loop():
    global is_recording, last_recognized_text, recognized_text_timer, input_text, typing, ime_composing_text, \
           gesture_mode_on, gesture_recognizer, PUPPETS, CHAR_PUPPET0, CHAR_PUPPET1, \
           script_system, SCRIPT_MODE_ENABLED, share_system

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
    screen = pygame.display.set_mode((w, h))
    pygame.display.set_caption("皮影数字人 (文本: Tab/回车, 语音: Space)")
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


    input_box_scale = 0.62
    input_box_height = int(245 * input_box_scale)
    input_box_width = max(950, w - 180)
    input_box_x = (w - input_box_width - 130) // 2
    input_box_y = h - input_box_height - 10
    input_box_img = create_input_box(input_box_width, input_box_height)
    input_box_rect = pygame.Rect(input_box_x, input_box_y, input_box_width, input_box_height)
    input_text_offset_x = 15
    input_text_offset_y = (input_box_height - input_font.get_sized_height(18)) // 2

    button_height = input_box_height - 10
    button_width = 110
    button_x = input_box_x + input_box_width + 10
    button_y = input_box_y + 5
    button_rect = pygame.Rect(button_x, button_y, button_width, button_height)

    # [GESTURE] 手势控制按钮：置于输入模式按钮正上方
    gesture_button_height = 50
    gesture_button_rect = pygame.Rect(button_x, button_y - gesture_button_height - 8,
                                      button_width, gesture_button_height)

    # [角色切换] 左右偶角色按钮，叠放在手势按钮上方
    char_button_height = 50
    left_char_rect = pygame.Rect(button_x, gesture_button_rect.y - char_button_height - 8,
                                 button_width, char_button_height)
    right_char_rect = pygame.Rect(button_x, left_char_rect.y - char_button_height - 8,
                                  button_width, char_button_height)

    # [剧本集成] 剧本模式切换按钮：叠放在右偶按钮上方
    script_button_height = 50
    script_button_rect = pygame.Rect(button_x, right_char_rect.y - script_button_height - 8,
                                     button_width, script_button_height)

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

    def _show_reply(reply):
        """某个人偶触发的回复：更新共享输出框并 TTS 播报。"""
        nonlocal text_reply, output_visible, output_fade_timer
        text_reply = reply or ""
        output_visible = bool(reply)
        output_fade_timer = OUTPUT_FADE_DURATION if output_visible else 0
        if reply and reply.strip() and HAS_TTS and tts_engine is not None:
            tts_engine.speak_async(reply)  # TTS 播报

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

    def _build_puppet(name, side, x_shift):  # [角色切换]
        """按角色名构建一只偶；side=0 左 / 1 右。失败返回 None。"""
        setup = pg_base.character_setup(name)
        if not setup:
            return None
        q = action_queue if side == 0 else None
        return Puppet(f"{'左' if side == 0 else '右'}/{name}", setup[0], setup[1],
                      idle_file=idle_file, x_shift=x_shift, q=q)

    def _build_layout():  # [布局]
        """按 gesture_mode_on 重建布局：常态=单个居中；手势=左右两只对峙。"""
        nonlocal puppet0, puppet1, mirror1
        left_shift = -gap_x if gesture_mode_on else 0.0
        p0 = _build_puppet(CHAR_PUPPET0, 0, left_shift)
        if p0 is None:
            print(f"[布局] 左偶角色 '{CHAR_PUPPET0}' 不可用，回退当前角色")
            p0 = Puppet(f"左/{CHAR_PUPPET0}", mats, pg_base.capture_config(),
                        idle_file=idle_file, x_shift=left_shift, q=action_queue)
        puppet0 = p0
        puppet1 = None
        if gesture_mode_on:
            p1 = _build_puppet(CHAR_PUPPET1, 1, gap_x)
            if p1 is None:
                print(f"[布局] 警告: 未找到右偶角色 '{CHAR_PUPPET1}'，仅单人偶运行。")
            puppet1 = p1
        PUPPETS.clear()
        PUPPETS.append(puppet0)
        if puppet1:
            PUPPETS.append(puppet1)
        mirror1 = not bool(puppet0.cfg["scene_mirror"])
        print(f"[布局] {'双人偶（手势）' if gesture_mode_on else '单人偶（常态）'}，左偶={CHAR_PUPPET0}")

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
    while running:
        delta_time = clock.tick(FPS) / 1000.0 

        # [GESTURE] 手势线程若已结束（摄像头异常/被关闭），自动关闭开关，保持 UI 同步
        if gesture_mode_on and gesture_recognizer is not None and not gesture_recognizer.is_alive():
            print("[GESTURE] 手势线程已结束，自动关闭手势控制。")
            gesture_mode_on = False
            gesture_recognizer = None

        for ev in pygame.event.get():
            # [分享卡片] 弹层打开时，点击或 Esc 关闭并吞掉该事件
            if share_system is not None and share_system.is_card_visible():
                if (ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE) or \
                   (ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1):
                    share_system.close_card()
                    continue

            if ev.type == pygame.QUIT:
                running = False
                break

            if ev.type == pygame.MOUSEBUTTONDOWN:
            
                if ev.button == 1:
                    if input_mode == "text":
                        if input_box_rect.collidepoint(ev.pos):
                            if not typing:
                                typing = True
                                input_text = ""
                                ime_composing_text = ""
                                pygame.key.start_text_input()
                                pygame.key.set_text_input_rect(input_box_rect)  # 候选框跟随输入框
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
                                print("[GESTURE] 手势功能不可用（缺 mediapipe/opencv 或模型）")
                        else:
                            if gesture_recognizer is not None:
                                gesture_recognizer.stop()
                                gesture_recognizer = None
                            _build_layout()  # 回到常态：单个居中人偶
                        print(f"[GESTURE] 手势控制: {'开启' if gesture_mode_on else '关闭'}")

                    # [角色切换] 左/右偶角色按钮（Shift+点击反向）
                    _rev = -1 if (pygame.key.get_mods() & pygame.KMOD_SHIFT) else 1
                    if left_char_rect.collidepoint(ev.pos):
                        _cycle_character(0, _rev)
                    elif right_char_rect.collidepoint(ev.pos):
                        _cycle_character(1, _rev)

                    # [剧本集成] 剧本模式切换按钮
                    if script_button_rect.collidepoint(ev.pos):
                        SCRIPT_MODE_ENABLED = not SCRIPT_MODE_ENABLED
                        mode_text = "启用" if SCRIPT_MODE_ENABLED else "禁用"
                        print(f"[剧本] 剧本模式已{mode_text}")
                        last_recognized_text = f"剧本模式已{mode_text}"
                        recognized_text_timer = RECOGNIZED_TEXT_DURATION

            elif ev.type == pygame.KEYDOWN:
                if input_mode == "voice" and ev.key == pygame.K_SPACE:
                    toggle_recording()

                elif input_mode == "text" and ev.key == pygame.K_TAB:
                     typing = not typing
                     if typing:
                         input_text = ""
                         ime_composing_text = ""
                         pygame.key.start_text_input()
                         pygame.key.set_text_input_rect(input_box_rect)  # 候选框跟随输入框
                         print("输入框已激活 (Tab)。")
                     else:
                         pygame.key.stop_text_input()
                         ime_composing_text = ""
                         print("输入框已取消激活 (Tab)。")
                         if input_text.strip():
                             print(f"提交文本 (Tab): {input_text.strip()}")
                             threading.Thread(target=process_and_enqueue, args=(input_text.strip(),), daemon=True).start()
                         input_text = ""

                elif typing and input_mode == "text":
                    if ev.key == pygame.K_RETURN or ev.key == pygame.K_KP_ENTER:
                        print(f"提交文本 (Enter): {input_text.strip()}")
                        pygame.key.stop_text_input()
                        typing = False
                        ime_composing_text = ""
                        if input_text.strip():
                            threading.Thread(target=process_and_enqueue, args=(input_text.strip(),), daemon=True).start()
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
            )

        if output_visible and text_reply:
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

        elif output_visible and output_fade_timer > 0:
            output_fade_timer -= 1
            if output_box_img and output_box_rect:
                 alpha = int(min(1.0, output_fade_timer / OUTPUT_FADE_DURATION) * UI_COLORS["output_bg"][3])
                 output_box_img.set_alpha(alpha)
                 screen.blit(output_box_img, output_box_rect.topleft)
            if output_fade_timer <= 0:
                 output_visible = False
                 output_box_img = None
                 output_box_rect = None


        if input_mode == "text":
             screen.blit(input_box_img, input_box_rect.topleft)
             text_render_x = input_box_rect.x + input_text_offset_x
             text_render_y = input_box_rect.y + input_text_offset_y

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

        button_color = UI_COLORS["gold"] if input_mode == "voice" else UI_COLORS["button_text"]
        pygame.draw.rect(screen, button_color, button_rect, border_radius=8)
        button_text = "语音模式" if input_mode == "voice" else "文字模式"
        btn_text_color = UI_COLORS["text_light"]
        text_surf, text_rect = input_font.render(button_text, btn_text_color)
        text_rect.center = button_rect.center
        screen.blit(text_surf, text_rect)

        # [角色切换] 绘制左/右偶角色按钮
        for _rect, _side, _name in ((left_char_rect, 0, CHAR_PUPPET0),
                                    (right_char_rect, 1, CHAR_PUPPET1)):
            pygame.draw.rect(screen, UI_COLORS["gold"], _rect, border_radius=8)
            _disp = pg_base.CHAR_DISPLAY.get(_name, _name)
            _label = f"{'左' if _side == 0 else '右'}偶：{_disp}"
            _surf, _r = input_font.render(_label, UI_COLORS["button_text"])
            _r.center = _rect.center
            screen.blit(_surf, _r)

        # [GESTURE] 绘制手势控制按钮
        gesture_color = UI_COLORS["red"] if gesture_mode_on else UI_COLORS["button_text"]
        pygame.draw.rect(screen, gesture_color, gesture_button_rect, border_radius=8)
        gesture_text = "手势控制：开" if gesture_mode_on else "手势控制：关"
        g_surf, g_rect = input_font.render(gesture_text, UI_COLORS["text_light"])
        g_rect.center = gesture_button_rect.center
        screen.blit(g_surf, g_rect)
        if gesture_mode_on:
            _l = pg_base.CHAR_DISPLAY.get(CHAR_PUPPET0, CHAR_PUPPET0)
            _r = pg_base.CHAR_DISPLAY.get(CHAR_PUPPET1, CHAR_PUPPET1)
            hint = f"左手={_l} 右手={_r} 双手=合击" if HAS_GESTURE else "手势不可用"
            status_font.render_to(screen, (gesture_button_rect.x - 320, gesture_button_rect.centery - 10),
                                  hint, UI_COLORS["text"])

        # [剧本集成] 绘制剧本模式按钮与当前剧目状态
        script_btn_color = (100, 180, 100) if SCRIPT_MODE_ENABLED else (180, 100, 100)
        pygame.draw.rect(screen, script_btn_color, script_button_rect, border_radius=8)
        pygame.draw.rect(screen, (80, 80, 80), script_button_rect, width=2, border_radius=8)
        script_btn_text = "剧本：开" if SCRIPT_MODE_ENABLED else "剧本：关"
        s_surf, s_rect = input_font.render(script_btn_text, UI_COLORS["text_light"])
        s_rect.center = script_button_rect.center
        screen.blit(s_surf, s_rect)

        if script_system and script_system.current_script:
            script_info = f"📜 {script_system.current_script.title}"
            if script_system.player.is_playing:
                script_info += " ▶"
            elif script_system.player.is_paused:
                script_info += " ⏸"
            status_font.render_to(screen, (script_button_rect.x - 240, script_button_rect.centery - 10),
                                  script_info, UI_COLORS["script_status"])

        if input_mode == "voice":
             rec_indicator_x = button_rect.left + 150
             rec_indicator_y = button_rect.centery
             rec_color = UI_COLORS["recording_active"] if is_recording else UI_COLORS["recording_idle"]
             pygame.draw.circle(screen, rec_color, (rec_indicator_x, rec_indicator_y), 7)
             rec_status_text = "录音中..." if is_recording else "按 '空格' 开始录音"
             status_font.render_to(screen,
                                  (rec_indicator_x + 12, rec_indicator_y - 10),
                                  rec_status_text,
                                  rec_color)

        if recognized_text_timer > 0:
             recognized_text_timer -= 1
             if last_recognized_text:
                 rec_text_surf, rec_text_rect = status_font.render(f"识别: {last_recognized_text}", UI_COLORS["recognized_text"])
                 rec_text_rect.topright = (w - 15, 15)
                 screen.blit(rec_text_surf, rec_text_rect)

        # [GESTURE] 摄像头预览：由主线程把最新帧画到主窗口左上角（避免子线程开 OpenCV 窗口卡死）
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
                    screen.blit(pv_surf, (10, 10))
                    pygame.draw.rect(screen, UI_COLORS["gold"], (10, 10, pv_w, pv_h), 3)
                    # 标签放在预览框底部，避免与手势识别文字重叠
                    status_font.render_to(screen, (14, 10 + pv_h - 22), "摄像头预览", (255, 255, 255))
                except Exception as e:
                    print(f"[GESTURE] 预览渲染失败: {e}")
            else:
                # 还没收到帧：画占位框
                pygame.draw.rect(screen, (60, 60, 60), (10, 10, 400, 300))
                pygame.draw.rect(screen, UI_COLORS["gold"], (10, 10, 400, 300), 3)
                status_font.render_to(screen, (14, 14), "等待摄像头画面...", (255, 255, 255))

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

