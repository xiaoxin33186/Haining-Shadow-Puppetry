# -*- coding: utf-8 -*-
"""摄像头单手抽象手势识别：握拳(出拳) / 捏合(向后走) / 手刀(向前走) / 剪刀手V(踢腿)。

基于 MediaPipe HandLandmarker（新版 Tasks API，兼容 mediapipe >= 1.0）+ OpenCV，
在独立后台线程运行，检测到手势后回调主流程。
缺依赖（mediapipe tasks / opencv）或模型不可用时优雅降级
（available 为 False、start 直接返回），不影响既有的文字/语音交互主链路。

首次运行会在 projects 目录下自动下载 hand_landmarker.task 模型到 ./models/。

单手识别：num_hands=2，借助 handedness 区分为 left/right，
每只手独立分类、独立消抖（连续确认帧按手分开）。
回调接口为 callback(hand: "left"|"right", gesture_name: "fist"|"pinch"|"kick"|"push")，
供主程序按左右手分发到不同人偶（左手->第一个人偶，右手->第二个人偶）。
"""

import os
import threading
import time
import urllib.request

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import mediapipe as mp                                        # noqa: E402
    from mediapipe.tasks import python as mp_python               # noqa: E402
    from mediapipe.tasks.python import vision                     # noqa: E402
    _HAS_TASKS = True
except ImportError:
    mp = None
    mp_python = None
    vision = None
    _HAS_TASKS = False

_HERE = os.path.dirname(os.path.abspath(__file__))
# 官方 hand_landmarker 模型（另存到项目可写的 models/ 目录，避免装到系统环境）
_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/"
              "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task")
_MODEL_PATH = os.path.join(_HERE, "models", "hand_landmarker.task")

# MediaPipe 手部 21 关键点的标准连线（非旧版 solutions，需手动绘制）
_HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),               # 拇指
    (0, 5), (5, 6), (6, 7), (7, 8),               # 食指
    (5, 9), (9, 10), (10, 11), (11, 12),          # 中指
    (9, 13), (13, 14), (14, 15), (15, 16),        # 无名指
    (13, 17), (17, 18), (18, 19), (19, 20),       # 小指
    (0, 17),
)


def _ensure_model(model_path=_MODEL_PATH, url=_MODEL_URL):
    """模型文件不存在时下载一次。返回是否可用。"""
    if os.path.exists(model_path):
        return True
    try:
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        print("[手势识别] 首次运行，正在下载 hand_landmarker 模型 ...")
        tmp = model_path + ".download"
        urllib.request.urlretrieve(url, tmp)
        os.replace(tmp, model_path)
        print(f"[手势识别] 手势模型已就绪: {model_path}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[手势识别] 手势模型下载失败，摄像头手势功能已禁用: {e}")
        return False


def is_available():
    """是否具备手势识别条件（只要求依赖齐全，不要求模型文件已下载）。

    刻意不检查模型文件：首次启用时 start() 会通过 _ensure_model() 自动下载。
    若把模型存在性算进来，新机器上 HAS_GESTURE 会恒为 False，按钮永远打不开，
    自动下载那段代码也永远走不到。
    """
    return cv2 is not None and _HAS_TASKS


# ===== MediaPipe 任务实例复用 =====
# landmarker.close() 在本环境下会永久阻塞（实测卡在内部 Event.wait 上不返回），
# 而它的 __del__ 又会调用 close()——因此不能每次开关会话都新建/丢弃实例：那样每开关一次
# 就泄漏一个实例和一条僵尸线程，反复开关后摄像头会抢不到（预览黑屏/卡死）。
# 这里改为全进程复用一个实例、永不 close，退出时由进程统一回收。
_landmarker = None
_landmarker_lock = threading.Lock()
_ts_ms = 0


def _next_timestamp():
    """返回按真实时间推进、且严格单调递增的毫秒时间戳。

    VIDEO 模式内部用时间戳做跟踪平滑，必须与真实帧间隔一致：
    固定 +33ms 在低帧率摄像头（弱光自动降帧）下会让运动模型失真、更容易丢手。
    实例是全进程复用的，跨会话也必须单调递增，故用 max 兜底。
    """
    global _ts_ms
    now_ms = int(time.monotonic() * 1000)
    _ts_ms = max(_ts_ms + 1, now_ms)
    return _ts_ms


