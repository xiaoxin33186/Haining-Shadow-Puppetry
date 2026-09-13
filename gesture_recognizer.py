# -*- coding: utf-8 -*-
"""摄像头双手抽象手势识别：挥手(wave) / 捏合·搓手(pinch)。

基于 MediaPipe HandLandmarker（新版 Tasks API，兼容 mediapipe >= 1.0）+ OpenCV，
在独立后台线程运行，检测到手势后回调主流程。
缺依赖（mediapipe tasks / opencv）或模型不可用时优雅降级
（available 为 False、start 直接返回），不影响既有的文字/语音交互主链路。

首次运行会在 projects 目录下自动下载 hand_landmarker.task 模型到 ./models/。

双手识别：num_hands=2，借助 handedness 区分为 left/right，
每只手独立分类、独立消抖（挥手滑动窗口 / 连续确认帧均按手分开）。
回调接口为 callback(hand: "left"|"right", gesture_name: "wave"|"pinch")，
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
    """是否具备手势识别条件（依赖齐全且模型文件已就位）。

    不触发联网下载，供主程序在导入时安全判断，避免 HAS_GESTURE 恒为 True。
    """
    return cv2 is not None and _HAS_TASKS and os.path.exists(_MODEL_PATH)


class GestureRecognizer:
    """后台线程持续读取摄像头并分类手势。"""

    # 可识别的手势名称：单手（wave/pinch/fist/kick/push/pull）与双手（cross/guard）
    GESTURE_NAMES = ("wave", "pinch", "fist", "kick", "push", "pull", "cross", "guard")

    def __init__(self, callback, camera_index=0, cooldown=2.0,
                 pinch_ratio=0.30, wave_window=1.5, confirm_frames=8,
                 show_preview=True, move_callback=None):
        self.callback = callback              # callback(hand, gesture_name)，在主流程线程调用
        # 连续手部位置回调：move_callback(hand, x, y)；手不在画面时 x/y 为 None
        self.move_callback = move_callback
        self.camera_index = camera_index
        self.cooldown = cooldown              # 同一手势两次触发的最小间隔（秒）
        self.pinch_ratio = pinch_ratio        # 拇指-食指间距 / 手尺寸 < 该值视为捏合
        self.wave_window = wave_window        # 挥手方向变化的观察窗口（秒）
        self.confirm_frames = confirm_frames  # 手势需连续稳定出现 N 帧才触发（防误触发/降速）
        self.show_preview = show_preview      # 是否生成实时预览帧（由主线程渲染）
        self.preview_frame = None             # 最新带标注预览帧(BGR)，供主线程读取
        self._preview_lock = threading.Lock()
        self._stop = False
        self._thread = None
        self._last_trigger = {}               # {(hand, gesture): 最近触发时间}，冷却按 手+手势 分开
        # 以下状态均按手 (left/right/unknown/both) 分开，避免双手互相污染
        self._wave_dirs = {"left": [], "right": [], "unknown": []}   # 挥手滑动窗口
        self._frame_streak = {"left": 0, "right": 0, "unknown": 0, "both": 0}  # 当前手势连续出现帧数
        self._last_gesture = {"left": None, "right": None, "unknown": None, "both": None}  # 最近一帧识别到的手势

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
        print("[手势识别] 摄像头手势识别已启动（双手独立）："
              "挥手→拱手礼，捏合→抓耳挠腮，握拳→出拳，张开+举手过头→踢腿，"
              "张开+平推胸前→前冲，张开+放低→后撤；双手交叉→格挡，双手护头→受击。")
        return True

    def stop(self):
        self._stop = True

    def is_alive(self):
        """手势线程是否仍在运行（供主程序同步 UI 状态）。"""
        return self._thread is not None and self._thread.is_alive()

    @staticmethod
    def _dist(a, b):
        return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5

    def _classify(self, lm, hand):
        """根据 21 个手部关键点判断单手手势。lm 为关键点列表。

        hand: 该手归属的 "left"/"right"/"unknown"，用于把挥手滑动窗口等状态按手隔离。
        返回: "fist"/"pinch"/"pull"/"kick"/"push"/"wave" 之一，或 None。
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

        # 捏合/搓手：拇指尖与食指尖距离极小，且【食指挺直】+ 中指/无名指/小指伸展开
        #     → 握拳时食指卷曲、其余三指也弯，不满足这些条件
        if (self._dist(lm[4], lm[8]) / hand_size < self.pinch_ratio
                and ext[8] > 0.85
                and curl[12] > 0.55 and curl[16] > 0.45 and curl[20] > 0.40):
            return "pinch"

        # 手掌张开 + 四指伸直 → 用【手腕在画面中的高度】区分（无需手指细节）：
        #   举高过头(y小) = 踢腿；胸口高度(中区) = 前冲
        #   （低位张开不触发动作，避免放松垂手误触）
        if (spread > 0.35 and all(ext[t] > 0.75 for t in tips)):
            if wrist.y < 0.40:
                return "kick"
            if wrist.y < 0.65:
                return "push"
            return None

        # 挥手：手掌张开 + 手腕在一个观察窗口内水平摆动到足够幅度（抗抖动）
        if spread <= 0.35:
            return None
        now = time.time()
        win = self._wave_dirs[hand]
        win.append((wrist.x, now))
        win = [(x, t) for x, t in win if now - t <= self.wave_window]
        self._wave_dirs[hand] = win
        if len(win) >= 6:
            xs = [x for x, _ in win]
            # 手腕水平位移幅度足够大即视为挥手
            if max(xs) - min(xs) >= 0.08:
                self._wave_dirs[hand] = []
                return "wave"
        return None

    def _classify_two_hands(self, hands):
        """双手整体手势：交叉→格挡(cross)，护头→受击(guard)，双拳护胸→后撤(pull)。

        hands: 两手的关键点列表（顺序不定），返回手势名或 None。
        """
        h0, h1 = hands[0], hands[1]
        w0, w1 = h0[0], h1[0]
        hs0 = self._dist(w0, h0[9])
        hs1 = self._dist(w1, h1[9])
        if min(hs0, hs1) < 1e-6:
            return None
        # 双拳护胸：两手都握拳、收在胸口两侧 → 后撤（专属双手手势，与出拳完全不冲突）
        #   两腕距离中等（0.15~0.55，避开"交叉<0.16"），且双手都弯曲
        if (0.35 < w0.y < 0.75 and 0.35 < w1.y < 0.75
                and self._is_fist(h0) and self._is_fist(h1)
                and 0.15 < self._dist(w0, w1) < 0.55):
            return "pull"
        # 双手交叉：两手腕靠拢（归一化坐标距离足够小）
        if self._dist(w0, w1) < 0.16:
            return "cross"
        # 双手护头：两手腕均高于画面约 40% 处且手掌张开
        spread0 = self._dist(h0[8], h0[20]) / hs0
        spread1 = self._dist(h1[8], h1[20]) / hs1
        if w0.y < 0.40 and w1.y < 0.40 and spread0 > 0.40 and spread1 > 0.40:
            return "guard"
        return None

    @staticmethod
    def _is_fist(lm):
        """双手手势用：判定该手是否握拳（四指弯曲、食指弯进掌内）。"""
        wrist, palm = lm[0], lm[9]
        hs = GestureRecognizer._dist(wrist, palm)
        if hs < 1e-6:
            return False
        tips = (8, 12, 16, 20)
        curl = {t: GestureRecognizer._dist(lm[t], palm) / hs for t in tips}
        return all(curl[t] < 0.68 for t in tips) and curl[8] < 0.55

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
        """
        backends = [("DSHOW", cv2.CAP_DSHOW), ("MSMF", cv2.CAP_MSMF), ("ANY", cv2.CAP_ANY)]
        for name, backend in backends:
            try:
                cap = cv2.VideoCapture(self.camera_index, backend)
            except Exception:  # noqa: BLE001
                continue
            if not cap.isOpened():
                cap.release()
                continue
            ok_any = False
            for _ in range(10):  # 预热并检测黑帧
                ok, f = cap.read()
                if ok and f is not None and float(f.mean()) > 1.0:
                    print(f"[手势识别] 摄像头后端 {name} 可用，画面正常。")
                    return cap
                if ok:
                    ok_any = True
            print(f"[手势识别] 摄像头后端 {name} {'只返回黑帧' if ok_any else '读取失败'}，尝试下一个。")
            cap.release()
            time.sleep(0.3)  # 给驱动一点时间彻底释放，再试下一个后端
        return None

    def _run(self):
        cap = self._open_camera()
        if cap is None:
            print("[手势识别] 无法打开摄像头，摄像头手势功能已禁用。")
            return

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
        landmarker = vision.HandLandmarker.create_from_options(options)
        ts_ms = 0
        try:
            while not self._stop:
                ok, frame = cap.read()
                if not ok:
                    break
                # 归一化为 3 通道 BGR（部分摄像头会返回 4 通道/灰度，否则预览会花屏）
                if frame.ndim == 2:
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                elif frame.shape[2] == 4:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                frame = cv2.flip(frame, 1)  # 镜像，更符合操作直觉
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                ts_ms += 33
                result = landmarker.detect_for_video(mp_image, ts_ms)
                gesture = None       # 最近一次实际触发的手势（供预览显示）
                gesture_hands = {}   # {hand: gesture}，供预览标记左右手
                two_gesture = None   # 双手整体手势（cross/guard/pull），供预览显示
                hand_pts = {}        # {hand: wrist landmark}，用于连续手部位置上报
                for i, lm in enumerate(result.hand_landmarks or []):
                    hand_pts[self._hand_label(result.handedness[i] if result.handedness else None)] = lm[0]

                if result.hand_landmarks:
                    # 先判定双手整体手势（双拳护胸→后撤 / 交叉→格挡 / 护头→受击）
                    # 命中时本帧只触发双手手势，跳过单手判定，避免左右手各自再触发
                    two = None
                    if len(result.hand_landmarks) >= 2:
                        two = self._classify_two_hands(result.hand_landmarks)
                        if two:
                            self._frame_streak["both"] = self._frame_streak["both"] + 1 if two == self._last_gesture["both"] else 1
                            self._last_gesture["both"] = two
                            two_gesture = two if self._frame_streak["both"] >= self.confirm_frames else None
                            if self._frame_streak["both"] >= self.confirm_frames:
                                self._frame_streak["both"] = 0
                                gesture = two
                                self._trigger("both", two)
                        else:
                            self._frame_streak["both"] = 0

                    if two is None:
                        # 无双手整体手势时，逐手独立判定、独立消抖
                        for i, lm in enumerate(result.hand_landmarks):
                            hand = self._hand_label(result.handedness[i] if result.handedness else None)
                            g = self._classify(lm, hand)
                            # 每只手：同一手势需连续 confirm_frames 帧稳定出现才触发（防瞬时误触发）
                            if g:
                                self._frame_streak[hand] = self._frame_streak[hand] + 1 if g == self._last_gesture[hand] else 1
                                self._last_gesture[hand] = g
                                gesture_hands[hand] = g
                                if self._frame_streak[hand] >= self.confirm_frames:
                                    self._frame_streak[hand] = 0
                                    gesture = g
                                    self._trigger(hand, g)
                            else:
                                self._frame_streak[hand] = 0
                    else:
                        for k in ("left", "right", "unknown"):
                            self._frame_streak[k] = 0
                            self._last_gesture[k] = None
                        gesture_hands = {}
                else:
                    for k in self._frame_streak:
                        self._frame_streak[k] = 0

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
                        gesture, gesture_hands, two_gesture)
        finally:
            landmarker.close()
            cap.release()  # 无论正常/异常都释放摄像头，避免占用
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

    def _draw_preview(self, frame, hand_landmarks, handedness, gesture, gesture_hands, two_gesture=None):
        """在画面上绘制多只手的骨架与识别结果，返回是否应退出预览(True=q/Esc)。"""
        h, w = frame.shape[:2]
        hand_colors = {"left": (255, 0, 0), "right": (0, 255, 0), "unknown": (0, 255, 255)}
        gesture_text = {
            "wave": "WAVE -> greet",
            "pinch": "PINCH -> scratch",
            "fist": "FIST -> punch",
            "kick": "KICK -> kick",
            "push": "PUSH -> lunge",
            "pull": "PULL(下压平掌) -> retreat",
        }
        two_gesture_text = {
            "cross": "BOTH CROSS -> block(双人格挡)",
            "guard": "BOTH GUARD -> hit(双手受击)",
            "pull": "BOTH PULL -> retreat(双拳护胸)",
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

        # 双手整体手势优先显示（黄色），随后显示每只手的实时判断
        line_y = 30
        if two_gesture:
            cv2.putText(frame, two_gesture_text.get(two_gesture, two_gesture),
                        (12, line_y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2, cv2.LINE_AA)
            line_y += 32
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