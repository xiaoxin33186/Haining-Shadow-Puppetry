import os, json, math
from pathlib import Path
import pygame
import time
import statistics as st

# Debug mode switch
DEBUG_MODE = False

SCALE_FACTOR = 0.79

# 新骨骼渲染开关（rig_v2）。默认开启；设环境变量 RIG_V2=0 或删除 rig_v2.py 即回退旧渲染。
RIG_V2 = os.environ.get("RIG_V2", "1") == "1"
_rig_v2_mod = None
_rig_v2_failed = False

BACKGROUND_IMG = "workbackground/stage.jpg"
MATERIAL_ROOT = Path("xinwukong_material")  # 占位；实际角色目录由 current_character.txt 决定
VIDEO_NAME = "demo"
OUTPUT_DIR = f"output_frames_png_{VIDEO_NAME}"
SHOW_PIVOTS = False
SHOW_JOINTS = False
paused = False
JsonList = [
    "actions/idle.json"
            ]
PART_NAMES = [
    "body", "head", "right_hip", "right_knee", "left_hip", "left_knee",
    "right_elbow", "right_wrist", "left_elbow", "left_wrist"
]

# Drawing sequence (the larger the value, the more forward）
DRAW_ORDER = {
    "body": 3, "head": 2,
    "right_hip": 1, "right_knee": 3,
    "left_hip": 1, "left_knee": 3,
    "right_elbow": 4, "right_wrist": 4,
    "left_elbow": 1, "left_wrist": 2,
}
DEFAULT_DRAW_ORDER = dict(DRAW_ORDER)

PART_PARAM = {
    "body": [0, 0, 0, 0], "head": [0, 0, 0, 0],
    "right_hip": [0, 0, 0, 0], "right_knee": [0, 0, 0, 0],
    "left_hip": [0, 0, 0, 0], "left_knee": [0, 0, 0, 0],
    "right_elbow": [0, 0, 0, 0], "right_wrist": [0, 0, 0, 0],
    "left_elbow": [0, 0, 0, 0], "left_wrist": [0, 0, 0, 0]
}

ROT_OFF = {p: 0.0 for p in PART_PARAM}
PART_MIRROR = {p: False for p in PART_PARAM}
PIVOT = {}
SCENE_MIRROR = False

PROJECT = Path(__file__).resolve().parent
CHAR_DISPLAY = {
    "xinwukong_material": "悟空",
    # "wukong_shadow_play_material": "旧悟空",   # [停用] 不再显示
    # "shadow_play_material": "海宁通用",         # [停用] 不再显示
    # "bull_demon_king_material": "牛魔王",        # [停用] 不再显示
    # "sha_wujing_material": "沙僧",               # [停用] 已由新沙僧取代
    "new_sha_wujing_material": "新沙僧",
    # "zhu_bajie_material": "猪八戒",               # [停用] 已由新猪八戒取代
    "tang_sanzang_material": "唐三藏",
    "new_zhu_bajie_material": "新猪八戒",
}

# [停用角色] 素材目录保留，但不进入可选角色池（按钮循环里不会再切到）
DISABLED_CHARACTERS = {
    "wukong_shadow_play_material",  # 旧悟空
    "shadow_play_material",         # 海宁通用
    "sha_wujing_material",          # 旧沙僧（已由新沙僧取代）
    "zhu_bajie_material",           # 旧猪八戒（已由新猪八戒取代）
    "bull_demon_king_material",     # 牛魔王
}

def _candidate_dirs():
    known = ["xinwukong_material",
             # "wukong_shadow_play_material",  # [停用] 旧悟空
             # "shadow_play_material",         # [停用] 海宁通用
             # "bull_demon_king_material",     # [停用] 牛魔王
             "new_sha_wujing_material",
             # "zhu_bajie_material",           # [停用] 旧猪八戒
             "tang_sanzang_material", "new_zhu_bajie_material"]
    found = []
    try:
        for d in sorted(os.listdir(PROJECT)):
            fp = PROJECT / d
            if fp.is_dir() and all((fp / (p + ".png")).exists() for p in PART_NAMES):
                found.append(d)
    except Exception:
        pass
    return [d for d in known if d in found] + [d for d in found
                                               if d not in known and d not in DISABLED_CHARACTERS]

