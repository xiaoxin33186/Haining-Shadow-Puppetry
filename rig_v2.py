"""新骨骼渲染（v2）——独立模块，可由 pygame_Alpha 开关调用。

设计原则：
- 只读取数据（rig_lab/rest.js、rig_lab/rig_bind.json）和动作关节，不改任何素材/配置；
- 以 idle 首帧的关节朝向为基准，只取动作的“朝向增量”，因此 idle 静止时与
  rig_lab 里绑定的姿态完全一致；
- 关节位置由骨长链式推导，部件枢轴钉在关节上，保证动作中不分离；
- 与旧渲染并存：调用方开关决定走哪条路径，删掉本文件即可完全回退。
"""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LAB = ROOT / "rig_lab"
SCALE_FACTOR = 0.79


def _read_js_value(path, marker):
    text = path.read_text(encoding="utf-8")
    pos = text.index(marker)
    eq = text.index("=", pos) + 1
    starts = [i for i in (text.find("{", eq), text.find("[", eq)) if i >= 0]
    if not starts:
        raise ValueError(f"{path.name}: 找不到 {marker} 的值")
    value, _ = json.JSONDecoder().raw_decode(text[min(starts):])
    return value


def _load():
    rests = _read_js_value(LAB / "rest.js", "window.REST_RIGS")
    binds = {}
    p = LAB / "rig_bind.json"
    if p.exists():
        binds = json.loads(p.read_text(encoding="utf-8"))
    return rests, binds


REST_RIGS, BINDS = _load()

# 标准骨架（与 rig_tool.CHAR_KEYS[0] 一致）：所有角色共用其躯干锚点与骨长/骨向基准
STD_KEY = "xinwukong_material"

BONE_ORDER = ["upperR", "foreR", "upperL", "foreL",
              "thighR", "shinR", "thighL", "shinL"]
CHILD = {
    "upperR": "elbowR", "foreR": "wristR",
    "upperL": "elbowL", "foreL": "wristL",
    "thighR": "kneeR", "shinR": "ankleR",
    "thighL": "kneeL", "shinL": "ankleL",
}
# 每根骨对应动作 JSON 里的关节对（用于取该骨的世界朝向）
BONE_JOINTS = {
    "upperR": ("right_shoulder", "right_elbow"),
    "foreR": ("right_elbow", "right_wrist"),
    "upperL": ("left_shoulder", "left_elbow"),
    "foreL": ("left_elbow", "left_wrist"),
    "thighR": ("right_hip", "right_knee"),
    "shinR": ("right_knee", "right_ankle"),
    "thighL": ("left_hip", "left_knee"),
    "shinL": ("left_knee", "left_ankle"),
}


def rad(d):
    return d * math.pi / 180.0


def rot_phi(vec, deg):
    if not deg:
        return [vec[0], vec[1]]
    r = rad(deg)
    c, s = math.cos(r), math.sin(r)
    return [vec[0] * c + vec[1] * s, -vec[0] * s + vec[1] * c]


def norm_deg(a):
    return (a + 180.0) % 360.0 - 180.0


def rig_for(character):
    """取某角色的骨架：rest + 工具保存的长度/朝向/枢轴/缩放/旋转。

    躯干锚点与骨长/骨向基准统一取标准骨架（悟空），与 rig_lab/rig_tool.py
    的 make_rig() 完全一致；否则非悟空角色会因各自 rest 的 neck/hips/
    phiTorso 不同而整体歪掉。
    """
    rest = REST_RIGS.get(character)
    if not rest:
        return None
    rig = json.loads(json.dumps(rest))  # 深拷贝，避免污染缓存
    std = REST_RIGS.get(STD_KEY)
    if std and character != STD_KEY:
        rig["phiTorso"] = float(std["phiTorso"])
        rig["neck"] = list(std["neck"])
        rig["shoulder"] = list(std["shoulder"])
        rig["hips"] = {"left": list(std["hips"]["left"]),
                       "right": list(std["hips"]["right"])}
        rig["parts"]["head"]["eyeOffset"] = list(
            std["parts"]["head"].get("eyeOffset", [0.0, 0.0]))
        for bn, b in rig["bones"].items():
            sb = std["bones"].get(bn)
            if sb:
                b["len"] = float(sb["len"])
                b["phi"] = float(sb["phi"])
    st = (BINDS or {}).get(character) or {}
    st_lens = st.get("lens") or {}
    st_phis = st.get("phis") or {}
    st_aoff = st.get("aoff") or {}
    for bn, b in rig["bones"].items():
        b["len"] = float(st_lens.get(bn, b["len"]))
        b["phi"] = float(st_phis.get(bn, b["phi"]))
        part = b.get("part")
        if part in st_aoff:
            b["axisOffset"] = float(st_aoff[part])
        elif bn in st_aoff:
            b["axisOffset"] = float(st_aoff[bn])
    st_mirror = st.get("mirror") or {}
    for part, d in rig["parts"].items():
        if part in (st.get("pivot") or {}):
            d["pivot"] = list(st["pivot"][part])
        if part in st_mirror:
            d["mirror"] = bool(st_mirror[part])
    rig["_scale"] = st.get("scale") or {}
    rig["_rot"] = st.get("rot") or {}
    rig["_off"] = st.get("off") or {}
    return rig


