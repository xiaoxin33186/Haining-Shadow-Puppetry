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

# 关节补片 / 边缘补边（防止动作时部件之间露出背景）
EDGE_DILATE = 2          # 每个部件沿不透明边缘向外扩的像素数（素材坐标）
JOINT_CAP_RADIUS = 7.0   # 小关节补片半径（素材坐标），实际按 SCALE_FACTOR 缩放
JOINT_CAP_RADIUS_BIG = 12.0   # 肩/髋这类大关节补片半径
JOINT_CAP_MIN_R = 4      # 补片最小屏幕半径
BIG_JOINTS = {"left_elbow", "right_elbow", "left_hip", "right_hip"}
_CAP_CACHE = {}
_COLOR_CACHE = {}

CHAR_SCALE_FILE = LAB / "char_scale.json"


def _load_char_scales():
    """读取每角色的整体缩放（用于统一人物大小）；文件缺失时全部按 1.0。"""
    try:
        data = json.loads(CHAR_SCALE_FILE.read_text(encoding="utf-8"))
        return {k: float(v) for k, v in data.items()}
    except Exception:
        return {}


CHAR_SCALES = _load_char_scales()


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
    rig["_name"] = character
    # 髋关节位置允许按角色覆盖（重链骨骼时把髋孔位置写进来，off 才能清零）
    st_hips = st.get("hips") or {}
    for side in ("left", "right"):
        if side in st_hips:
            rig["hips"][side] = [float(st_hips[side][0]), float(st_hips[side][1])]
    _apply_char_scale(rig, CHAR_SCALES.get(character, 1.0))
    return rig


def _apply_char_scale(rig, cs):
    """整体缩放角色：骨长、躯干锚点、眼位一起乘，素材在 render 里同步乘。"""
    cs = float(cs or 1.0)
    rig["_char_scale"] = cs
    if abs(cs - 1.0) < 1e-6:
        return
    rig["neck"] = [float(rig["neck"][0]) * cs, float(rig["neck"][1]) * cs]
    rig["shoulder"] = [float(rig["shoulder"][0]) * cs,
                       float(rig["shoulder"][1]) * cs]
    for side in ("left", "right"):
        hv = rig["hips"][side]
        rig["hips"][side] = [float(hv[0]) * cs, float(hv[1]) * cs]
    for b in rig["bones"].values():
        b["len"] = float(b["len"]) * cs
    head = rig["parts"].get("head")
    if head and head.get("eyeOffset"):
        head["eyeOffset"] = [float(head["eyeOffset"][0]) * cs,
                             float(head["eyeOffset"][1]) * cs]


# ---------------------------------------------------------------- 图片（烘焙优先）
_MATS_CACHE = {}


def reset_cache():
    """素材/绑定被重新烘焙后可调用本函数强制重载。"""
    _MATS_CACHE.clear()
    _CAP_CACHE.clear()
    _COLOR_CACHE.clear()


def _avg_color(img):
    """取素材不透明区域的平均色，用作补边/补片颜色。"""
    import pygame

    try:
        c = pygame.transform.average_color(img, consider_alpha=True)
    except TypeError:
        c = pygame.transform.average_color(img)
    if not any(c[:3]):
        c = (60, 40, 30)
    return (int(c[0]), int(c[1]), int(c[2]))


def _with_edge(img):
    """把素材沿边缘向外扩 EDGE_DILATE 像素，用邻像素真实颜色填充。

    旋转/错位时露出的是同色补边而不是背景，消除 1-3px 细缝；
    与平均色方案不同，花纹/袖口等渐变色部位也能自然过渡。
    """
    import pygame

    if EDGE_DILATE <= 0:
        return img
    try:
        w, h = img.get_size()
        base = pygame.Surface((w, h), pygame.SRCALPHA)
        base.blit(img, (0, 0))
        step = EDGE_DILATE
        for dy in (-step, 0, step):
            for dx in (-step, 0, step):
                if dx == 0 and dy == 0:
                    continue
                base.blit(img, (dx, dy))
        base.blit(img, (0, 0))
        return base
    except Exception:
        return img