def _get_landmarker():
    """惰性创建并复用唯一的 HandLandmarker 实例（整个进程只创建一次）。"""
    global _landmarker
    with _landmarker_lock:
        if _landmarker is None:
            base_options = mp_python.BaseOptions(model_asset_path=_MODEL_PATH)
            options = vision.HandLandmarkerOptions(
                base_options=base_options,
                running_mode=vision.RunningMode.VIDEO,   # 手动喂帧，适配线程拉取模式
                num_hands=2,                            # 双手独立识别
                # 弱光下检测/跟踪置信度会下降，放宽阈值以提升夜间识别率（8帧确认兜底防误触发）
                min_hand_detection_confidence=0.20,
                min_hand_presence_confidence=0.20,
                min_tracking_confidence=0.20,
            )
            _landmarker = vision.HandLandmarker.create_from_options(options)
        return _landmarker


class GestureRecognizer:
    """后台线程持续读取摄像头并分类手势。"""

    # 可识别的手势名称（四个单手手势）
    GESTURE_NAMES = ("fist", "pinch", "kick", "push")

    def __init__(self, callback, camera_index=0, cooldown=2.0,
                 pinch_ratio=0.30, wave_window=1.5,
                 confirm_hold=0.20, confirm_gap=0.30, confirm_min_hits=2,
                 show_preview=True, move_callback=None):
        self.callback = callback              # callback(hand, gesture_name)，在主流程线程调用
        # 连续手部位置回调：move_callback(hand, x, y)；手不在画面时 x/y 为 None
        self.move_callback = move_callback
        self.camera_index = camera_index
        self.cooldown = cooldown              # 同一手势两次触发的最小间隔（秒）
        self.pinch_ratio = pinch_ratio        # 拇指-食指间距 / 手尺寸 < 该值视为捏合
        self.wave_window = wave_window        # 挥手方向变化的观察窗口（秒）
        self.confirm_hold = confirm_hold      # 手势需持续这么久才触发（按时间，与帧率无关）
        self.confirm_gap = confirm_gap        # 手势/手短暂消失的容忍时长，超时才重置
        self.confirm_min_hits = confirm_min_hits  # 至少命中这些帧，防单帧误判
        self.show_preview = show_preview      # 是否生成实时预览帧（由主线程渲染）
        self.preview_frame = None             # 最新带标注预览帧(BGR)，供主线程读取
        self._preview_lock = threading.Lock()
        self._stop = False
        self._thread = None
        self._last_trigger = {}               # {(hand, gesture): 最近触发时间}，冷却按 手+手势 分开
        # 以下状态均按手 (left/right/unknown) 分开，避免双手互相污染。
        # 消抖状态机基于时间而非帧数：低帧率摄像头（实测 7.5fps）下按帧计数会永远凑不够。
        self._state = {h: {"gesture": None, "since": 0.0, "hits": 0, "last_seen": 0.0}
                       for h in ("left", "right", "unknown")}

    @property
    def available(self):
        return cv2 is not None and _HAS_TASKS and _ensure_model()

    def start(self):
        if not self.available:
            print("[手势识别] mediapipe tasks、opencv 或手势模型不可用，摄像头手势功能已禁用（不影响文字/语音）。")
            return False
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("[手势识别] 摄像头手势识别已启动（单手独立）："
              "握拳→出拳，捏合→向后走，手刀→向前走，剪刀手V→踢腿。")
        return True

    def stop(self):
        """停止识别线程，并等待其释放摄像头后再返回。

        _open_camera() 会反复试后端并预热若干帧，耗时可达数秒；若只置标志位就返回，
        旧线程仍在霸占摄像头设备，紧接着开启的新会话会与之争抢设备（预览黑屏/卡死）。
        """
        self._stop = True
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=2.0)
            if t.is_alive():
                print("[手势识别] 警告: 旧识别线程未能在 2 秒内释放摄像头。")

    def is_alive(self):
        """手势线程是否仍在运行（供主程序同步 UI 状态）。"""
        return self._thread is not None and self._thread.is_alive()

    @staticmethod
    def _dist(a, b):
        return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5

    def _classify(self, lm, hand):
        """根据 21 个手部关键点判断单手手势。lm 为关键点列表。

        hand: 该手归属的 "left"/"right"/"unknown"，用于把各状态按手隔离。
        返回: "fist"/"pinch"/"kick"/"push" 之一，或 None。
        """
        wrist = lm[0]
        palm = lm[9]
        hand_size = self._dist(wrist, palm)  # 手腕 -> 中指掌指关节，作为手尺寸基数
        if hand_size < 1e-6:
            return None

        tips = (8, 12, 16, 20)
        # 各指尖到手腕的距离（归一化）：越大手指越伸直
        ext = {t: self._dist(lm[t], wrist) / hand_size for t in tips}
        # 各指尖到掌心的距离（归一化）：越小手指越弯曲
        curl = {t: self._dist(lm[t], palm) / hand_size for t in tips}
        spread = self._dist(lm[8], lm[20]) / hand_size  # 食指-小指张开程度

        # 握拳：四指卷曲进掌心且食指弯进掌内 → 出拳
        #    仅限胸口高度(0.35~0.75)握拳；低位/高位握拳不触发 → 手自然垂放绝对安全
        #    （后撤已改走"双手握拳护胸"专属手势，与出拳不再重叠）
        if (all(curl[t] < 0.68 for t in tips) and curl[8] < 0.55):
            if 0.35 <= wrist.y <= 0.75:
                return "fist"
            return None

        # 捏合/向后走：拇指尖与食指尖距离极小，且【食指挺直】+ 中指/无名指/小指伸展开
        #     → 握拳时食指卷曲、其余三指也弯，不满足这些条件
        if (self._dist(lm[4], lm[8]) / hand_size < self.pinch_ratio
                and ext[8] > 0.85
                and curl[12] > 0.55 and curl[16] > 0.45 and curl[20] > 0.40):
            return "pinch"

        # 手刀/劈掌：四指并拢伸直、五指贴拢（spread 小）→ 向前走
        #   要求拇指不碰食指尖（避开"捏合"），避免两者冲突
        if (spread < 0.30 and all(ext[t] > 0.70 for t in tips)
                and self._dist(lm[4], lm[8]) / hand_size >= 0.28):
            return "push"

        # 剪刀手 V/踢腿：食指+中指伸直并分开，无名指+小指弯曲 → 踢腿
        #    食指中指 ext 大（伸直），两者彼此分开（dist 大），无名指小指 curl 小（弯曲）
        #    （与"手刀"区分：手刀四指并拢伸直；与"捏合"区分：捏合要求拇指碰食指）
        v_spread = self._dist(lm[8], lm[12]) / hand_size  # 食指-中指分开程度
        if (ext[8] > 0.70 and ext[12] > 0.70
                and v_spread > 0.30
                and curl[16] < 0.68 and curl[20] < 0.68
                and self._dist(lm[4], lm[8]) / hand_size >= 0.25):
            if wrist.y < 0.75:
                return "kick"
            return None
        return None

    def _is_stable(self, hand, g, now):
        """基于时间的消抖：同一手势持续 confirm_hold 秒且至少命中 confirm_min_hits 帧。

        不按帧数判定，因为摄像头帧率差异很大（实测有 7.5fps 的机器，按帧计数
        在固定时间窗内永远凑不够样本，表现为"怎么做手势都没反应"）。
        中间漏检或单帧误判由 confirm_gap 容忍，换手势会立即重置计时。
        """
        st = self._state[hand]
        # 手或手势消失超过容忍时长：重置，避免"手离开很久再回来"直接触发
        if st["gesture"] is not None and now - st["last_seen"] > self.confirm_gap:
            st["gesture"] = None
            st["hits"] = 0
        if g is None:
            return False
        st["last_seen"] = now
        if g != st["gesture"]:
            st["gesture"] = g
            st["since"] = now
            st["hits"] = 1
            return False
        st["hits"] += 1
        if st["hits"] < self.confirm_min_hits:
            return False
        return now - st["since"] >= self.confirm_hold

    def _trigger(self, hand, gesture):
        key = (hand, gesture)
        now = time.time()
        if now - self._last_trigger.get(key, 0.0) < self.cooldown:
            return
        self._last_trigger[key] = now
        print(f"[手势识别] 检测到手势: {hand}手 -> {gesture}")
        if self.callback:
            self.callback(hand, gesture)

    def _open_camera(self):
        """逐个后端试读，返回能出「非全黑」画面的 cap；都不行返回 None。

        不同机器上可用的后端不同（DSHOW/MSMF/默认），且有的后端能打开却只返回黑帧，
        因此这里不仅看 isOpened，还预热读若干帧判断是否黑屏。
        后端优先 MSMF：实测同一摄像头 MSMF 约 18fps、DSHOW 只有 6fps，
        帧率过低会让手势判定明显迟滞。
        """
        backends = [("MSMF", cv2.CAP_MSMF), ("DSHOW", cv2.CAP_DSHOW), ("ANY", cv2.CAP_ANY)]
        for name, backend in backends:
            if self._stop:      # 已被要求停止：立刻放弃，不再占用摄像头
                return None
            try:
                cap = cv2.VideoCapture(self.camera_index, backend)
            except Exception:  # noqa: BLE001
                continue
            if not cap.isOpened():
                cap.release()
                continue
            # 限制采集分辨率与帧率：默认可能给 1080p，MediaPipe 推理跟不上会让帧率掉到个位数
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS, 30)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # 只留最新一帧，降低手部拖拽延迟
            ok_any = False
            good = False
            t_warm = time.monotonic()
            for _ in range(10):  # 预热并检测黑帧
                if self._stop:
                    cap.release()
                    return None
                ok, f = cap.read()
                if ok and f is not None and float(f.mean()) > 1.0:
                    good = True
                if ok:
                    ok_any = True
            warm_fps = 10 / max(time.monotonic() - t_warm, 1e-6)
            if good:
                print(f"[手势识别] 摄像头后端 {name} 可用，画面正常（预热帧率≈{warm_fps:.1f}fps）。")
                print(f"[手势识别] 采集参数: "
                      f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
                      f"{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))} @ "
                      f"{cap.get(cv2.CAP_PROP_FPS):.0f}fps")
                return cap
            print(f"[手势识别] 摄像头后端 {name} {'只返回黑帧' if ok_any else '读取失败'}，尝试下一个。")
            cap.release()
            time.sleep(0.3)  # 给驱动一点时间彻底释放，再试下一个后端
        return None

    def _run(self):
        cap = self._open_camera()
        if cap is None:
            print("[手势识别] 无法打开摄像头，摄像头手势功能已禁用。")
            return

        landmarker = _get_landmarker()
        read_fails = 0
        try:
            while not self._stop:
                ok, frame = cap.read()
                if not ok:
                    # 单帧读失败多为瞬时抖动（USB 抢占/驱动抖动），连续失败才判定摄像头掉线
                    read_fails += 1
                    if read_fails >= 30:
                        print("[手势识别] 摄像头连续读取失败，手势线程退出。")
                        break
                    time.sleep(0.03)
                    continue
                read_fails = 0
                # 归一化为 3 通道 BGR（部分摄像头会返回 4 通道/灰度，否则预览会花屏）
                if frame.ndim == 2:
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                elif frame.shape[2] == 4:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                frame = cv2.flip(frame, 1)  # 镜像，更符合操作直觉
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                # 时间戳按真实时间推进，与真实帧率一致（跨会话严格单调）
                result = landmarker.detect_for_video(mp_image, _next_timestamp())
                gesture = None       # 最近一次实际触发的手势（供预览显示）
                gesture_hands = {}   # {hand: gesture}，供预览标记左右手
                hand_pts = {}        # {hand: wrist landmark}，用于连续手部位置上报
                for i, lm in enumerate(result.hand_landmarks or []):
                    hand_pts[self._hand_label(result.handedness[i] if result.handedness else None)] = lm[0]

                if result.hand_landmarks:
                    now = time.monotonic()
                    # 逐手独立判定、独立消抖
                    for i, lm in enumerate(result.hand_landmarks):
                        hand = self._hand_label(result.handedness[i] if result.handedness else None)
                        g = self._classify(lm, hand)
                        if g:
                            gesture_hands[hand] = g
                            if self._is_stable(hand, g, now):
                                gesture = g
                                self._trigger(hand, g)
                # 手离开画面时时间状态会按 confirm_gap 自动过期

                # 连续上报每只手的归一化坐标（无手则上报 None，供主程序松手）
                if self.move_callback:
                    for h in ("left", "right", "unknown"):
                        p = hand_pts.get(h)
                        if p is not None:
                            self.move_callback(h, p.x, p.y)
                        else:
                            self.move_callback(h, None, None)

                if self.show_preview:
                    # 只在本线程生成带标注的预览帧，交给主线程渲染（不在子线程开 OpenCV 窗口）
                    self._draw_preview(
                        frame, result.hand_landmarks, result.handedness,
                        gesture, gesture_hands)
        finally:
            # 只需释放摄像头：landmarker 为全进程复用的单例，不在此关闭
            # （close() 会永久阻塞，而 __del__ 又会调用它，故实例始终保留）。
            try:
                cap.release()
            except Exception:  # noqa: BLE001
                pass
            print("[手势识别] 摄像头已释放，手势线程结束。")

    def get_preview_rgb(self):
        """返回最新预览帧的 RGB 副本（供主线程渲染），无则 None。"""
        with self._preview_lock:
            if self.preview_frame is None:
                return None
            return self.preview_frame[:, :, ::-1].copy()  # BGR -> RGB

    @staticmethod
    def _hand_label(handedness):
        """根据 MediaPipe handedness 判定左右手，返回 "left"/"right"/"unknown"。

        handedness 形如 [[Classification(category_name='Left', display_name='Left',...)], ...]。
        注意：媒体流已做镜像(flip)，但不影响 handedness 语义——它表示画面中人的真实左右手，
        正好对应真实世界中你的左手/右手。
        """
        try:
            cat = handedness[0] if handedness else None
            name = (getattr(cat, "display_name", None)
                    or getattr(cat, "category_name", None) or "")
        except Exception:  # noqa: BLE001
            name = ""
        lowered = str(name).strip().lower()
        if lowered.startswith("l"):
            return "left"
        if lowered.startswith("r"):
            return "right"
        return "unknown"

    def _draw_preview(self, frame, hand_landmarks, handedness, gesture, gesture_hands):
        """在画面上绘制多只手的骨架与识别结果，返回是否应退出预览(True=q/Esc)。"""
        h, w = frame.shape[:2]
        hand_colors = {"left": (255, 0, 0), "right": (0, 255, 0), "unknown": (0, 255, 255)}
        gesture_text = {
            "fist": "FIST -> punch",
            "pinch": "PINCH -> back",
            "kick": "V-PEACE -> kick",
            "push": "PUSH -> forward",
        }
        if hand_landmarks:
            for i, lm in enumerate(hand_landmarks):
                color = hand_colors.get(self._hand_label(handedness[i] if handedness else None),
                                        (0, 255, 255))
                pts = [(int(k.x * w), int(k.y * h)) for k in lm]
                for a, b in _HAND_CONNECTIONS:
                    cv2.line(frame, pts[a], pts[b], color, 2)
                for p in pts:
                    cv2.circle(frame, p, 3, (0, 0, 255), -1)

        # 显示每只手的实时判断
        line_y = 30
        if gesture_hands:
            for idx, (hd, g) in enumerate(gesture_hands.items()):
                color = hand_colors.get(hd, (255, 255, 255))
                text = f"{hd} {gesture_text.get(g, 'no gesture')}"
                cv2.putText(frame, text, (12, line_y + idx * 32), cv2.FONT_HERSHEY_SIMPLEX,
                            0.7, color, 2, cv2.LINE_AA)
        else:
            cv2.putText(frame, "no gesture", (12, line_y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.9, (255, 255, 255), 2, cv2.LINE_AA)
        # 保存带标注帧，交由主线程渲染到 pygame 窗口（不在子线程调用 OpenCV GUI）
        with self._preview_lock:
            self.preview_frame = frame.copy()