# ---------------------------------------------------------------- 图片（烘焙优先）
_MATS_CACHE = {}


def reset_cache():
    """素材/绑定被重新烘焙后可调用本函数强制重载。"""
    _MATS_CACHE.clear()


def _mats_for(character, fallback):
    """有烘焙图就用烘焙图（枢轴/骨长端已按烘焙图保存），否则用原图。"""
    import pygame

    if not character:
        return fallback, {}
    cached = _MATS_CACHE.get(character)
    if cached is not None:
        return cached
    baked_dir = LAB / "baked" / character
    if not baked_dir.is_dir():
        _MATS_CACHE[character] = (fallback, {})
        return _MATS_CACHE[character]
    mats = dict(fallback)
    baked_flags = {}
    for part in list(fallback):
        path = baked_dir / (part + ".png")
        if not path.exists():
            continue
        try:
            mats[part] = pygame.image.load(str(path)).convert_alpha()
            baked_flags[part] = True
        except Exception:
            pass
    _MATS_CACHE[character] = (mats, baked_flags)
    return _MATS_CACHE[character]


# ---------------------------------------------------------------- FK
def _dir(joints, j1, j2):
    """关节对的世界朝向 φ（0=向下，+90=向右）；缺关节返回 None。"""
    a, b = joints.get(j1), joints.get(j2)
    if not a or not b:
        return None
    return math.degrees(math.atan2(float(b["x"]) - float(a["x"]),
                                   float(b["y"]) - float(a["y"])))


def _delta(cur, ref):
    if cur is None or ref is None:
        return 0.0
    return norm_deg(cur - ref)


def build(rig, joints, ref=None):
    """由动作朝向增量 + 绑定骨长推关节：返回 (j, angles)。

    ref 为 idle 首帧关节（基准），保证静止时与 rig_lab 里的绑定姿态一致。
    """
    ref = ref or joints
    pelvis = joints.get("pelvis") or {"x": 0.0, "y": 0.0}
    root = [float(pelvis["x"]), float(pelvis["y"])]
    phi_torso_rest = float(rig.get("phiTorso", 0.0))
    d_torso = _delta(_dir(joints, "upper_neck", "pelvis"),
                     _dir(ref, "upper_neck", "pelvis"))

    nv = rot_phi(rig["neck"], d_torso)
    sv = rot_phi(rig["shoulder"], d_torso)
    j = {"pelvis": root,
         "neck": [root[0] + nv[0], root[1] + nv[1]],
         "shoulder": [root[0] + sv[0], root[1] + sv[1]]}
    for side in ("left", "right"):
        hv = rot_phi(rig["hips"][side], d_torso)
        j["hip" + side[0].upper()] = [root[0] + hv[0], root[1] + hv[1]]

    phi = {}
    for bn in BONE_ORDER:
        j1, j2 = BONE_JOINTS[bn]
        d = _delta(_dir(joints, j1, j2), _dir(ref, j1, j2))
        phi[bn] = float(rig["bones"][bn]["phi"]) + d

    for bn in BONE_ORDER:
        b = rig["bones"][bn]
        ln = float(b["len"])
        j[CHILD[bn]] = [j[b["base"]][0] + math.sin(rad(phi[bn])) * ln,
                        j[b["base"]][1] + math.cos(rad(phi[bn])) * ln]

    eye = rig["parts"]["head"].get("eyeOffset", [0.0, 0.0])
    ev = rot_phi(eye, d_torso)
    j["headEye"] = [j["neck"][0] + ev[0], j["neck"][1] + ev[1]]

    angles = {}
    for bn in BONE_ORDER:
        angles[bn] = -phi[bn] + float(rig["bones"][bn].get("axisOffset", 0.0))
    angles["body"] = (-(phi_torso_rest + d_torso)
                      + float(rig["parts"]["body"].get("axisOffset", 0.0)))
    d_head = _delta(_dir(joints, "head_top", "upper_neck"),
                    _dir(ref, "head_top", "upper_neck"))
    angles["head"] = (-(phi_torso_rest + d_head)
                      + float(rig["parts"]["head"].get("axisOffset", 0.0)))
    return j, angles


