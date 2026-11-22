# -*- coding: utf-8 -*-
"""皮影新骨骼绑定台（统一骨骼 FK）

复刻根目录 adjust_puppet.py 的操作与面板，渲染改为 rig_lab/index.html 的
统一骨骼 FK：四个角色共用一套标准骨长 / 骨段方向 / 肩胯锚点，各自只保留
素材枢轴、axisOffset、hinge 与图片。

运行：
    python rig_lab\\rig_tool.py              交互窗口
    python rig_lab\\rig_tool.py --selftest   dummy 驱动自检
"""
import os
import sys
import json
import math
from pathlib import Path

import pygame

LAB = Path(__file__).resolve().parent
ROOT = LAB.parent
FONT_PATH = ROOT / "font.ttf"
BIND_PATH = LAB / "rig_bind.json"
BAKE_ROOT = LAB / "baked"
SELFTEST_ROOT = LAB / "_selftest"

WIN_W, WIN_H = 1440, 900
BG = (34, 36, 52)
ACCENT = (255, 230, 90)

PART_ORDER = [
    "head", "body",
    "right_hip", "right_knee", "left_hip", "left_knee",
    "right_elbow", "right_wrist", "left_elbow", "left_wrist",
]
PART_CN = {
    "head": "头", "body": "身体",
    "right_hip": "右大腿", "right_knee": "右小腿",
    "left_hip": "左大腿", "left_knee": "左小腿",
    "right_elbow": "右上臂", "right_wrist": "右前臂/手",
    "left_elbow": "左上臂", "left_wrist": "左前臂/手",
}
BONE_ORDER = ["upperR", "foreR", "upperL", "foreL",
              "thighR", "shinR", "thighL", "shinL"]
CHILD = {
    "upperR": "elbowR", "foreR": "wristR",
    "upperL": "elbowL", "foreL": "wristL",
    "thighR": "kneeR", "shinR": "ankleR",
    "thighL": "kneeL", "shinL": "ankleL",
}
BONE_CN = {
    "upperR": "右上臂", "foreR": "右前臂",
    "upperL": "左上臂", "foreL": "左前臂",
    "thighR": "右大腿", "shinR": "右小腿",
    "thighL": "左大腿", "shinL": "左小腿",
}
JOINT_CN = {
    "headEye": "头", "neck": "颈", "pelvis": "盆", "shoulder": "肩",
    "elbowR": "右肘", "wristR": "右腕", "elbowL": "左肘", "wristL": "左腕",
    "hipR": "右胯", "kneeR": "右膝", "ankleR": "右踝",
    "hipL": "左胯", "kneeL": "左膝", "ankleL": "左踝",
}
CHAR_KEYS = [
    "xinwukong_material",
    "new_sha_wujing_material",
    "tang_sanzang_material",
    "new_zhu_bajie_material",
]
POSE_KEYS = ["torso", "head", "shR", "elR", "shL", "elL",
             "hipR", "knR", "hipL", "knL", "rx", "ry"]

SKEL_LINKS = [
    ("neck", "pelvis"),
    ("neck", "headEye"),
    ("shoulder", "elbowR"), ("elbowR", "wristR"),
    ("shoulder", "elbowL"), ("elbowL", "wristL"),
    ("hipR", "kneeR"), ("kneeR", "ankleR"),
    ("hipL", "kneeL"), ("kneeL", "ankleL"),
    ("hipL", "hipR"),
]


# ---------------------------------------------------------------- 数据读取
def _read_js_value(path, marker):
    """从 window.X = {...}/[...] 中取出 JSON 值（文件本身是 JSON 兼容字面量）。"""
    text = path.read_text(encoding="utf-8")
    pos = text.index(marker)
    eq = text.index("=", pos) + 1
    starts = [i for i in (text.find("{", eq), text.find("[", eq)) if i >= 0]
    if not starts:
        raise ValueError(f"{path.name}: 找不到 {marker} 的值")
    start = min(starts)
    value, _ = json.JSONDecoder().raw_decode(text[start:])
    return value


REST_RIGS = _read_js_value(LAB / "rest.js", "window.REST_RIGS")
ACTION_KEYS = _read_js_value(LAB / "actions.js", "window.ACTION_KEYS")
ACTIONS = _read_js_value(LAB / "actions.js", "window.ACTIONS")

REST_RIGS = {k: REST_RIGS[k] for k in CHAR_KEYS if k in REST_RIGS}


# ---------------------------------------------------------------- 数学 / FK
def rad(deg):
    return deg * math.pi / 180.0


def rot_phi(vec, deg):
    """按 φ 增加 deg 度旋转向量（φ = atan2(x, y)，0 向下，+90 向右）。"""
    if not deg:
        return [vec[0], vec[1]]
    r = rad(deg)
    c, s = math.cos(r), math.sin(r)
    return [vec[0] * c + vec[1] * s, -vec[0] * s + vec[1] * c]


def add(a, b):
    return [a[0] + b[0], a[1] + b[1]]


def blank_pose():
    return {k: 0.0 for k in POSE_KEYS}


def standard_rig(rest):
    """取悟空的标准骨架：neck / shoulder / hips / phiTorso / 8 骨 len+phi。"""
    bones = {}
    for bn in BONE_ORDER:
        src = rest["bones"][bn]
        bones[bn] = {"len": float(src["len"]), "phi": float(src["phi"])}
    return {
        "neck": list(rest["neck"]),
        "phiTorso": float(rest["phiTorso"]),
        "shoulder": list(rest["shoulder"]),
        "hips": {
            "left": list(rest["hips"]["left"]),
            "right": list(rest["hips"]["right"]),
        },
        "head": list(rest["parts"]["head"].get("eyeOffset", [0.0, 0.0])),
        "bones": bones,
    }


STD = standard_rig(REST_RIGS[CHAR_KEYS[0]])


def make_rig(key):
    """把某角色的素材参数套到统一标准骨架上（等价 index.html rigOf）。"""
    src = REST_RIGS[key]
    parts = {}
    for p, d in src["parts"].items():
        nd = dict(d)
        nd["pivot"] = list(d.get("pivot", [0, 0]))
        nd["size"] = list(d.get("size", [1, 1]))
        nd["draw"] = int(d.get("draw", 0))
        nd["mirror"] = bool(d.get("mirror", False))
        nd["axisOffset"] = float(d.get("axisOffset", 0.0))
        if p == "head":
            nd["eyeOffset"] = list(STD["head"])
        parts[p] = nd
    bones = {}
    for bn in BONE_ORDER:
        b = dict(src["bones"][bn])
        b["len"] = STD["bones"][bn]["len"]
        b["phi"] = STD["bones"][bn]["phi"]
        b["axisOffset"] = float(b.get("axisOffset", 0.0))
        bones[bn] = b
    hinge = dict(src.get("hinge") or {})
    hinge.setdefault("elbowR", 1)
    hinge.setdefault("elbowL", 1)
    hinge.setdefault("kneeR", -1)
    hinge.setdefault("kneeL", -1)
    out = dict(src)
    out.update({
        "neck": list(STD["neck"]),
        "phiTorso": STD["phiTorso"],
        "shoulder": list(STD["shoulder"]),
        "hips": {"left": list(STD["hips"]["left"]),
                 "right": list(STD["hips"]["right"])},
        "parts": parts,
        "bones": bones,
        "hinge": hinge,
        "pelvis": list(src.get("pelvis", [0.0, 0.0])),
    })
    return out