def _pivot_color(img, px, py, r=6):
    """补片颜色：取素材枢轴附近不透明像素的平均色，与关节处同色。

    用整块素材的平均色会把花纹/白色/描边混进来（例如躯干平均色偏灰），
    在红裤等部位补出异色圆斑；改为只在枢轴周围取色即可贴合。
    """
    key = (id(img), int(px), int(py))
    cached = _COLOR_CACHE.get(key)
    if cached is not None:
        return cached
    w, h = img.get_size()
    acc = [0, 0, 0, 0]
    try:
        for x in range(max(0, int(px) - r), min(w, int(px) + r + 1), 2):
            for y in range(max(0, int(py) - r), min(h, int(py) + r + 1), 2):
                c = img.get_at((x, y))
                if c.a < 200:
                    continue
                acc[0] += c.r
                acc[1] += c.g
                acc[2] += c.b
                acc[3] += 1
    except Exception:
        acc[3] = 0
    if acc[3]:
        color = (acc[0] // acc[3], acc[1] // acc[3], acc[2] // acc[3])
    else:
        color = _avg_color(img)
    _COLOR_CACHE[key] = color
    return color


def _cap_surface(color, radius):
    """生成带抗锯齿的圆形关节补片（按颜色+半径缓存）。"""
    import pygame

    key = (color, int(radius))
    surf = _CAP_CACHE.get(key)
    if surf is not None:
        return surf
    r = max(1, int(radius))
    ss = 4
    big = pygame.Surface((r * 2 * ss, r * 2 * ss), pygame.SRCALPHA)
    pygame.draw.circle(big, color + (255,), (r * ss, r * ss), r * ss)
    surf = pygame.transform.smoothscale(big, (r * 2, r * 2))
    _CAP_CACHE[key] = surf
    return surf


def _mats_for(character, fallback):
    """有烘焙图就用烘焙图（枢轴/骨长端已按烘焙图保存），否则用原图。"""
    import pygame

    if not character:
        return {p: _with_edge(img) for p, img in fallback.items()}, {}
    cached = _MATS_CACHE.get(character)
    if cached is not None:
        return cached
    baked_dir = LAB / "baked" / character
    if not baked_dir.is_dir():
        out = {p: _with_edge(img) for p, img in fallback.items()}
        _MATS_CACHE[character] = (out, {})
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
    mats = {p: _with_edge(img) for p, img in mats.items()}
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


# [KICK] 踢腿按角色骨长重映射：抬腿峰值角按腿长比例缩放，腿短的角色改成前踢
KICK_MIN_SCALE = 0.45   # 下限：再短的腿也保留 45% 幅度，避免踢腿塌成小动作
KICK_LEG_BONES = ("thighR", "shinR")   # 踢腿用的是右腿
# 个别角色的踢腿幅度直接指定（覆盖按腿长算出的系数，可调高或调低）
KICK_SCALE_OVERRIDE = {"new_sha_wujing_material": 0.75,
                       "new_zhu_bajie_material": 0.9}
# 踢腿屈膝：小腿的朝向增量再乘该系数，抬腿时膝盖留一点弯曲（不写则保持原样）
KICK_KNEE_LAG = {}
KICK_LAYER_SKIP = {"tang_sanzang_material"}  # 长袍整体遮腿，保持原样（2026-11-23 确认）
KICK_LAYER_MIN_LIFT = 12.0  # 大腿抬起超过该角度才把腿部提到身前，站姿/收腿不加层
_STD_LEG_LEN = None


def _std_leg_len():
    """标准角色（悟空）的右腿长，含 rig_bind.json 的绑定覆盖值；只算一次。"""
    global _STD_LEG_LEN
    if _STD_LEG_LEN is None:
        try:
            std = rig_for(STD_KEY)
            _STD_LEG_LEN = (float(std["bones"]["thighR"]["len"])
                            + float(std["bones"]["shinR"]["len"]))
        except (KeyError, TypeError, AttributeError):
            _STD_LEG_LEN = 0.0
    return _STD_LEG_LEN


def _kick_leg_scale(rig):
    """踢腿幅度系数 = 角色右腿长 / 标准腿长（悟空），限制在 [KICK_MIN_SCALE, 1]。"""
    try:
        std_len = _std_leg_len()
        leg = float(rig["bones"]["thighR"]["len"]) + float(rig["bones"]["shinR"]["len"])
    except (KeyError, TypeError):
        return 1.0
    if std_len <= 0 or leg <= 0:
        return 1.0
    s = max(KICK_MIN_SCALE, min(1.0, leg / std_len))
    return KICK_SCALE_OVERRIDE.get(rig.get("_name"), s)


def build(rig, joints, ref=None, leg_scale=None):
    """由动作朝向增量 + 绑定骨长推关节：返回 (j, angles)。

    ref 为 idle 首帧关节（基准），保证静止时与 rig_lab 里的绑定姿态一致。
    leg_scale 不为 None 时，只把右腿（踢腿侧）的朝向增量乘以该系数。
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
        if leg_scale is not None and bn in KICK_LEG_BONES:
            d *= leg_scale
            if bn == "shinR":
                d *= KICK_KNEE_LAG.get(rig.get("_name"), 1.0)
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
           draw_parts=None, draw_order=None, action=None):
    """v2 渲染入口。返回 True 表示已接管；False 表示交回旧渲染。

    action: 当前动作名（如 "kick"）；只用于踢腿的按骨长重映射，其他动作不受影响。
    """
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
    # [KICK] 只有踢腿动作做按骨长重映射
    is_kick = bool(action) and Path(str(action)).stem.lower() == "kick"
    leg_scale = _kick_leg_scale(rig) if is_kick else None
    j, angles = build(rig, joints, ref, leg_scale=leg_scale)

    pelvis = joints.get("pelvis") or {"x": surface.get_width() // 2.0,
                                      "y": surface.get_height() // 2.0}
    mirror_center_x = float(pelvis["x"])
    order = draw_order or cfg.get("draw_order") or {}
    parts = draw_parts or [p for p in rig["parts"]]
    # [KICK] 踢腿时把踢腿侧腿部提到身体前面，避免被衣摆遮住（仅抬腿阶段生效）
    if is_kick and character not in KICK_LAYER_SKIP:
        lift = abs(_delta(_dir(joints, "right_hip", "right_knee"),
                          _dir(ref, "right_hip", "right_knee")))
        if lift >= KICK_LAYER_MIN_LIFT:
            top = max([order.get(p, rig["parts"][p]["draw"]) for p in parts] + [0])
            order = dict(order)
            order["right_hip"] = top + 1   # 大腿
            order["right_knee"] = top + 2  # 小腿+脚，压在大腿之上
    center_x = surface.get_width() // 2
    center_y = surface.get_height() // 2
    cs = float(rig.get("_char_scale", 1.0))

    for part in sorted(parts, key=lambda p: order.get(p, rig["parts"][p]["draw"])):
        info = rig["parts"].get(part)
        if not info:
            continue
        img = mats.get(part)
        if img is None:
            continue

        px, py = list(info["pivot"])[:2]
        cap_src, cap_px, cap_py = img, px, py
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
        wx += off[0] * cs
        wy += off[1] * cs

        rotated, mvx, mvy = _rotate_bound(img, ang, px, py)
        sc = SCALE_FACTOR * float((rig["_scale"] or {}).get(part, 1.0)) * cs
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

        # 关节补片：用关节处的本部件颜色垫一个圆，盖住旋转产生的缝隙
        if info.get("bone") and JOINT_CAP_RADIUS > 0:
            color = _pivot_color(cap_src, cap_px, cap_py)
            base_r = (JOINT_CAP_RADIUS_BIG if part in BIG_JOINTS
                      else JOINT_CAP_RADIUS)
            cap_r = max(JOINT_CAP_MIN_R, int(round(base_r * sc)))
            cap = _cap_surface(color, cap_r)
            surface.blit(cap, (fx - cap_r, fy - cap_r))

        if scene_mirror:
            rotated = pygame.transform.flip(rotated, True, False)
            surface.blit(rotated, (fx - (nw - mvx), fy - mvy))
        else:
            surface.blit(rotated, (fx - mvx, fy - mvy))
    return True