def _rotate_bound(img, angle_cw, px, py):
    """与 pygame_Alpha.rotate_bound_pg 完全一致：绕 pivot 旋转。"""
    import pygame

    rotated = pygame.transform.rotate(img, -angle_cw)
    w, h = img.get_size()
    nw, nh = rotated.get_size()
    dx, dy = px - w / 2.0, py - h / 2.0
    r = rad(angle_cw)
    mvx = nw / 2.0 + (dx * math.cos(r) - dy * math.sin(r))
    mvy = nh / 2.0 + (dx * math.sin(r) + dy * math.cos(r))
    return rotated, mvx, mvy


# ---------------------------------------------------------------- 渲染入口
def render(surface, bg, mats, joints, adjusted_initial_joints, frame_idx,
           cfg=None, draw_bg=True, mirror=None, character=None,
           draw_parts=None, draw_order=None):
    """v2 渲染入口。返回 True 表示已接管；False 表示交回旧渲染。"""
    import pygame

    cfg = cfg or {}
    character = character or cfg.get("character")
    rig = rig_for(character) if character else None
    if rig is None:
        return False

    mats, _ = _mats_for(character, mats)

    if draw_bg:
        surface.blit(bg, (0, 0))

    scene_mirror = (bool(cfg.get("scene_mirror", False))
                    if mirror is None else bool(mirror))
    ref = adjusted_initial_joints or joints
    j, angles = build(rig, joints, ref)

    pelvis = joints.get("pelvis") or {"x": surface.get_width() // 2.0,
                                      "y": surface.get_height() // 2.0}
    mirror_center_x = float(pelvis["x"])
    order = draw_order or cfg.get("draw_order") or {}
    parts = draw_parts or [p for p in rig["parts"]]
    center_x = surface.get_width() // 2
    center_y = surface.get_height() // 2

    for part in sorted(parts, key=lambda p: order.get(p, rig["parts"][p]["draw"])):
        info = rig["parts"].get(part)
        if not info:
            continue
        img = mats.get(part)
        if img is None:
            continue

        px, py = list(info["pivot"])[:2]
        use_mirror = bool(info.get("mirror"))
        if use_mirror:
            img = pygame.transform.flip(img, True, False)
            px = img.get_width() - 1 - px

        if part in ("body", "head"):
            base_ang = angles[part]
            anchor = "neck" if part == "body" else "headEye"
            wx, wy = j[anchor]
        else:
            bone = info["bone"]
            base_ang = angles[bone]
            wx, wy = j[rig["bones"][bone]["base"]]
        ang = base_ang + float((rig["_rot"] or {}).get(part, 0.0))

        off = list((rig["_off"] or {}).get(part) or [0.0, 0.0])
        wx += off[0]
        wy += off[1]

        rotated, mvx, mvy = _rotate_bound(img, ang, px, py)
        sc = SCALE_FACTOR * float((rig["_scale"] or {}).get(part, 1.0))
        nw = max(1, int(round(rotated.get_width() * sc)))
        nh = max(1, int(round(rotated.get_height() * sc)))
        if (nw, nh) != rotated.get_size():
            rotated = pygame.transform.smoothscale(rotated, (nw, nh))
        mvx *= sc
        mvy *= sc

        if scene_mirror:
            wx = 2 * mirror_center_x - wx
        fx = (wx - center_x) * SCALE_FACTOR + center_x
        fy = (wy - center_y + 65) * SCALE_FACTOR + center_y

        if scene_mirror:
            rotated = pygame.transform.flip(rotated, True, False)
            surface.blit(rotated, (fx - (nw - mvx), fy - mvy))
        else:
            surface.blit(rotated, (fx - mvx, fy - mvy))
    return True