def fk_build(rig, pose):
    """index.html build() 的 Python 版：返回 13 个关节与 10 个绘制角。"""
    j = {"pelvis": [0.0, 0.0]}
    j["neck"] = rot_phi(rig["neck"], pose["torso"])
    j["shoulder"] = rot_phi(rig["shoulder"], pose["torso"])
    j["hipL"] = rot_phi(rig["hips"]["left"], pose["torso"])
    j["hipR"] = rot_phi(rig["hips"]["right"], pose["torso"])
    j["headEye"] = add(j["neck"], rot_phi(
        rig["parts"]["head"].get("eyeOffset", [0.0, 0.0]), pose["torso"]))

    hinge = rig["hinge"]
    delta = {
        "upperR": pose["shR"],
        "foreR": pose["shR"] + pose["elR"],
        "upperL": pose["shL"],
        "foreL": pose["shL"] + pose["elL"],
        "thighR": pose["hipR"],
        "shinR": pose["hipR"] + pose["knR"] * hinge["kneeR"],
        "thighL": pose["hipL"],
        "shinL": pose["hipL"] + pose["knL"] * hinge["kneeL"],
    }
    angles = {}
    for bn in BONE_ORDER:
        b = rig["bones"][bn]
        phi = b["phi"] + pose["torso"] + delta[bn]
        j[CHILD[bn]] = add(j[b["base"]], [
            math.sin(rad(phi)) * b["len"],
            math.cos(rad(phi)) * b["len"],
        ])
        angles[bn] = -phi + b["axisOffset"]

    phi_torso = rig["phiTorso"] + pose["torso"]
    angles["body"] = -phi_torso + rig["parts"]["body"].get("axisOffset", 0.0)
    angles["head"] = (-phi_torso + rig["parts"]["head"].get("axisOffset", 0.0)
                      - pose["head"])

    rx, ry = pose.get("rx", 0.0), pose.get("ry", 0.0)
    if rx or ry:
        for k in j:
            j[k] = [j[k][0] + rx, j[k][1] + ry]
    return {"j": j, "angles": angles}


def part_anchor_joint(rig, part):
    if part == "body":
        return "neck"
    if part == "head":
        return "headEye"
    bone = rig["parts"][part].get("bone")
    return rig["bones"][bone]["base"] if bone in rig["bones"] else "neck"


def part_joints(rig, part):
    """部件相关的两个关节（原工具 PART_CONNECT 的新版）。"""
    if part == "body":
        return ("neck", "pelvis")
    if part == "head":
        return ("headEye", "neck")
    bone = rig["parts"][part].get("bone")
    if bone in rig["bones"]:
        return (rig["bones"][bone]["base"], CHILD[bone])
    return None


def image_tilt_deg(x1, y1, x2, y2):
    """枢轴 -> 不透明质心的图像内倾角（O 自动竖直用，非旧骨骼渲染）。"""
    dx = abs(x1 - x2)
    dy = abs(y1 - y2)
    if x1 == x2:
        return 180.0 if y1 > y2 else 0.0
    if y1 == y2:
        return 0.0
    angle = math.degrees(math.atan(dx / dy)) if dy else 0.0
    if x1 < x2:
        result = -(180 - angle) if y1 > y2 else -angle
    else:
        result = 180 - angle if y1 > y2 else angle
    return result + 360.0 if result < 0 else result


def rotate_bound_pg(img, angle_cw, pivot_x, pivot_y):
    """原 pygame_Alpha.rotate_bound_pg：返回旋转图与枢轴在新图中的位置。"""
    rotated = pygame.transform.rotate(img, -angle_cw)
    w, h = img.get_size()
    nw, nh = rotated.get_size()
    dx = pivot_x - w / 2.0
    dy = pivot_y - h / 2.0
    r = rad(angle_cw)
    mvx = nw / 2.0 + (dx * math.cos(r) - dy * math.sin(r))
    mvy = nh / 2.0 + (dx * math.sin(r) + dy * math.cos(r))
    return rotated, mvx, mvy