CHARACTERS = _candidate_dirs()

def _read_current():
    try:
        n = (PROJECT / "current_character.txt").read_text(encoding="utf-8").strip()
        if n in CHARACTERS:
            return n
    except Exception:
        pass
    return CHARACTERS[0] if CHARACTERS else "xinwukong_material"

CURRENT_CHARACTER = _read_current()
MATERIAL_ROOT = PROJECT / CURRENT_CHARACTER
CONFIG_PATH = MATERIAL_ROOT / "adjust.json"

CHAR_JOINT_FIX = {"wukong_shadow_play_material": True}

def uses_joint_fix():
    return bool(CHAR_JOINT_FIX.get(CURRENT_CHARACTER, False))

def _reset_config_defaults():
    for part in PART_PARAM:
        PART_PARAM[part][2] = 0
        PART_PARAM[part][3] = 0
        PART_PARAM[part][0] = 0
        PART_PARAM[part][1] = 0
    for p in ROT_OFF:
        ROT_OFF[p] = 0.0
    for p in PART_MIRROR:
        PART_MIRROR[p] = False
    PIVOT.clear()
    DRAW_ORDER.update(DEFAULT_DRAW_ORDER)
    global SCENE_MIRROR
    SCENE_MIRROR = False

def _apply_config(cfg):
    if isinstance(cfg, dict) and "parts" in cfg:
        for part in PART_PARAM:
            if part in cfg["parts"]:
                for i in range(2, 4):
                    PART_PARAM[part][i] = cfg["parts"][part][i]
        for part in PART_PARAM:
            if part in cfg.get("rot", {}):
                ROT_OFF[part] = float(cfg["rot"][part])
        for part in PART_PARAM:
            if part in cfg.get("mirror", {}):
                PART_MIRROR[part] = bool(cfg["mirror"][part])
        for part in PART_PARAM:
            if part in cfg.get("draw", {}):
                DRAW_ORDER[part] = int(cfg["draw"][part])
        for part in PART_PARAM:
            if part in cfg.get("pivot", {}) and cfg["pivot"][part]:
                PIVOT[part] = [int(cfg["pivot"][part][0]), int(cfg["pivot"][part][1])]
    else:
        for part in PART_PARAM:
            if part in cfg:
                for i in range(2, 4):
                    PART_PARAM[part][i] = cfg[part][i]
    global SCENE_MIRROR
    SCENE_MIRROR = bool(cfg.get("scene_mirror", False))

def _load_config_file(path, migrate=False):
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        _apply_config(cfg)
        return True
    except FileNotFoundError:
        pass
    except Exception:
        pass
    if migrate:
        legacy = PROJECT / "pivot_config.json"
        try:
            with open(legacy, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            _apply_config(cfg)
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2, ensure_ascii=False)
            except Exception:
                pass
            return True
        except Exception:
            pass
    return False

def set_character(name):
    global CURRENT_CHARACTER, MATERIAL_ROOT, CONFIG_PATH
    if name not in CHARACTERS:
        return False
    CURRENT_CHARACTER = name
    MATERIAL_ROOT = PROJECT / name
    CONFIG_PATH = MATERIAL_ROOT / "adjust.json"
    try:
        (PROJECT / "current_character.txt").write_text(name, encoding="utf-8")
    except Exception:
        pass
    _reset_config_defaults()
    _load_config_file(CONFIG_PATH)
    return True

_reset_config_defaults()
if not _load_config_file(CONFIG_PATH, migrate=(CURRENT_CHARACTER == "xinwukong_material")):
    pass