# ---------------------------------------------------------------- 工具主体
class RigTool(object):
    def __init__(self, screen):
        self.screen = screen
        self.sw, self.sh = screen.get_size()
        self.running = True
        self.font = (pygame.font.Font(str(FONT_PATH), 16)
                     if FONT_PATH.exists() else pygame.font.SysFont(None, 16))
        self.small = (pygame.font.Font(str(FONT_PATH), 14)
                      if FONT_PATH.exists() else pygame.font.SysFont(None, 14))

        self.rigs = {k: make_rig(k) for k in CHAR_KEYS}
        self.char_idx = 0
        self.key = CHAR_KEYS[0]
        self.rig = self.rigs[self.key]

        self.bind = self._load_bind()
        self.st = None
        self.mirror = {}

        self.action_names = list(ACTIONS.keys())
        default_act = ("idle" if "idle" in ACTIONS
                       else (self.action_names[0] if self.action_names else ""))
        self.action_idx = (self.action_names.index(default_act)
                           if default_act else 0)
        self.frames = []
        self.fps = 30.0
        self.frame = 0
        self.pose = blank_pose()
        self.paused = True
        self.cycle_mode = False
        self.big_pose = False

        self.selected = None
        self.dragging = False
        self.rotating = False
        self.last_mouse = None
        self.last_world = None
        self.pick_pivot = False
        self.pick_tip = False
        self.show_all = False
        self.show_map = True
        self.scene_mirror = False
        self.msg = ""

        self.view = None
        self.view_zoom = 1.0
        self.manual_zoom = False
        self.play_acc = 0.0

        self.images = {}
        self.switch_char(self.key, initial=True)
        self.load_action(default_act)
        self.update_view()

    # ------------------------------------------------------------ 绑定数据
    def _load_bind(self):
        if not BIND_PATH.exists():
            return {}
        try:
            data = json.loads(BIND_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _num(self, value, default):
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    def _state_for(self, key):
        rig = self.rigs[key]
        d = self.bind.setdefault(key, {})
        for f in ("pivot", "scale", "rot", "off", "draw", "tip", "mirror"):
            if not isinstance(d.get(f), dict):
                d[f] = {}
        if not isinstance(d.get("lens"), dict):
            d["lens"] = {}
        if not isinstance(d.get("phis"), dict):
            d["phis"] = {}
        if not isinstance(d.get("aoff"), dict):
            d["aoff"] = {}
        for p, defn in rig["parts"].items():
            pv = d["pivot"].get(p)
            if not (isinstance(pv, (list, tuple)) and len(pv) == 2):
                pv = defn.get("pivot", [0, 0])
            d["pivot"][p] = [self._num(pv[0], 0), self._num(pv[1], 0)]

            sc = self._num(d["scale"].get(p, 1.0), 1.0)
            d["scale"][p] = min(2.5, max(0.4, sc))
            d["rot"][p] = round(self._num(d["rot"].get(p, 0.0), 0.0), 2)

            tp = d["tip"].get(p)
            if not (isinstance(tp, (list, tuple)) and len(tp) == 2):
                tp = None
            d["tip"][p] = (None if tp is None else
                           [self._num(tp[0], 0), self._num(tp[1], 0)])

            off = d["off"].get(p)
            if not (isinstance(off, (list, tuple)) and len(off) == 2):
                off = [0, 0]
            d["off"][p] = [self._num(off[0], 0), self._num(off[1], 0)]
            d["draw"][p] = int(self._num(d["draw"].get(p, defn.get("draw", 0)),
                                        defn.get("draw", 0)))
            baked = (BAKE_ROOT / key / (p + ".png")).exists()
            d["mirror"][p] = bool(d["mirror"].get(
                p, False if baked else defn.get("mirror", False)))
        return d

    def part_reach(self, part):
        """素材从枢轴沿骨向到最远可见像素的距离（已乘缩放）。"""
        img = self.imgs().get(part)
        if img is None:
            return 0.0
        if self.mirror.get(part, False):
            img = pygame.transform.flip(img, True, False)
            px = img.get_width() - 1 - self.pivot(part)[0]
        else:
            px = self.pivot(part)[0]
        py = self.pivot(part)[1]
        rot, mx, my = rotate_bound_pg(img, self.angle_of(part), px, py)
        rect = rot.get_bounding_rect(min_alpha=10)
        if rect.height <= 0:
            return 0.0
        far = rect.bottom - my
        if far <= 0:
            far = float(rect.height)
        return far * self.scale(part)

    def derive_lens(self):
        """按素材实际像素长度量出本角色骨长；不动枢轴和偏移。"""
        self.pose = blank_pose()
        self.big_pose = False
        self.rig.update(fk_build(self.rig, self.pose))
        std = {bn: float(b.get("len_std", b["len"]))
               for bn, b in self.rig["bones"].items()}
        lens = dict(std)
        for bn, part in (("upperR", "right_elbow"), ("upperL", "left_elbow"),
                         ("thighR", "right_hip"), ("thighL", "left_hip")):
            r = self.part_reach(part)
            if r > 0:
                base = std[bn]
                lens[bn] = min(base * 1.8, max(base * 0.5, r))
        has_tip = set()
        bad_tip = 0
        for bn, b in self.rig["bones"].items():
            part = b.get("part")
            tp = (self.st.get("tip") or {}).get(part) if part else None
            if tp:
                img = self.imgs().get(part)
                w, h = img.get_size() if img else (0, 0)
                if not (0 <= tp[0] < w and 0 <= tp[1] < h):
                    # 标记因烘焙跑到图外：沿枢轴→标记方向压回图内，取可达最长
                    bad_tip += 1
                    px0, py0 = self.pivot(part)
                    dx0, dy0 = tp[0] - px0, tp[1] - py0
                    lo, hi = 0.0, 1.0
                    for _ in range(20):
                        mid = (lo + hi) / 2.0
                        x, y = px0 + dx0 * mid, py0 + dy0 * mid
                        if 0 <= x < w and 0 <= y < h:
                            lo = mid
                        else:
                            hi = mid
                    tp = [px0 + dx0 * lo, py0 + dy0 * lo]
                    self.st["tip"][part] = [round(tp[0], 1), round(tp[1], 1)]
                pv = self.pivot(part)
                vx, vy = tp[0] - pv[0], tp[1] - pv[1]
                if self.mirror.get(part, False):
                    vx = -vx
                lens[bn] = math.hypot(vx, vy) * self.scale(part)
                # 有骨长标记的段，位置由关节决定，偏移归零
                self.st["off"][part] = [0.0, 0.0]
                has_tip.add(bn)
        for src, dst in (("upperR", "foreR"), ("upperL", "foreL"),
                         ("thighR", "shinR"), ("thighL", "shinL")):
            if std.get(src) and dst not in has_tip:
                lens[dst] = std[dst] * lens[src] / std[src]
        self.st["lens"] = {k: float(v) for k, v in lens.items()}
        for bn, b in self.rig["bones"].items():
            b["len"] = float(lens[bn])
        phis = {}
        # 方向：从小到大逐段搜索，让骨段末端正好落在标记点上
        for bn in BONE_ORDER:
            if bn not in has_tip:
                continue
            b = self.rig["bones"][bn]
            part = b["part"]
            tp = self.st["tip"][part]
            pv = self.pivot(part)
            dx, dy = tp[0] - pv[0], tp[1] - pv[1]
            if self.mirror.get(part, False):
                dx = -dx
            s = self.scale(part)
            o = self.off(part)
            # 以素材当前朝向为准：骨段朝向 = 标记轴方向，素材本身不转
            eng = fk_build(self.rig, self.pose)
            base = eng["j"].get(b["base"])
            if base:
                a_old = eng["angles"][bn]
                tw = self.tip_world(part)
                vx2 = tw[0] - base[0] - o[0]
                vy2 = tw[1] - base[1] - o[1]
                phi = math.degrees(math.atan2(vx2, vy2))
                phi = (phi + 180.0) % 360.0 - 180.0
                b["phi"] = phi
                # 反算轴偏，保证素材画出的角度和刚才完全一致
                b["axisOffset"] = a_old + phi
                self.st["aoff"][part] = round(float(b["axisOffset"]), 3)
                phis[bn] = phi
            self.rig.update(fk_build(self.rig, self.pose))
        self.st["phis"] = {k: round(float(v), 3) for k, v in phis.items()}
        self.st["lens"] = {k: float(v) for k, v in lens.items()}
        self.save_bind()
        tip_note = (f"，另有 {bad_tip} 段标记超出素材范围（请用 T 重标）"
                    if bad_tip else "")
        self.msg = ("已按素材量出本角色骨长：上臂 %.1f 前臂 %.1f 大腿 %.1f 小腿 %.1f"
                    % (lens["upperR"], lens["foreR"], lens["thighR"], lens["shinR"])
                    + tip_note)

    def save_bind(self):
        BIND_PATH.parent.mkdir(parents=True, exist_ok=True)
        out = {}
        for key in CHAR_KEYS:
            st = self._state_for(key)
            if key == self.key:
                # 当前角色的镜像开关存在 self.mirror，落盘前同步进绑定
                for p in self.rig["parts"]:
                    st["mirror"][p] = bool(self.mirror.get(p, False))
            out[key] = {
                "pivot": {p: [float(v[0]), float(v[1])]
                          for p, v in st["pivot"].items() if p in self.rigs[key]["parts"]},
                "scale": {p: round(float(v), 4)
                          for p, v in st["scale"].items() if p in self.rigs[key]["parts"]},
                "rot": {p: round(float(v), 2)
                        for p, v in st["rot"].items() if p in self.rigs[key]["parts"]},
                "off": {p: [float(v[0]), float(v[1])]
                        for p, v in st["off"].items() if p in self.rigs[key]["parts"]},
                "draw": {p: int(v)
                         for p, v in st["draw"].items() if p in self.rigs[key]["parts"]},
                "mirror": {p: bool(v)
                           for p, v in st["mirror"].items()
                           if p in self.rigs[key]["parts"]},
                "lens": {bn: round(float(v), 3)
                         for bn, v in st.get("lens", {}).items()
                         if bn in self.rigs[key]["bones"]},
                "tip": {p: [float(v[0]), float(v[1])]
                        for p, v in st.get("tip", {}).items()
                        if v and p in self.rigs[key]["parts"]},
                "phis": {bn: round(float(v), 3)
                         for bn, v in st.get("phis", {}).items()
                         if bn in self.rigs[key]["bones"]},
                "aoff": {k: round(float(v), 3)
                         for k, v in st.get("aoff", {}).items()
                         if k in self.rigs[key]["bones"]
                         or k in self.rigs[key]["parts"]},
            }
        with open(BIND_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        self.msg = f"已保存绑定数据 -> rig_lab/{BIND_PATH.name}（重启自动加载）"

    # 当前部件参数
    def pivot(self, p):
        return self.st["pivot"][p]

    def scale(self, p):
        return self.st["scale"][p]

    def rot(self, p):
        return self.st["rot"][p]

    def off(self, p):
        return self.st["off"][p]

    def draw(self, p):
        return self.st["draw"][p]

    # ------------------------------------------------------------ 角色 / 动作
    def load_images(self, key):
        if key in self.images:
            return self.images[key]
        rig = self.rigs[key]
        mats = {}
        baked = BAKE_ROOT / key
        for p in PART_ORDER:
            path = baked / (p + ".png")
            if not path.exists():
                path = ROOT / rig["folder"] / (p + ".png")
            try:
                mats[p] = pygame.image.load(str(path)).convert_alpha()
            except Exception:
                pass
        self.images[key] = mats
        return mats

    def imgs(self):
        return self.images.get(self.key, {})

    def switch_char(self, key, initial=False):
        if key not in self.rigs:
            return
        self.key = key
        self.char_idx = CHAR_KEYS.index(key)
        self.rig = self.rigs[key]
        self.st = self._state_for(key)
        for bn, b in self.rig["bones"].items():
            b.setdefault("len_std", float(b["len"]))
            b.setdefault("phi_std", float(b["phi"]))
            b.setdefault("aoff_std", float(b.get("axisOffset", 0.0)))
            b["len"] = float(self.st["lens"].get(bn, b["len_std"]))
            b["phi"] = float(self.st["phis"].get(bn, b["phi_std"]))
            # 轴偏在派生骨长时按“部件名”落盘，兼容旧的“骨名”键
            part = b.get("part")
            aoff = self.st["aoff"]
            b["axisOffset"] = float(aoff.get(part, aoff.get(bn, b["aoff_std"])))
        self.load_images(key)
        self.mirror = {p: bool(self.st["mirror"].get(p, False))
                       for p, d in self.rig["parts"].items()}
        self.selected = None
        self.dragging = False
        self.rotating = False
        self.last_mouse = None
        self.last_world = None
        self.manual_zoom = False
        self.view = None
        if not initial:
            if self.big_pose:
                self.pose = blank_pose()
                self.pose.update({"shR": 90, "shL": 90,
                                  "hipR": 45, "hipL": -35})
            else:
                self.apply_frame(self.frame)
            self.msg = f"已切换角色: {self.rig.get('name', key)} ({key})"

    def load_action(self, name):
        if name not in ACTIONS:
            return
        self.big_pose = False
        self.action_idx = self.action_names.index(name)
        data = ACTIONS[name]
        # 用动作原始帧，不做“首帧对齐 idle”的平移，保证与主程序逐帧一致。
        self.frames = data.get("frames", [])
        self.fps = float(data.get("fps") or 30.0)
        self.frame = 0
        self.manual_zoom = False
        self.view = None
        self.play_acc = 0.0
        if self.frames:
            self.pose = self.pose_from_row(self.frames[0])
        self.paused = True

    def pose_from_row(self, row):
        p = blank_pose()
        for i, k in enumerate(ACTION_KEYS):
            if i < len(row):
                p[k] = float(row[i])
        p["rx"] = float(row[10]) if len(row) > 10 else 0.0
        p["ry"] = float(row[11]) if len(row) > 11 else 0.0
        return p

    def apply_frame(self, idx):
        if not self.frames:
            return
        self.frame = max(0, min(len(self.frames) - 1, int(idx)))
        self.pose = self.pose_from_row(self.frames[self.frame])

    def switch_action(self, delta, keep_playing=False):
        if not self.action_names:
            return
        self.action_idx = (self.action_idx + delta) % len(self.action_names)
        self.load_action(self.action_names[self.action_idx])
        if keep_playing:
            self.paused = False
        self.msg = f"已切换动作: {self.action_names[self.action_idx]}"

    def advance(self, n):
        if not self.frames:
            return
        self.frame += n
        if self.frame >= len(self.frames):
            if self.cycle_mode:
                self.switch_action(1, keep_playing=True)
                return
            self.frame %= len(self.frames)
        self.pose = self.pose_from_row(self.frames[self.frame])

    # ------------------------------------------------------------ 坐标
    def stage_rect(self):
        left, top = 12, 140
        right = self.sw - 268 if self.sw >= 900 else self.sw - 12
        bottom = self.sh - 136 if self.sh >= 620 else self.sh - 12
        if right - left < 200:
            left, right = 8, self.sw - 12
        if bottom - top < 160:
            top, bottom = 8, self.sh - 12
        return left, top, right, bottom

    def bounds(self):
        minx = miny = float("inf")
        maxx = maxy = float("-inf")

        def push(x, y):
            nonlocal minx, miny, maxx, maxy
            minx = min(minx, x)
            miny = min(miny, y)
            maxx = max(maxx, x)
            maxy = max(maxy, y)

        for p in PART_ORDER:
            img = self.imgs().get(p)
            if img is None:
                continue
            w, h = img.get_size()
            pv = self.pivot(p)
            w, h = img.get_size()
            pvx = (w - 1 - pv[0]) if self.mirror.get(p, False) else pv[0]
            pos = self.part_pos(p)
            ang = self.angle_of(p)
            s = self.scale(p)
            r = rad(ang)
            cs, sn = math.cos(r), math.sin(r)
            for cx, cy in ((0, 0), (w, 0), (0, h), (w, h)):
                lx = (cx - pvx) * s
                ly = (cy - pv[1]) * s
                push(pos[0] + lx * cs - ly * sn,
                     pos[1] + lx * sn + ly * cs)
        for x, y in self.rig["j"].values():
            push(x, y)
        if not math.isfinite(minx):
            minx, miny, maxx, maxy = -100.0, -300.0, 100.0, 100.0
        return {"x": minx, "y": miny,
                "w": max(1.0, maxx - minx), "h": max(1.0, maxy - miny)}

    def sync_fk(self):
        fk = fk_build(self.rig, self.pose)
        self.rig["j"] = fk["j"]
        self.rig["angles"] = fk["angles"]

    def update_view(self):
        self.sync_fk()
        left, top, right, bottom = self.stage_rect()
        cx = (left + right) / 2.0
        cy = (top + bottom) / 2.0
        # 视图只在首次/切角色/改窗口时自动适配；之后保持固定，避免拖动时反复缩放。
        # 缩放固定为主程序同款 0.79，人物以骨盆为锚点居中，保证与正式渲染一致。
        if self.view is not None:
            self.view["cx"] = cx
            self.view["cy"] = cy + 65.0 * self.view_zoom
            self.view["zoom"] = self.view_zoom
            return
        if not self.manual_zoom:
            self.view_zoom = 0.79
        self.view = {
            "zoom": self.view_zoom,
            "cx": cx, "cy": cy + 65.0 * self.view_zoom,
            "bcx": 0.0, "bcy": 0.0,
        }

    def to_screen(self, x, y):
        v = self.view
        sgn = -1.0 if self.scene_mirror else 1.0
        return (v["cx"] + sgn * (x - v["bcx"]) * v["zoom"],
                v["cy"] + (y - v["bcy"]) * v["zoom"])

    def to_world(self, mx, my):
        v = self.view
        if self.scene_mirror:
            wx = v["bcx"] - (mx - v["cx"]) / v["zoom"]
        else:
            wx = v["bcx"] + (mx - v["cx"]) / v["zoom"]
        wy = v["bcy"] + (my - v["cy"]) / v["zoom"]
        return wx, wy

    # ------------------------------------------------------------ FK 部件
    def part_pos(self, part):
        o = self.off(part)
        if part == "body":
            p = self.rig["j"]["neck"]
        elif part == "head":
            p = self.rig["j"]["headEye"]
        else:
            bone = self.rig["parts"][part].get("bone")
            p = self.rig["j"][self.rig["bones"][bone]["base"]]
        return [p[0] + o[0], p[1] + o[1]]

    def part_angle(self, part):
        if part in ("body", "head"):
            return self.rig["angles"][part]
        bone = self.rig["parts"][part].get("bone")
        return self.rig["angles"].get(bone, 0.0)

    def angle_of(self, part):
        return self.part_angle(part) + self.rot(part)

    def bone_eye_deviation(self):
        """max |关节计算位置 - 部件期望位置|；统一 FK 下应恒为 0。"""
        worst = 0.0
        for p in PART_ORDER:
            if p not in self.rig["parts"]:
                continue
            joint = self.rig["j"].get(part_anchor_joint(self.rig, p))
            if not joint:
                continue
            off = self.off(p)
            expected = (joint[0] + off[0], joint[1] + off[1])
            actual = self.part_pos(p)
            worst = max(worst, math.hypot(actual[0] - expected[0],
                                          actual[1] - expected[1]))
        return worst

    # ------------------------------------------------------------ 绘画
    def draw_character(self):
        order = sorted(
            [p for p in self.rig["parts"]],
            key=lambda p: self.draw(p),
        )
        for part in order:
            img = self.imgs().get(part)
            if img is None:
                continue
            pv = self.pivot(part)
            iw0, _ = img.get_size()
            mv_px = (iw0 - 1 - pv[0]) if self.mirror.get(part, False) else pv[0]
            px, py = int(round(mv_px)), int(round(pv[1]))
            ang = self.angle_of(part)
            s = self.scale(part) * (self.view["zoom"] if self.view else 1.0)
            src = img
            if self.mirror.get(part, False):
                src = pygame.transform.flip(src, True, False)
            rotated, mvx, mvy = rotate_bound_pg(src, ang, px, py)
            if abs(s - 1.0) > 1e-4:
                nw = max(1, int(round(rotated.get_width() * s)))
                nh = max(1, int(round(rotated.get_height() * s)))
                rotated = pygame.transform.smoothscale(rotated, (nw, nh))
                mvx *= s
                mvy *= s
            pos = self.part_pos(part)
            sx, sy = self.to_screen(pos[0], pos[1])
            x = sx - mvx
            y = sy - mvy
            if self.scene_mirror:
                rotated = pygame.transform.flip(rotated, True, False)
                x = sx - (rotated.get_width() - mvx)
            self.screen.blit(rotated, (int(round(x)), int(round(y))))

    def draw_skeleton(self):
        j = self.rig["j"]
        for a, b in SKEL_LINKS:
            if a in j and b in j:
                pygame.draw.line(
                    self.screen, (92, 106, 132),
                    self.to_screen(*j[a]), self.to_screen(*j[b]), 1)

    def draw_markers(self):
        # V 关时只保留选中部件；V 开显示全部骨眼与关节
        for p in PART_ORDER:
            if p not in self.rig["parts"]:
                continue
            pos = self.part_pos(p)
            sp = self.to_screen(pos[0], pos[1])
            if p == self.selected:
                pygame.draw.circle(self.screen, ACCENT,
                                   (int(sp[0]), int(sp[1])), 6)
                lab = self.small.render(p, True, ACCENT)
                self.screen.blit(lab, (int(sp[0]) + 7, int(sp[1]) - 7))
            elif self.show_all:
                pygame.draw.circle(self.screen, (0, 255, 255),
                                   (int(sp[0]), int(sp[1])), 4)
                lab = self.small.render(p, True, (0, 255, 255))
                self.screen.blit(lab, (int(sp[0]) + 6, int(sp[1]) - 6))

        # 骨长端标记（橙色菱形）+ 轴连线 + 长度
        for p in (PART_ORDER if self.show_all else [self.selected]):
            if not p:
                continue
            tw = self.tip_world(p)
            if tw is None:
                continue
            a = self.to_screen(*self.part_pos(p))
            b = self.to_screen(*tw)
            img = self.imgs().get(p)
            tp = self.st["tip"][p]
            valid = bool(img) and 0 <= tp[0] < img.get_width() \
                and 0 <= tp[1] < img.get_height()
            col = (255, 150, 60) if valid else (255, 80, 80)
            pygame.draw.line(self.screen, col,
                             (int(a[0]), int(a[1])),
                             (int(b[0]), int(b[1])), 1)
            bx, by = int(b[0]), int(b[1])
            pygame.draw.polygon(self.screen, col,
                                [(bx, by - 5), (bx + 5, by),
                                 (bx, by + 5), (bx - 5, by)])
            pv = self.pivot(p)
            if valid:
                L = math.hypot(tp[0] - pv[0], tp[1] - pv[1]) * self.scale(p)
                lab = self.small.render(f"{L:.0f}", True, col)
            else:
                lab = self.small.render("超出素材", True, col)
            self.screen.blit(lab, (bx + 7, by - 6))

        # 选中部件的两个连接关节 + 连线
        if self.selected:
            js = part_joints(self.rig, self.selected)
            if js:
                a = self.rig["j"].get(js[0])
                b = self.rig["j"].get(js[1])
                if a and b:
                    sa = self.to_screen(a[0], a[1])
                    sb = self.to_screen(b[0], b[1])
                    pygame.draw.line(self.screen, (140, 155, 185),
                                     (int(sa[0]), int(sa[1])),
                                     (int(sb[0]), int(sb[1])), 1)
                for jn in js:
                    p = self.rig["j"].get(jn)
                    if not p:
                        continue
                    sp = self.to_screen(p[0], p[1])
                    pygame.draw.circle(self.screen, (255, 70, 70),
                                       (int(sp[0]), int(sp[1])), 5)
                    lab = self.small.render(jn, True, (255, 70, 70))
                    self.screen.blit(lab, (int(sp[0]) + 6, int(sp[1]) - 6))
        elif self.show_all:
            for jn, p in self.rig["j"].items():
                sp = self.to_screen(p[0], p[1])
                pygame.draw.circle(self.screen, (255, 70, 70),
                                   (int(sp[0]), int(sp[1])), 3)

    def preview_geom(self):
        pw = 240
        x0 = max(12, self.sw - pw - 12)
        y0 = 150
        ph = self.sh - y0 - 70
        if ph < 90:
            ph = 90
        return x0, y0, pw, ph

    def preview_place(self):
        x0, y0, pw, ph = self.preview_geom()
        img = self.imgs().get(self.selected)
        if img is None:
            return None
        iw, ih = img.get_size()
        top = y0 + 22
        avail = ph - 28
        z = min(pw / iw, avail / ih)
        dx = x0 + (pw - iw * z) / 2
        dy = top + (avail - ih * z) / 2
        return x0, y0, pw, ph, z, dx, dy, iw, ih

    def draw_preview(self):
        data = self.preview_place()
        if not data:
            return
        x0, y0, pw, ph, z, dx, dy, iw, ih = data
        pane = pygame.Surface((pw, ph), pygame.SRCALPHA)
        pane.fill((15, 15, 25, 220))
        self.screen.blit(pane, (x0, y0))
        baked = (BAKE_ROOT / self.key / (self.selected + ".png")).exists()
        label = self.small.render(
            ("部件烘焙图" if baked else "部件原图") + "  点这儿定枢轴(关节):",
            True, (255, 255, 255))
        self.screen.blit(label, (x0 + 6, y0 + 2))
        img = self.imgs()[self.selected]
        scaled = pygame.transform.smoothscale(
            img, (max(1, int(iw * z)), max(1, int(ih * z))))
        self.screen.blit(scaled, (int(dx), int(dy)))
        pv = self.pivot(self.selected)
        cx = int(dx + pv[0] * z)
        cy = int(dy + pv[1] * z)
        pygame.draw.line(self.screen, (255, 60, 60), (cx - 10, cy), (cx + 10, cy), 2)
        pygame.draw.line(self.screen, (255, 60, 60), (cx, cy - 10), (cx, cy + 10), 2)
        pygame.draw.circle(self.screen, (255, 255, 255), (cx, cy), 3)

    def draw_map(self):
        x0, y0, pw, ph = self.preview_geom()
        pane = pygame.Surface((pw, ph), pygame.SRCALPHA)
        pane.fill((15, 15, 25, 230))
        self.screen.blit(pane, (x0, y0))
        title = self.small.render("骨架结构示意图 (红=关节, 连线=骨架段)", True,
                                  (255, 255, 255))
        self.screen.blit(title, (x0 + 6, y0 + 2))
        pts = dict(self.rig["j"])
        if not pts:
            return
        top = y0 + 24
        avail = ph - 30
        xs = [p[0] for p in pts.values()]
        ys = [p[1] for p in pts.values()]
        mnx, mxx = min(xs), max(xs)
        mny, mxy = min(ys), max(ys)
        z = min((pw - 16) / max(1, mxx - mnx),
                (avail - 16) / max(1, mxy - mny))
        ox = x0 + (pw - (mxx - mnx) * z) / 2 - mnx * z
        oy = top + (avail - (mxy - mny) * z) / 2 - mny * z
        pos = {n: (int(p[0] * z + ox), int(p[1] * z + oy))
               for n, p in pts.items()}
        for a, b in SKEL_LINKS:
            if a in pos and b in pos:
                pygame.draw.line(self.screen, (150, 170, 200),
                                 pos[a], pos[b], 2)
        for n, (cx, cy) in pos.items():
            if n.endswith("R"):
                col = (90, 220, 255)
            elif n.endswith("L"):
                col = (255, 180, 90)
            else:
                col = (255, 90, 90)
            pygame.draw.circle(self.screen, col, (cx, cy), 5)
            t = self.small.render(JOINT_CN.get(n, n), True, col)
            self.screen.blit(t, (cx + 6, cy - 6))

    def info_lines(self):
        act = self.action_names[self.action_idx] if self.action_names else "-"
        name = self.rig.get("name", self.key)
        state = "暂停" if self.paused else "播放中"
        cyc = " [C:轮播开]" if self.cycle_mode else " [C:轮播关]"
        lines = [
            f"角色:{name}({self.key})  动作:{act}  {state}  帧 "
            f"{self.frame}/{max(0, len(self.frames) - 1)}   空格=播放/暂停  "
            f",/.=逐帧  [ ]=换动作  Tab=切角色{cyc}",
            "左键点选/拖动  右键拖=旋转  Q/E=旋转  Shift+Q/E=缩放  "
            "滚轮=缩放  方向键=微调(Shift 10px)  P=点取骨眼",
            "PgUp/PgDn=图层  1-0=选件  R=复位  B=全部复位  M=单件镜像  "
            "L=全部镜像  F=场景镜像  V=标记  Y=示意图",
            "O=自动竖直  X=烘焙  G=生成本角色骨长  S=保存绑定  +/-=视图缩放  D=大字校准  "
            "Esc=退出  (骨架: 统一 FK, 四角色共用)",
        ]
        if self.selected:
            p = self.selected
            pv = self.pivot(p)
            mir = " 镜" if self.mirror.get(p, False) else ""
            o = self.off(p)
            lines.append(
                f"选中: {p} {PART_CN.get(p, '')}{mir}  偏移({o[0]:.1f},{o[1]:.1f})  "
                f"rot={self.rot(p):.1f}  scale={self.scale(p):.2f}  "
                f"层={self.draw(p)}  枢轴=({pv[0]:.1f},{pv[1]:.1f})")
        else:
            lines.append("未选中部件")
        b = self.rig["bones"]
        zoom = self.view["zoom"] if self.view else 0.0
        lines.append(
            f"骨长 上臂 {b['upperR']['len']:.0f} / 前臂 {b['foreR']['len']:.0f} / "
            f"大腿 {b['thighR']['len']:.0f} / 小腿 {b['shinR']['len']:.0f}   "
            f"骨眼偏差 {self.bone_eye_deviation():.4f}px   "
            f"视图 {zoom:.2f}x")
        if self.msg:
            lines.append(self.msg)
        return lines

    def draw_info(self):
        yy = 6
        for ln in self.info_lines():
            if not ln:
                continue
            t = self.font.render(ln, True, (255, 255, 255))
            self.screen.blit(t, (8, yy))
            yy += 18

    def draw_hud(self):
        dev = self.bone_eye_deviation()
        n = len(self.imgs())
        z = self.view["zoom"] if self.view else 0.0
        text = (f"骨眼偏差 = max |关节计算位置 - 部件期望位置| = {dev:.4f}px"
                f"    部件 {n}/{len(PART_ORDER)}    视图 {z:.2f}x")
        t = self.small.render(text, True, (185, 190, 210))
        self.screen.blit(t, (10, self.sh - 132))

    def draw_part_list(self):
        h = 100
        y0 = self.sh - h - 8
        w = min(self.sw - 16, 920)
        pygame.draw.rect(self.screen, (255, 255, 255), (8, y0, w, h), 1)
        yy = y0 + 6
        for i, p in enumerate(PART_ORDER):
            col = ACCENT if p == self.selected else (200, 200, 200)
            k = str((i + 1) % 10)
            t = self.font.render(f"[{k}] {PART_CN.get(p, p)} ({p})", True, col)
            if i % 2 == 0:
                self.screen.blit(t, (16, yy))
            else:
                self.screen.blit(t, (max(16, w // 2 + 8), yy))
                yy += 18

    def render(self):
        self.screen.fill(BG)
        self.update_view()
        self.draw_character()
        self.draw_skeleton()
        self.draw_markers()
        if self.selected:
            self.draw_preview()
        elif self.show_map:
            self.draw_map()
        self.draw_info()
        self.draw_hud()
        self.draw_part_list()

    # ------------------------------------------------------------ 拾取 / 编辑
    def image_pick(self, wx, wy):
        order = sorted([p for p in self.rig["parts"]],
                       key=lambda p: self.draw(p))
        for part in reversed(order):
            img = self.imgs().get(part)
            if img is None:
                continue
            pos = self.part_pos(part)
            s = self.scale(part)
            r = rad(self.angle_of(part))
            cs, sn = math.cos(r), math.sin(r)
            dx, dy = wx - pos[0], wy - pos[1]
            lx = (dx * cs + dy * sn) / s
            ly = (-dx * sn + dy * cs) / s
            pv = self.pivot(part)
            ix = lx + pv[0]
            iy = ly + pv[1]
            w, h = img.get_size()
            if 0 <= ix < w and 0 <= iy < h:
                ax = w - 1 - int(ix) if self.mirror.get(part, False) else int(ix)
                if img.get_at((ax, int(iy))).a > 20:
                    return part
        return None

    def nearest_anchor(self, mx, my, limit=60):
        best, best_d = None, limit * limit
        for p in PART_ORDER:
            if p not in self.rig["parts"] or p not in self.imgs():
                continue
            pos = self.part_pos(p)
            sx, sy = self.to_screen(pos[0], pos[1])
            d = (sx - mx) ** 2 + (sy - my) ** 2
            if d < best_d:
                best, best_d = p, d
        return best

    def pick_part(self, wx, wy):
        return self.image_pick(wx, wy)

    def pivot_from_world(self, part, wx, wy):
        pos = self.part_pos(part)
        s = self.scale(part)
        r = rad(self.angle_of(part))
        cs, sn = math.cos(r), math.sin(r)
        dx, dy = wx - pos[0], wy - pos[1]
        lx = (dx * cs + dy * sn) / s
        ly = (-dx * sn + dy * cs) / s
        pv = self.pivot(part)
        if self.mirror.get(part, False):
            return pv[0] - lx, ly + pv[1]
        return lx + pv[0], ly + pv[1]

    def set_pivot_pixel(self, part, ix, iy):
        img = self.imgs().get(part)
        if img is None:
            return False
        w, h = img.get_size()
        ix = max(0.0, min(w - 1.0, ix))
        iy = max(0.0, min(h - 1.0, iy))
        self.st["pivot"][part] = [round(ix, 1), round(iy, 1)]
        self.msg = (f"{part} 枢轴已设为 ({ix:.1f},{iy:.1f})"
                    "（原图坐标，整块图随之移动）")
        return True

    def set_tip_pixel(self, part, ix, iy):
        img = self.imgs().get(part)
        if img is None:
            return False
        w, h = img.get_size()
        ix = max(0.0, min(w - 1.0, ix))
        iy = max(0.0, min(h - 1.0, iy))
        self.st["tip"][part] = [round(ix, 1), round(iy, 1)]
        pv = self.pivot(part)
        L = math.hypot(ix - pv[0], iy - pv[1]) * self.scale(part)
        self.msg = (f"{part} 骨长端已设为 ({ix:.1f},{iy:.1f})"
                    f"  骨长≈{L:.1f}")
        return True

    def tip_world(self, part):
        tp = (self.st.get("tip") or {}).get(part) if self.st else None
        if not tp:
            return None
        pos = self.part_pos(part)
        s = self.scale(part)
        r = rad(self.angle_of(part))
        cs, sn = math.cos(r), math.sin(r)
        pv = self.pivot(part)
        dx, dy = tp[0] - pv[0], tp[1] - pv[1]
        if self.mirror.get(part, False):
            dx = -dx
        return (pos[0] + (dx * cs - dy * sn) * s,
                pos[1] + (dx * sn + dy * cs) * s)

    def in_preview(self, mx, my):
        x0, y0, pw, ph = self.preview_geom()
        return x0 <= mx <= x0 + pw and y0 <= my <= y0 + ph

    # ------------------------------------------------------------ 操作
    def reset_part(self, p, quiet=False):
        defn = self.rig["parts"].get(p)
        if not defn:
            return
        self.st["pivot"][p] = [float(defn.get("pivot", [0, 0])[0]),
                               float(defn.get("pivot", [0, 0])[1])]
        self.st["scale"][p] = 1.0
        self.st["rot"][p] = 0.0
        self.st["off"][p] = [0.0, 0.0]
        self.st["draw"][p] = int(defn.get("draw", 0))
        self.mirror[p] = bool(defn.get("mirror", False))
        if not quiet:
            self.msg = f"已把 {p} 复位（偏移0/旋转0/缩放1/镜像默认/图层默认/枢轴默认）"

    def auto_vertical(self, p):
        img = self.imgs().get(p)
        if img is None:
            return False
        pv = self.pivot(p)
        px, py = int(round(pv[0])), int(round(pv[1]))
        w, h = img.get_size()
        cx = cy = cnt = 0.0
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                if img.get_at((x, y)).a > 40:
                    cx += x
                    cy += y
                    cnt += 1
        if cnt < 8:
            return False
        cx /= cnt
        cy /= cnt
        self.st["rot"][p] = round(-image_tilt_deg(px, py, cx, cy), 1)
        return True

    def bake_part(self, p):
        img = self.imgs().get(p)
        if img is None:
            return None
        pv = self.pivot(p)
        iw0, _ = img.get_size()
        bx = (iw0 - 1 - pv[0]) if self.mirror.get(p, False) else pv[0]
        px, py = int(round(bx)), int(round(pv[1]))
        rot = self.rot(p)
        sc = self.scale(p)
        mirror = self.mirror.get(p, False)
        if abs(rot) < 0.05 and not mirror and abs(sc - 1.0) < 0.005:
            return ""
        src = pygame.transform.flip(img, True, False) if mirror else img
        rotated, mvx, mvy = rotate_bound_pg(src, rot, px, py)
        if abs(sc - 1.0) > 0.005:
            nw = max(1, int(round(rotated.get_width() * sc)))
            nh = max(1, int(round(rotated.get_height() * sc)))
            rotated = pygame.transform.smoothscale(rotated, (nw, nh))
            mvx *= sc
            mvy *= sc
        rect = rotated.get_bounding_rect(min_alpha=8)
        if rect.width <= 0 or rect.height <= 0:
            return ""
        pad = 4
        x0 = max(0, rect.x - pad)
        y0 = max(0, rect.y - pad)
        x1 = min(rotated.get_width() - 1, rect.x + rect.width - 1 + pad)
        y1 = min(rotated.get_height() - 1, rect.y + rect.height - 1 + pad)
        out = rotated.subsurface((x0, y0, x1 - x0 + 1, y1 - y0 + 1)).copy()
        new_px = int(round(mvx - x0))
        new_py = int(round(mvy - y0))
        out_dir = BAKE_ROOT / self.key
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / (p + ".png")
        pygame.image.save(out, str(path))
        # 从烘焙文件重新载入，确保画面显示的就是烘焙后的图
        self.images[self.key][p] = pygame.image.load(str(path)).convert_alpha()
        if hasattr(self, "_basis_cache"):
            self._basis_cache = None
        tp = self.st["tip"].get(p)
        if tp:
            # 骨长端跟着一起旋转/缩放/裁剪，否则烘焙后标记就失效
            tx = (iw0 - 1 - tp[0]) if mirror else tp[0]
            r = rad(rot)
            cs, sn = math.cos(r), math.sin(r)
            dx, dy = tx - px, tp[1] - py
            rx = px + dx * cs - dy * sn
            ry = py + dx * sn + dy * cs
            nx = ((rx - px) + mvx) * sc - x0
            ny = ((ry - py) + mvy) * sc - y0
            self.st["tip"][p] = [round(nx, 1), round(ny, 1)]
        self.st["pivot"][p] = [float(new_px), float(new_py)]
        self.st["rot"][p] = 0.0
        self.st["scale"][p] = 1.0
        self.mirror[p] = False
        self.save_bind()
        return path

    def set_scale(self, part, value):
        self.st["scale"][part] = round(min(2.5, max(0.4, value)), 3)
        self.msg = f"{part} 缩放 = {self.st['scale'][part]:.2f}x（0.4~2.5）"

    def big_pose_on(self):
        self.big_pose = True
        self.paused = True
        self.pose = blank_pose()
        self.pose.update({"shR": 90, "shL": 90, "hipR": 45, "hipL": -35})
        self.msg = "大字校准姿势 开（四肢摊开，不影响动作文件）"

    def big_pose_off(self):
        self.big_pose = False
        self.apply_frame(self.frame)
        self.msg = "大字校准姿势 关（回到当前动作）"

    def on_key(self, e):
        key = e.key
        mods = getattr(e, "mod", pygame.key.get_mods())
        shift = bool(mods & pygame.KMOD_SHIFT)

        if key == pygame.K_ESCAPE:
            if self.pick_pivot or self.pick_tip:
                self.pick_pivot = False
                self.pick_tip = False
                self.msg = "已取消点取"
            else:
                self.running = False
        elif key == pygame.K_t:
            self.pick_tip = not self.pick_tip
            self.pick_pivot = False
            self.msg = ("点取骨长端(轴的另一头): 左键点素材，Esc 取消"
                        if self.pick_tip else "已退出点取骨长")
        elif key == pygame.K_p:
            self.pick_pivot = not self.pick_pivot
            self.pick_tip = False
            self.msg = ("点取骨眼: 左键点素材上的孔，Esc 取消"
                        if self.pick_pivot else "已退出点取骨眼")
        elif key == pygame.K_SPACE:
            if self.big_pose:
                self.big_pose_off()
            self.paused = not self.paused
            self.play_acc = 0.0
        elif key == pygame.K_g:
            self.derive_lens()
        elif key == pygame.K_s:
            self.save_bind()
        elif key == pygame.K_c:
            self.cycle_mode = not self.cycle_mode
            if self.cycle_mode:
                self.paused = False
            self.msg = "轮播全部动作: 开" if self.cycle_mode else "轮播: 关"
        elif key == pygame.K_f:
            self.scene_mirror = not self.scene_mirror
            self.msg = ("整体朝向镜像 开" if self.scene_mirror
                        else "整体朝向镜像 关")
        elif key == pygame.K_v:
            self.show_all = not self.show_all
            self.msg = "显示全部标记 开" if self.show_all else "仅显示选中部件标记"
        elif key == pygame.K_y:
            self.show_map = not self.show_map
            self.msg = "结构示意图 开" if self.show_map else "结构示意图 关"
        elif key == pygame.K_d:
            if self.big_pose:
                self.big_pose_off()
            else:
                self.big_pose_on()
        elif key == pygame.K_TAB:
            nxt = CHAR_KEYS[(self.char_idx + 1) % len(CHAR_KEYS)]
            self.switch_char(nxt)
        elif key == pygame.K_r and self.selected:
            self.reset_part(self.selected)
        elif key == pygame.K_b:
            for p in PART_ORDER:
                self.reset_part(p, quiet=True)
            self.msg = "全部部件已复位"
        elif key == pygame.K_o and self.selected:
            if self.auto_vertical(self.selected):
                self.msg = (f"{self.selected} 已按枢轴自动竖直 "
                            f"rot={self.rot(self.selected):.1f}（可 Q/E 微调）")
            else:
                self.msg = f"{self.selected} 图太简单，自动竖直失败，用 Q/E 手动转"
        elif key == pygame.K_x and self.selected:
            path = self.bake_part(self.selected)
            if path:
                self.msg = f"已烘焙 -> rig_lab/baked/{self.key}/{path.name}（原图未改）"
            elif path == "":
                self.msg = f"{self.selected} 旋转为0且未镜像，无需烘焙"
            else:
                self.msg = "烘焙失败"
        elif key == pygame.K_m and self.selected:
            self.mirror[self.selected] = not self.mirror.get(self.selected, False)
            self.msg = f"{self.selected} 镜像 {'开' if self.mirror[self.selected] else '关'}"
        elif key == pygame.K_l:
            target = not all(self.mirror.values())
            for p in self.mirror:
                self.mirror[p] = target
            self.msg = f"全部部件镜像整体{'开启' if target else '关闭'}"
        elif key in (pygame.K_q, pygame.K_e) and self.selected:
            sign = -1.0 if key == pygame.K_q else 1.0
            if shift:
                self.set_scale(self.selected,
                               self.scale(self.selected) + sign * 0.05)
            else:
                if self.scene_mirror:
                    sign = -sign
                self.st["rot"][self.selected] = round(
                    self.rot(self.selected) + sign, 2)
        elif key in (pygame.K_PAGEUP, pygame.K_PAGEDOWN) and self.selected:
            d = 1 if key == pygame.K_PAGEUP else -1
            self.st["draw"][self.selected] = self.draw(self.selected) + d
            self.msg = f"{self.selected} 图层={self.draw(self.selected)} (大=靠前)"
        elif key in (pygame.K_MINUS, pygame.K_EQUALS, pygame.K_PLUS):
            if not self.view:
                self.update_view()
            self.manual_zoom = True
            factor = 1.08 if key in (pygame.K_EQUALS, pygame.K_PLUS) else 1 / 1.08
            self.view_zoom = min(5.0, max(0.05, self.view_zoom * factor))
            if self.view:
                self.view["zoom"] = self.view_zoom
            self.msg = f"视图缩放 {self.view_zoom:.2f}x"
        elif key == pygame.K_COMMA:
            if self.big_pose:
                self.big_pose_off()
            if self.frames:
                self.apply_frame((self.frame - 1) % len(self.frames))
                self.paused = True
        elif key == pygame.K_PERIOD:
            if self.big_pose:
                self.big_pose_off()
            if self.frames:
                self.apply_frame((self.frame + 1) % len(self.frames))
                self.paused = True
        elif key in (pygame.K_LEFTBRACKET, pygame.K_RIGHTBRACKET):
            self.switch_action(-1 if key == pygame.K_LEFTBRACKET else 1)
        elif pygame.K_0 <= key <= pygame.K_9:
            i = 9 if key == pygame.K_0 else key - pygame.K_1
            if 0 <= i < len(PART_ORDER):
                self.selected = PART_ORDER[i]
                self.msg = f"选中 {self.selected} {PART_CN.get(self.selected, '')}"
        elif key in (pygame.K_LEFT, pygame.K_RIGHT,
                     pygame.K_UP, pygame.K_DOWN) and self.selected:
            d = 10.0 if shift else 1.0
            hflip = -1.0 if self.scene_mirror else 1.0
            o = list(self.off(self.selected))
            if key == pygame.K_LEFT:
                o[0] -= d * hflip
            elif key == pygame.K_RIGHT:
                o[0] += d * hflip
            elif key == pygame.K_UP:
                o[1] -= d
            else:
                o[1] += d
            self.st["off"][self.selected] = o

    # --------------------------------------------------- 拖动=改枢轴（不脱骨眼）
    def on_left_down(self, pos):
        mx, my = pos
        # 右上原图面板：点像素直接定枢轴
        if self.selected and self.in_preview(mx, my):
            data = self.preview_place()
            if data:
                _, _, _, _, z, dx, dy, iw, ih = data
                ix = int((mx - dx) / z)
                iy = int((my - dy) / z)
                if self.pick_tip:
                    if self.set_tip_pixel(self.selected, ix, iy):
                        self.pick_tip = False
                        return
                elif self.set_pivot_pixel(self.selected, ix, iy):
                    return
        wx, wy = self.to_world(mx, my)
        if self.pick_pivot:
            part = self.pick_part(wx, wy)
            if part:
                ix, iy = self.pivot_from_world(part, wx, wy)
                self.set_pivot_pixel(part, ix, iy)
                self.selected = part
            else:
                self.msg = "这里没有部件，请点在素材上"
            self.pick_pivot = False
            return
        if self.pick_tip:
            part = self.pick_part(wx, wy)
            if part:
                ix, iy = self.pivot_from_world(part, wx, wy)
                self.set_tip_pixel(part, ix, iy)
                self.selected = part
            else:
                self.msg = "这里没有部件，请点在素材上（T 仍处于点取状态）"
            if part:
                self.pick_tip = False
            return

        part = self.pick_part(wx, wy)
        if not part:
            part = self.nearest_anchor(mx, my, 60)
        if part:
            self.selected = part
            self.dragging = True
            self.last_mouse = pos
            self.last_world = (wx, wy)
            self.msg = f"选中 {part} {PART_CN.get(part, '')}"

    def on_motion(self, pos):
        mx, my = pos
        if self.dragging and self.selected:
            wx, wy = self.to_world(mx, my)
            if self.last_world:
                o = list(self.off(self.selected))
                o[0] += wx - self.last_world[0]
                o[1] += wy - self.last_world[1]
                self.st["off"][self.selected] = o
            self.last_world = (wx, wy)
            self.last_mouse = pos
        elif self.rotating and self.selected:
            if self.last_mouse:
                ang = (mx - self.last_mouse[0]) * 0.5
                if self.scene_mirror:
                    ang = -ang
                self.st["rot"][self.selected] = round(
                    self.rot(self.selected) + ang, 2)
            self.last_mouse = pos

    def on_wheel(self, direction):
        if not self.selected or direction == 0:
            return
        self.set_scale(self.selected,
                       self.scale(self.selected) + 0.05 * direction)

    def handle_events(self):
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                self.running = False
            elif e.type == pygame.VIDEORESIZE:
                self.sw, self.sh = e.w, e.h
                self.screen = pygame.display.set_mode(
                    (self.sw, self.sh), pygame.RESIZABLE)
                self.manual_zoom = False
                self.view = None
                self.msg = f"窗口已调为 {self.sw}x{self.sh}"
            elif e.type == pygame.KEYDOWN:
                self.on_key(e)
            elif e.type == pygame.MOUSEBUTTONDOWN:
                if e.button == 1:
                    self.on_left_down(e.pos)
                elif e.button == 3 and self.selected:
                    self.rotating = True
                    self.last_mouse = e.pos
                elif e.button in (4, 5):
                    self.on_wheel(1 if e.button == 4 else -1)
            elif e.type == pygame.MOUSEBUTTONUP:
                if e.button == 1:
                    self.dragging = False
                    self.last_mouse = None
                    self.last_world = None
                elif e.button == 3:
                    self.rotating = False
                    self.last_mouse = None
            elif e.type == pygame.MOUSEMOTION:
                if self.dragging or self.rotating:
                    self.on_motion(e.pos)
            elif e.type == pygame.MOUSEWHEEL:
                self.on_wheel(e.y)


# ---------------------------------------------------------------- 入口
def run_selftest():
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    os.environ["SDL_AUDIODRIVER"] = "dummy"
    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    tool = RigTool(screen)
    SELFTEST_ROOT.mkdir(parents=True, exist_ok=True)
    ok = True
    print("[selftest] 统一骨骼 FK · 4 角色 idle 首帧")
    for key in CHAR_KEYS:
        tool.switch_char(key)
        tool.load_action("idle" if "idle" in ACTIONS else tool.action_names[0])
        tool.paused = True
        tool.frame = 0
        tool.apply_frame(0)
        tool.selected = None
        tool.show_all = False
        tool.show_map = False
        tool.manual_zoom = False
        tool.view = None
        tool.render()
        path = SELFTEST_ROOT / (key + ".png")
        pygame.image.save(screen, str(path))
        dev = tool.bone_eye_deviation()
        count = len(tool.imgs())
        rel = path.relative_to(ROOT)
        print(f"[selftest] {key} {tool.rig.get('name', '')}  "
              f"部件 {count}/{len(PART_ORDER)}  骨眼偏差 {dev:.4f}px  -> {rel}")
        if count != len(PART_ORDER) or dev > 0.5 or not path.exists():
            ok = False
    pygame.quit()
    print("[selftest] PASS" if ok else "[selftest] FAIL")
    return 0 if ok else 1


def main():
    pygame.init()
    pygame.display.set_caption("皮影新骨骼绑定台 · 统一骨骼 FK")
    screen = pygame.display.set_mode((WIN_W, WIN_H), pygame.RESIZABLE)
    tool = RigTool(screen)
    clock = pygame.time.Clock()
    while tool.running:
        dt = clock.tick(60) / 1000.0
        tool.handle_events()
        if not tool.paused and tool.frames and not tool.big_pose:
            tool.play_acc += dt
            spf = 1.0 / max(1.0, tool.fps)
            n = int(tool.play_acc / spf)
            if n > 0:
                tool.play_acc -= n * spf
                tool.advance(n)
        tool.render()
        pygame.display.flip()
    pygame.quit()
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(run_selftest())
    sys.exit(main())