def finalize_pivots(mats):
    for part, img in mats.items():
        if part not in PART_PARAM:
            continue
        if part in PIVOT:
            PART_PARAM[part][0] = PIVOT[part][0]
            PART_PARAM[part][1] = PIVOT[part][1]
        else:
            PART_PARAM[part][0] = img.get_width() // 2
            PART_PARAM[part][1] = 0


# ===== 双人偶渲染支持：按角色保存/恢复渲染配置 =====

def capture_config():
    """返回当前角色的渲染配置快照（枢轴/旋转/镜像/绘制顺序/镜像场景）。

    供双人偶同时渲染时，每个 render() 调用前按各自角色的快照恢复配置。
    """
    return {
        "character": CURRENT_CHARACTER,
        "part_param": {p: list(PART_PARAM[p]) for p in PART_PARAM},
        "rot_off": dict(ROT_OFF),
        "part_mirror": dict(PART_MIRROR),
        "draw_order": dict(DRAW_ORDER),
        "scene_mirror": SCENE_MIRROR,
    }


def apply_config(cfg):
    """把 capture_config() 得到的快照恢复到全局渲染配置。"""
    if not cfg:
        return
    if "part_param" in cfg:
        for p in PART_PARAM:
            if p in cfg["part_param"]:
                PART_PARAM[p] = list(cfg["part_param"][p])
    if "rot_off" in cfg:
        for p in ROT_OFF:
            ROT_OFF[p] = cfg["rot_off"].get(p, 0.0)
    if "part_mirror" in cfg:
        for p in PART_MIRROR:
            PART_MIRROR[p] = bool(cfg["part_mirror"].get(p, False))
    if "draw_order" in cfg:
        for p in DRAW_ORDER:
            DRAW_ORDER[p] = cfg["draw_order"].get(p, 0)
    global SCENE_MIRROR
    SCENE_MIRROR = bool(cfg.get("scene_mirror", False))


def character_setup(name):
    """为指定角色安装配置并返回 (素材, 渲染配置快照)；不改变 current_character.txt。

    双人偶需要同时用两套角色配置渲染；本函数临时切换配置读取目标角色的
    素材与枢轴参数，随后恢复原配置，避免污染正在使用本模块的主流程。
    """
    if name not in CHARACTERS:
        print(f"[双人偶] 警告: 角色 '{name}' 不在可用素材列表 CHARACTERS 中。")
        return None
    saved_char, saved_material, saved_cfg_path = CURRENT_CHARACTER, MATERIAL_ROOT, CONFIG_PATH
    saved = capture_config()
    globals()["CURRENT_CHARACTER"] = name
    globals()["MATERIAL_ROOT"] = PROJECT / name
    globals()["CONFIG_PATH"] = (PROJECT / name) / "adjust.json"
    _reset_config_defaults()
    _load_config_file(CONFIG_PATH)
    try:
        mats = load_materials()
        finalize_pivots(mats)
        cfg = capture_config()
    finally:
        globals()["CURRENT_CHARACTER"] = saved_char
        globals()["MATERIAL_ROOT"] = saved_material
        globals()["CONFIG_PATH"] = saved_cfg_path
        apply_config(saved)
    return mats, cfg

PART_CONNECT = {
    "body": ("upper_neck", "pelvis"),
    "head": ("head_top", "upper_neck"),
    "right_hip": ("right_hip", "right_knee"),
    "right_knee": ("right_knee", "right_ankle"),
    "left_hip": ("left_hip", "left_knee"),
    "left_knee": ("left_knee", "left_ankle"),
    "right_elbow": ("right_shoulder", "right_elbow"),
    "right_wrist": ("right_elbow", "right_wrist"),
    "left_elbow": ("left_shoulder", "left_elbow"),
    "left_wrist": ("left_elbow", "left_wrist"),
}

frames = []
selected_part = None
edit_mode = None
frame_json_mapping = []
frame_json_ranges = []

def get_angle(x1, y1, x2, y2):
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

def get_image_path(name):
    return MATERIAL_ROOT / f"{name}.png"

def load_materials():
    return {name: pygame.image.load(str(get_image_path(name))).convert_alpha() for name in PART_NAMES}

def rotate_bound_pg(img, angle_cw, pivot_x, pivot_y):
    rot_ccw = -angle_cw
    rotated = pygame.transform.rotate(img, rot_ccw)
    w, h = img.get_size()
    nW, nH = rotated.get_size()
    dx = pivot_x - w / 2
    dy = pivot_y - h / 2
    rad = math.radians(angle_cw)
    move_x = nW / 2 + (dx * math.cos(rad) - dy * math.sin(rad))
    move_y = nH / 2 + (dx * math.sin(rad) + dy * math.cos(rad))
    return rotated, move_x, move_y

def render(surface, bg, mats, joints, adjusted_initial_joints, frame_idx,
           cfg=None, draw_bg=True, mirror=None, action=None):
    if cfg is not None:
        apply_config(cfg)
    # 整体水平镜像：mirror=None 用角色配置(SCENE_MIRROR)，True/False 强制指定
    # （双人偶对峙时，第二人偶强制取第一人偶的反向朝向）
    scene_mirror = SCENE_MIRROR if mirror is None else bool(mirror)
    # 镜像绕角色自身中心翻转（而不是整屏中心），保证位置不变、只反转朝向。
    # 注意 joints 已是加了站位偏移的渲染坐标，翻转中心必须用当前帧 pelvis，
    # 否则会把第二人偶（偏右侧）绕原点翻回第一人偶处造成重叠
    mirror_center_x = (joints.get("pelvis")
                       or adjusted_initial_joints.get("pelvis")
                       or {}).get("x", surface.get_width() // 2)

    # ===== 新骨骼渲染（rig_v2）：接管成功则直接返回；失败/关闭时走下面的旧逻辑 =====
    global _rig_v2_mod, _rig_v2_failed
    if RIG_V2 and not _rig_v2_failed:
        if _rig_v2_mod is None:
            try:
                import rig_v2
                _rig_v2_mod = rig_v2
            except Exception as exc:
                _rig_v2_failed = True
                print("[rig_v2] 模块加载失败，继续使用旧渲染:", exc)
        if _rig_v2_mod is not None:
            try:
                if _rig_v2_mod.render(
                        surface, bg, mats, joints, adjusted_initial_joints,
                        frame_idx, cfg=cfg, draw_bg=draw_bg,
                        mirror=scene_mirror,
                        character=(cfg or {}).get("character"),
                        draw_parts=PART_NAMES, draw_order=DRAW_ORDER,
                        action=action):
                    return
            except Exception as exc:
                _rig_v2_failed = True
                print("[rig_v2] 渲染失败，继续使用旧渲染:", exc)

    if draw_bg:
        surface.blit(bg, (0, 0))
    font = pygame.font.SysFont(None, 20)

    if DEBUG_MODE:
        for json_path, (start, end) in zip(frame_json_mapping, frame_json_ranges):
            if start <= frame_idx < end:
                current_json_frame = frame_idx - start + 1
                total_json_frames = end - start
                text = font.render(f"JSON: {json_path}, Frame: {current_json_frame}/{total_json_frames}", True, (255, 255, 255))
                surface.blit(text, (10, 10))
                break

    for part in sorted(PART_NAMES, key=lambda p: DRAW_ORDER[p]):
        img = mats[part]
        if PART_MIRROR.get(part, False):
            img = pygame.transform.flip(img, True, False)
        pivot_x, pivot_y, x_off, y_off = PART_PARAM[part]
        joint_name1, joint_name2 = PART_CONNECT[part]

        p1_cur = joints[joint_name1]
        p2_cur = joints[joint_name2]
        p1_base = adjusted_initial_joints[joint_name1]

        dx = p1_cur["x"] - p1_base["x"]
        dy = p1_cur["y"] - p1_base["y"]

        world_x = p1_base["x"] + dx + x_off
        world_y = p1_base["y"] + dy + y_off

        ang = get_angle(
            p1_cur["x"], p1_cur["y"],
            p2_cur["x"], p2_cur["y"]
        )
        ang = ang + ROT_OFF.get(part, 0.0)
        rotated, mvx, mvy = rotate_bound_pg(img, ang, pivot_x, pivot_y)

        new_w = int(rotated.get_width() * SCALE_FACTOR)
        new_h = int(rotated.get_height() * SCALE_FACTOR)
        rotated = pygame.transform.smoothscale(rotated, (new_w, new_h))

        mvx *= SCALE_FACTOR
        mvy *= SCALE_FACTOR
        # 整体镜像：绕角色自身中心水平翻转世界坐标（位置不变、朝向反转）
        if scene_mirror:
            world_x = 2 * mirror_center_x - world_x
        first_x = (world_x - surface.get_width() // 2) * SCALE_FACTOR + surface.get_width() // 2
        first_y = (world_y - surface.get_height() // 2 + 65) * SCALE_FACTOR + surface.get_height() // 2

        if scene_mirror:
            flipped = pygame.transform.flip(rotated, True, False)
            surface.blit(flipped, (first_x - (new_w - mvx), first_y - mvy))
        else:
            surface.blit(rotated, (first_x - mvx, first_y - mvy))

        if SHOW_PIVOTS:
            px_show = first_x
            pygame.draw.circle(surface, (0, 255, 255), (int(px_show), int(first_y)), 5)
            surface.blit(font.render(part, True, (0, 255, 255)), (int(px_show) + 6, int(first_y) - 6))

    if SHOW_JOINTS:
        for name, pos in joints.items():
            jx = pos["x"]
            if scene_mirror:
                jx = 2 * mirror_center_x - jx
            sx = (jx - surface.get_width() // 2) * SCALE_FACTOR + surface.get_width() // 2
            sy = (pos["y"] - surface.get_height() // 2 + 65) * SCALE_FACTOR + surface.get_height() // 2
            pygame.draw.circle(surface, (255, 0, 0), (int(sx), int(sy)), 4)
            surface.blit(font.render(name, True, (255, 0, 0)), (int(sx) + 6, int(sy) - 6))


def save_pivot_config():
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(PART_PARAM, f, indent=2)
    print(f"已保存 pivot 配置至 {CONFIG_PATH}")


def main():
    global paused, selected_part, edit_mode, frames, frame_json_mapping, frame_json_ranges
    pygame.init()
    JSON_LIST = JsonList

    frames = []
    adjusted_initial_joints = {}
    first_file = True
    start_frame = 0

    for json_path in JSON_LIST:
        with open(json_path, "r") as fp:
            data = json.load(fp)
        cur_frames = data["frames"]
        w, h = data["video_info"]["resolution"]

        for frame in cur_frames:
            if uses_joint_fix():
                if "right_elbow" in frame["joints"]:
                    frame["joints"]["right_elbow"]["y"] += 20
                if "left_elbow" in frame["joints"]:
                    frame["joints"]["left_elbow"]["y"] += 20
                if "right_shoulder" in frame["joints"] and "left_shoulder" in frame["joints"]:
                    mid = (frame["joints"]["right_shoulder"]["x"] + frame["joints"]["left_shoulder"]["x"]) / 2
                    frame["joints"]["right_shoulder"]["x"] = mid - 30
                    frame["joints"]["left_shoulder"]["x"] = mid

        if first_file:
            pelvis = cur_frames[0]["joints"]["pelvis"]
            offset_x = w // 2 - pelvis["x"]
            offset_y = h // 2 - pelvis["y"]
            for frame in cur_frames:
                for name, pos in frame["joints"].items():
                    pos["x"] += offset_x
                    pos["y"] += offset_y
            for name, pos in cur_frames[0]["joints"].items():
                adjusted_initial_joints[name] = {"x": pos["x"], "y": pos["y"]}
            first_file = False
        else:
            last = frames[-1]["joints"]["pelvis"]
            this = cur_frames[0]["joints"]["pelvis"]
            dx = last["x"] - this["x"]
            dy = last["y"] - this["y"]
            for frame in cur_frames:
                for name, pos in frame["joints"].items():
                    pos["x"] += dx
                    pos["y"] += dy

        end_frame = start_frame + len(cur_frames)
        frames += cur_frames
        frame_json_mapping += [json_path] * len(cur_frames)
        frame_json_ranges.append((start_frame, end_frame))
        start_frame = end_frame

    screen = pygame.display.set_mode((w, h))
    pygame.display.set_caption("Shadow Puppet")
    bg = pygame.transform.smoothscale(pygame.image.load(BACKGROUND_IMG).convert(), (w, h))
    mats = load_materials()
    finalize_pivots(mats)

    clock = pygame.time.Clock()
    idx = 0

    while True:
        time_delta = clock.tick(30) / 1000.0
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_s:
                    save_pivot_config()
                elif e.key == pygame.K_SPACE:
                    paused = not paused
                elif e.key == pygame.K_1:
                    edit_mode = "pivot"
                    print(" 进入 Pivot 编辑模式 (使用方向键调整)")
                elif e.key == pygame.K_2:
                    edit_mode = "joint"
                    print(" 进入 Joint 编辑模式 (使用方向键调整)")
                elif e.key == pygame.K_3:
                    edit_mode = "offset"
                    print(" 进入 Offset 编辑模式 (使用方向键调整, s 保存后重启生效)")
                elif e.key in [pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN] and selected_part:
                    delta = 1 if not pygame.key.get_mods() & pygame.KMOD_SHIFT else 10
                    if edit_mode == "pivot":
                        if e.key == pygame.K_LEFT:
                            PART_PARAM[selected_part][0] -= delta
                        elif e.key == pygame.K_RIGHT:
                            PART_PARAM[selected_part][0] += delta
                        elif e.key == pygame.K_UP:
                            PART_PARAM[selected_part][1] -= delta
                        elif e.key == pygame.K_DOWN:
                            PART_PARAM[selected_part][1] += delta
                    elif edit_mode == "joint":
                        joint_name = PART_CONNECT[selected_part][0]
                        if e.key == pygame.K_LEFT:
                            adjusted_initial_joints[joint_name]["x"] -= delta
                        elif e.key == pygame.K_RIGHT:
                            adjusted_initial_joints[joint_name]["x"] += delta
                        elif e.key == pygame.K_UP:
                            adjusted_initial_joints[joint_name]["y"] -= delta
                        elif e.key == pygame.K_DOWN:
                            adjusted_initial_joints[joint_name]["y"] += delta
                    elif edit_mode == "offset":
                        if e.key == pygame.K_LEFT:
                            PART_PARAM[selected_part][2] -= delta
                        elif e.key == pygame.K_RIGHT:
                            PART_PARAM[selected_part][2] += delta
                        elif e.key == pygame.K_UP:
                            PART_PARAM[selected_part][3] -= delta
                        elif e.key == pygame.K_DOWN:
                            PART_PARAM[selected_part][3] += delta
                elif paused:
                    if e.key == pygame.K_LEFT:
                        idx = (idx - 1) % len(frames)
                    elif e.key == pygame.K_RIGHT:
                        idx = (idx + 1) % len(frames)
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                mouse_x, mouse_y = e.pos
                for part in PART_NAMES:
                    joint_name = PART_CONNECT[part][0]
                    pos = adjusted_initial_joints.get(joint_name, {})
                    if pos and abs(pos["x"] - mouse_x) < 10 and abs(pos["y"] - mouse_y) < 10:
                        selected_part = part
                        print(f"选中部件: {selected_part}")
                        break

        render(screen, bg, mats, frames[idx]["joints"], adjusted_initial_joints, idx)
        pygame.display.flip()
        if not paused:
            idx = (idx + 1) % len(frames)
            


if __name__ == "__main__":
    main()

