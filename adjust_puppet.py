# -*- coding: utf-8 -*-
import os, json, math
from pathlib import Path
import pygame

import pygame_Alpha as base

PART_ORDER = [
    "head", "body",
    "right_hip", "right_knee", "left_hip", "left_knee",
    "right_elbow", "right_wrist", "left_elbow", "left_wrist",
]
PART_CN = {
    "head": "头", "body": "身体",
    "right_hip": "右大腿", "right_knee": "右小腿", "left_hip": "左大腿", "left_knee": "左小腿",
    "right_elbow": "右上臂", "right_wrist": "右前臂/手",
    "left_elbow": "左上臂", "left_wrist": "左前臂/手",
}
ACTION_FILES = [p for p in [
    "idle.json", "normal.json", "greet.json", "dance.json",
    "forward.json", "back.json", "jump.json", "scratch.json", "dun.json",
    "punch.json", "kick.json", "block.json", "lunge.json", "retreat.json", "hit.json",
] if (Path("actions") / p).exists()]

FONT_PATH = "font.ttf"
ACCENT = (255, 230, 90)
DEFAULT_DRAW = {
    "body": 3, "head": 2,
    "right_hip": 1, "right_knee": 3,
    "left_hip": 1, "left_knee": 3,
    "right_elbow": 4, "right_wrist": 4,
    "left_elbow": 1, "left_wrist": 2,
}


def load_action(path, cw, ch):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cur = data["frames"]
    pelvis = cur[0]["joints"]["pelvis"]
    ox, oy = cw // 2 - pelvis["x"], ch // 2 - pelvis["y"]
    for fr in cur:
        for name, pos in fr["joints"].items():
            pos["x"] += ox
            pos["y"] += oy
    adj = {name: {"x": pos["x"], "y": pos["y"]}
           for name, pos in cur[0]["joints"].items()}
    return cur, adj


def anchor_screen(part, adj, sw, sh):
    _, _, x_off, y_off = base.PART_PARAM[part]
    jname = base.PART_CONNECT[part][0]
    p1 = adj.get(jname)
    if not p1:
        return None
    sx = (p1["x"] + x_off - sw // 2) * base.SCALE_FACTOR + sw // 2
    sy = (p1["y"] + y_off - sh // 2 + 65) * base.SCALE_FACTOR + sh // 2
    return sx, sy


def save_config():
    base.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(base.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"parts": base.PART_PARAM, "rot": base.ROT_OFF,
                   "mirror": base.PART_MIRROR, "draw": base.DRAW_ORDER,
                   "pivot": base.PIVOT, "scene_mirror": base.SCENE_MIRROR},
                  f, indent=2, ensure_ascii=False)


def main():
    pygame.init()
    sw, sh = 1440, 900
    screen = pygame.display.set_mode((sw, sh), pygame.RESIZABLE)
    pygame.display.set_caption("皮影角色傻瓜式调整器")

    action_idx = 0
    frames, adj = load_action(str(Path("actions") / ACTION_FILES[action_idx]), sw, sh)
    def make_bg():
        bg = pygame.Surface((sw, sh))
        bg.fill((34, 36, 52))
        return bg
    bg = make_bg()

    mats = base.load_materials()
    base.finalize_pivots(mats)
    base.SHOW_PIVOTS = False
    base.SHOW_JOINTS = False
    if base.CURRENT_CHARACTER in base.CHARACTERS:
        char_idx = base.CHARACTERS.index(base.CURRENT_CHARACTER)
    else:
        char_idx = 0
    small = pygame.font.Font(FONT_PATH, 16) if os.path.exists(FONT_PATH) \
        else pygame.font.SysFont(None, 16)

    selected = None
    paused = True
    play_mode = False
    idx = 0
    dragging = False
    rotating = False
    last_mouse = None
    cycle_mode = False
    show_all = False
    show_map = True
    big_star = False
    msg = ""
    clock = pygame.time.Clock()

    def switch_action(delta, play=False):
        nonlocal action_idx, frames, adj, idx, paused
        action_idx = (action_idx + delta) % len(ACTION_FILES)
        frames, adj = load_action(str(Path("actions") / ACTION_FILES[action_idx]), sw, sh)
        idx = 0
        if not play:
            paused = True

    def recenter():
        nonlocal frames, adj
        frames, adj = load_action(str(Path("actions") / ACTION_FILES[action_idx]), sw, sh)

    def _dist(p1, p2):
        return math.hypot(p1["x"] - p2["x"], p1["y"] - p2["y"])

    def build_big():
        j = {k: dict(v) for k, v in adj.items()}
        for side in ("right", "left"):
            o = -1 if side == "right" else 1
            s = adj.get(side + "_shoulder"); e = adj.get(side + "_elbow"); w = adj.get(side + "_wrist")
            if s and e and w:
                L1 = _dist(s, e); L2 = _dist(e, w)
                a = math.radians(-15)
                ux, uy = o * math.cos(a), -math.sin(a)
                j[side + "_elbow"] = {"x": s["x"] + ux * L1, "y": s["y"] + uy * L1}
                j[side + "_wrist"] = {"x": j[side + "_elbow"]["x"] + ux * L2,
                                      "y": j[side + "_elbow"]["y"] + uy * L2}
            h = adj.get(side + "_hip"); k = adj.get(side + "_knee"); an = adj.get(side + "_ankle")
            if h and k and an:
                L3 = _dist(h, k); L4 = _dist(k, an)
                mag = math.hypot(0.6 * o, 1.15)
                ux, uy = 0.6 * o / mag, 1.15 / mag
                j[side + "_knee"] = {"x": h["x"] + ux * L3, "y": h["y"] + uy * L3}
                j[side + "_ankle"] = {"x": j[side + "_knee"]["x"] + ux * L4,
                                      "y": j[side + "_knee"]["y"] + uy * L4}
        return j

    def cur_joints():
        return build_big() if big_star else frames[idx]["joints"]

    def disp_anchor(p):
        raw = anchor_screen(p, cur_joints(), sw, sh)
        if not raw:
            return None
        x, y = raw
        if base.SCENE_MIRROR:
            x = sw - x
        return (x, y)

    def preview_geom():
        pw = 240
        x0 = max(12, sw - pw - 12)
        y0 = 150
        ph = sh - y0 - 60
        if ph < 90:
            ph = 90
        return (x0, y0, pw, ph)

    def preview_place():
        (x0, y0, pw, ph) = preview_geom()
        img = mats[selected]
        iw, ih = img.get_size()
        top = y0 + 22
        avail = ph - 22 - 6
        z = min(pw / iw, avail / ih)
        dx = x0 + (pw - iw * z) / 2
        dy = top + (avail - ih * z) / 2
        return (x0, y0, pw, ph, z, dx, dy, iw, ih)

    def joint_screen(name):
        p = cur_joints().get(name)
        if not p:
            return None
        sx = (p["x"] - sw // 2) * base.SCALE_FACTOR + sw // 2
        sy = (p["y"] - sh // 2 + 65) * base.SCALE_FACTOR + sh // 2
        if base.SCENE_MIRROR:
            sx = sw - sx
        return (sx, sy)

    def reset_part(p):
        base.PART_PARAM[p][2] = 0
        base.PART_PARAM[p][3] = 0
        base.ROT_OFF[p] = 0.0
        base.PART_MIRROR[p] = False
        base.PART_PARAM[p][0] = mats[p].get_width() // 2
        base.PART_PARAM[p][1] = 0
        base.PIVOT.pop(p, None)
        base.DRAW_ORDER[p] = DEFAULT_DRAW[p]

    def auto_vertical(p):
        img = mats[p]
        px, py = base.PART_PARAM[p][0], base.PART_PARAM[p][1]
        w, h = img.get_size()
        cx = cy = cnt = 0.0
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                if img.get_at((x, y)).a > 40:
                    cx += x; cy += y; cnt += 1
        if cnt < 8:
            return False
        cx /= cnt; cy /= cnt
        ang_img = base.get_angle(px, py, cx, cy)
        base.ROT_OFF[p] = round(-ang_img, 1)
        return True

    def bake_part(p):
        img = mats[p]
        px = int(base.PART_PARAM[p][0])
        py = int(base.PART_PARAM[p][1])
        rot = base.ROT_OFF.get(p, 0.0)
        mirror = base.PART_MIRROR.get(p, False)
        if abs(rot) < 0.05 and not mirror:
            return None
        if mirror:
            img = pygame.transform.flip(img, True, False)
        rotated, mvx, mvy = base.rotate_bound_pg(img, rot, px, py)
        npivx = int(round(mvx))
        npivy = int(round(mvy))
        w, h = rotated.get_size()
        x0 = x1 = y0 = y1 = None
        for yy in range(h):
            for xx in range(w):
                if rotated.get_at((xx, yy)).a > 8:
                    if x0 is None or xx < x0: x0 = xx
                    if x1 is None or xx > x1: x1 = xx
                    if y0 is None or yy < y0: y0 = yy
                    if y1 is None or yy > y1: y1 = yy
        if x0 is None:
            return None
        pad = 4
        x0 = max(0, x0 - pad); y0 = max(0, y0 - pad)
        x1 = min(w - 1, x1 + pad); y1 = min(h - 1, y1 + pad)
        out = rotated.subsurface((x0, y0, x1 - x0 + 1, y1 - y0 + 1)).copy()
        new_px = npivx - x0
        new_py = npivy - y0
        path = str(base.MATERIAL_ROOT / (p + ".png"))
        pygame.image.save(out, path)
        mats[p] = out
        base.PART_PARAM[p][0] = new_px
        base.PART_PARAM[p][1] = new_py
        if new_px == out.get_width() // 2 and new_py == 0:
            base.PIVOT.pop(p, None)
        else:
            base.PIVOT[p] = [new_px, new_py]
        base.ROT_OFF[p] = 0.0
        base.PART_MIRROR[p] = False
        return path

    LINKS = [
        ("head_top", "upper_neck"),
        ("upper_neck", "pelvis"),
        ("upper_neck", "right_shoulder"),
        ("upper_neck", "left_shoulder"),
        ("right_shoulder", "right_elbow"), ("right_elbow", "right_wrist"),
        ("left_shoulder", "left_elbow"), ("left_elbow", "left_wrist"),
        ("pelvis", "right_hip"), ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
        ("pelvis", "left_hip"), ("left_hip", "left_knee"), ("left_knee", "left_ankle"),
    ]
    NODES = []
    for a, b in LINKS:
        if a not in NODES: NODES.append(a)
        if b not in NODES: NODES.append(b)

    def draw_map():
        pts = {n: adj.get(n) for n in NODES if adj.get(n)}
        if not pts:
            return
        x0, y0, pw, ph = preview_geom()
        pane = pygame.Surface((pw, ph), pygame.SRCALPHA)
        pane.fill((15, 15, 25, 230))
        screen.blit(pane, (x0, y0))
        title = small.render("骨架结构示意图 (红=关节, 连线=骨架段)", True, (255, 255, 255))
        screen.blit(title, (x0 + 6, y0 + 2))
        top = y0 + 24
        avail = ph - 24 - 6
        xs = [p["x"] for p in pts.values()]
        ys = [p["y"] for p in pts.values()]
        mnx, mxx = min(xs), max(xs)
        mny, mxy = min(ys), max(ys)
        z = min((pw - 16) / max(1, mxx - mnx), (avail - 16) / max(1, mxy - mny))
        ox = x0 + (pw - (mxx - mnx) * z) / 2 - mnx * z
        oy = y0 + 24 + (avail - (mxy - mny) * z) / 2 - mny * z
        pos = {}
        for n, p in pts.items():
            pos[n] = (int(p["x"] * z + ox), int(p["y"] * z + oy))
        for a, b in LINKS:
            if a in pos and b in pos:
                pygame.draw.line(screen, (150, 170, 200), pos[a], pos[b], 2)
        for n, (cx, cy) in pos.items():
            if n.startswith("right_"):
                col = (90, 220, 255)
            elif n.startswith("left_"):
                col = (255, 180, 90)
            else:
                col = (255, 90, 90)
            pygame.draw.circle(screen, col, (cx, cy), 5)
            t = small.render(n, True, col)
            screen.blit(t, (cx + 6, cy - 6))

    def info_lines():
        a = ACTION_FILES[action_idx]
        ch = base.CHAR_DISPLAY.get(base.CURRENT_CHARACTER, base.CURRENT_CHARACTER)
        play_st = "▶ 播放" if not paused else "⏸ 暂停"
        cyc = " [C:轮播开]" if cycle_mode else " [C:轮播关]"
        line1 = f"角色:{ch}  动作:{a}  {play_st}  帧 {idx}/{len(frames)}   空格=播放/暂停  ,/.=逐帧  [ ]=换动作  Tab=切角色  D=大字校准{cyc}"
        line2 = "左键点选; 左键拖=移动  右键拖=旋转  Q/E=旋转 方向键=微移  M=本件镜像  L=全部镜像"
        line3 = "PgUp/PgDn=图层  1-0选件  R=复位  B=全部回初始  O=自动竖直  X=烘焙入素材  -=缩放  F=朝向  V=全标记  Y=示意图"
        line3b = "P=播放模式(纯净预览) S保存 ESC退出 窗口可拉伸; 定枢轴: 右上原图面板点真实关节; 灰线=该段骨架轴"
        if selected:
            px = base.PART_PARAM[selected]
            rot = base.ROT_OFF.get(selected, 0.0)
            mir = "镜" if base.PART_MIRROR.get(selected, False) else ""
            piv = base.PIVOT.get(selected)
            piv_s = f" 枢轴=({piv[0]},{piv[1]})" if piv else ""
            line4 = f"选中: {selected} {PART_CN.get(selected, '')}{mir}  偏移({px[2]:.1f},{px[3]:.1f})  rot={rot:.1f}  层={base.DRAW_ORDER[selected]}{piv_s}"
        else:
            line4 = "未选中部件"
        return [line1, line2, line3, line3b, line4, msg]

    while True:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return
            elif e.type == pygame.VIDEORESIZE:
                sw, sh = e.w, e.h
                screen = pygame.display.set_mode((sw, sh), pygame.RESIZABLE)
                bg = make_bg()
                recenter()
                idx = min(idx, len(frames) - 1)
                msg = f"窗口已调为 {sw}x{sh}"
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    if play_mode:
                        play_mode = False
                        paused = True
                        msg = "已退出播放模式"
                    else:
                        return
                elif e.key == pygame.K_p:
                    play_mode = not play_mode
                    if play_mode:
                        big_star = False
                        paused = False
                        msg = "播放模式: 空格=暂停/播放  [ ]=切动作  C=轮播  P=退出"
                    else:
                        paused = True
                        msg = "已退出播放模式"
                elif e.key == pygame.K_SPACE:
                    paused = not paused
                elif e.key == pygame.K_s:
                    save_config(); msg = f"已保存角色配置 -> {base.CONFIG_PATH}（重启自动生效）"
                elif e.key == pygame.K_c:
                    cycle_mode = not cycle_mode
                    paused = False if cycle_mode else paused
                    msg = "轮播全部动作: 开" if cycle_mode else "轮播: 关"
                elif e.key == pygame.K_f:
                    base.SCENE_MIRROR = not base.SCENE_MIRROR
                    msg = "整体朝向镜像(人物+动作) 开" if base.SCENE_MIRROR else "整体朝向镜像 关"
                elif e.key == pygame.K_v:
                    show_all = not show_all
                    msg = "显示全部标记 开" if show_all else "仅显示选中部件标记"
                elif e.key == pygame.K_y:
                    show_map = not show_map
                    msg = "结构示意图 开" if show_map else "结构示意图 关"
                elif e.key == pygame.K_d:
                    big_star = not big_star
                    paused = True
                    msg = "大字校准姿势 开（四肢摊开，逐段校准枢轴/重叠；不影响任何动作文件）" if big_star else "大字校准姿势 关（回到当前动作）"
                elif e.key == pygame.K_TAB:
                    char_idx = (char_idx + 1) % len(base.CHARACTERS)
                    name = base.CHARACTERS[char_idx]
                    base.set_character(name)
                    mats = base.load_materials()
                    base.finalize_pivots(mats)
                    base.SHOW_PIVOTS = False
                    base.SHOW_JOINTS = False
                    selected = None
                    msg = f"已切换角色: {base.CHAR_DISPLAY.get(name, name)} ({name})"
                elif e.key == pygame.K_r and selected:
                    reset_part(selected)
                    msg = f"已把 {selected} 复位为初始(偏移0/旋转0/镜像关/枢轴顶中/图层默认)"
                elif e.key == pygame.K_b:
                    for p in PART_ORDER:
                        reset_part(p)
                    msg = "全部部件已复位为初始姿态（原有点位已清除，请重新调）"
                elif e.key == pygame.K_o and selected:
                    if auto_vertical(selected):
                        msg = f"{selected} 已按枢轴自动竖直 rot={base.ROT_OFF[selected]:.1f}（可再 Q/E 微调，X 烘焙入素材）"
                    else:
                        msg = f"{selected} 图太简单，自动竖直失败，用 Q/E 手动转"
                elif e.key == pygame.K_x and selected:
                    path = bake_part(selected)
                    if path:
                        msg = f"已把 {selected} 烘焙写回: {path}（rot已归0；内容不裁剪，不会再变小）"
                    else:
                        msg = f"{selected} 旋转为0且未镜像，无需烘焙"
                elif e.key == pygame.K_m and selected:
                    base.PART_MIRROR[selected] = not base.PART_MIRROR[selected]
                    msg = f"{selected} 镜像 {'开' if base.PART_MIRROR[selected] else '关'}"
                elif e.key == pygame.K_l:
                    for p in PART_ORDER:
                        base.PART_MIRROR[p] = not base.PART_MIRROR[p]
                    st = all(base.PART_MIRROR[p] for p in PART_ORDER)
                    msg = f"全部部件镜像整体{'开启' if st else '关闭'}"
                elif e.key == pygame.K_q and selected:
                    d = 5 if pygame.key.get_mods() & pygame.KMOD_SHIFT else 1
                    if base.SCENE_MIRROR:
                        d = -d
                    base.ROT_OFF[selected] = round(base.ROT_OFF[selected] - d, 2)
                elif e.key == pygame.K_e and selected:
                    d = 5 if pygame.key.get_mods() & pygame.KMOD_SHIFT else 1
                    if base.SCENE_MIRROR:
                        d = -d
                    base.ROT_OFF[selected] = round(base.ROT_OFF[selected] + d, 2)
                elif e.key in (pygame.K_PAGEUP, pygame.K_PAGEDOWN) and selected:
                    d = 1 if e.key == pygame.K_PAGEUP else -1
                    base.DRAW_ORDER[selected] += d
                    msg = f"{selected} 图层={base.DRAW_ORDER[selected]} (大=靠前)"
                elif e.key in (pygame.K_MINUS, pygame.K_EQUALS):
                    step = -0.02 if e.key == pygame.K_MINUS else 0.02
                    base.SCALE_FACTOR = round(max(0.05, base.SCALE_FACTOR + step), 2)
                    msg = f"SCALE_FACTOR = {base.SCALE_FACTOR}（需写入 pygame_Alpha.py:10 才持久）"
                elif e.key == pygame.K_COMMA:
                    idx = (idx - 1) % len(frames)
                elif e.key == pygame.K_PERIOD:
                    idx = (idx + 1) % len(frames)
                elif e.key in (pygame.K_LEFTBRACKET, pygame.K_RIGHTBRACKET):
                    d = 1 if e.key == pygame.K_RIGHTBRACKET else -1
                    switch_action(d)
                    msg = f"已切换动作: {ACTION_FILES[action_idx]}"
                elif pygame.K_0 <= e.key <= pygame.K_9:
                    i = 9 if e.key == pygame.K_0 else e.key - pygame.K_1
                    if i < len(PART_ORDER):
                        selected = PART_ORDER[i]
                        msg = f"选中 {selected} {PART_CN.get(selected, '')}"
                elif e.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN) and selected:
                    d = 10 if pygame.key.get_mods() & pygame.KMOD_SHIFT else 1
                    hflip = -1 if base.SCENE_MIRROR else 1
                    if e.key == pygame.K_LEFT: base.PART_PARAM[selected][2] -= d * hflip
                    elif e.key == pygame.K_RIGHT: base.PART_PARAM[selected][2] += d * hflip
                    elif e.key == pygame.K_UP: base.PART_PARAM[selected][3] -= d
                    elif e.key == pygame.K_DOWN: base.PART_PARAM[selected][3] += d
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and not play_mode:
                mx, my = e.pos
                x0, y0, pw, ph = preview_geom()
                if selected and x0 <= mx <= x0 + pw and y0 <= my <= y0 + ph:
                    _, _, _, _, z, dx, dy, iw, ih = preview_place()
                    sx = int((mx - dx) / z)
                    sy = int((my - dy) / z)
                    sx = max(0, min(iw - 1, sx)); sy = max(0, min(ih - 1, sy))
                    base.PIVOT[selected] = [sx, sy]
                    base.PART_PARAM[selected][0] = sx
                    base.PART_PARAM[selected][1] = sy
                    msg = f"{selected} 枢轴已设为 ({sx},{sy})（原图坐标，十字标处即关节）"
                else:
                    best, bd = None, 1e9
                    for p in PART_ORDER:
                        a = disp_anchor(p)
                        if not a: continue
                        dd = (a[0]-mx)**2 + (a[1]-my)**2
                        if dd < bd: best, bd = p, dd
                    if best and bd < 60*60:
                        selected = best
                        dragging = True
                        last_mouse = e.pos
                        msg = f"选中 {best} {PART_CN.get(best, '')}"
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 3 and selected and not play_mode:
                rotating = True
                last_mouse = e.pos
            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1 and not play_mode:
                dragging = False; last_mouse = None
            elif e.type == pygame.MOUSEBUTTONUP and e.button == 3 and not play_mode:
                rotating = False; last_mouse = None
            elif e.type == pygame.MOUSEMOTION and dragging and selected and not play_mode:
                mx, my = e.pos
                if last_mouse:
                    dx = (mx - last_mouse[0]) / base.SCALE_FACTOR
                    dy = (my - last_mouse[1]) / base.SCALE_FACTOR
                    if base.SCENE_MIRROR:
                        dx = -dx
                    base.PART_PARAM[selected][2] += dx
                    base.PART_PARAM[selected][3] += dy
                last_mouse = e.pos
            elif e.type == pygame.MOUSEMOTION and rotating and selected and not play_mode:
                mx, _ = e.pos
                if last_mouse:
                    ang = (mx - last_mouse[0]) * 0.5
                    if base.SCENE_MIRROR:
                        ang = -ang
                    base.ROT_OFF[selected] = round(base.ROT_OFF[selected] + ang, 2)
                last_mouse = e.pos

        base.render(screen, bg, mats, cur_joints(), adj, 0)

        if play_mode:
            hint = small.render(
                f"▶ 播放模式  动作:{ACTION_FILES[action_idx]}  (P退出  空格=暂停/播放  [ ]=切动作  C=轮播)",
                True, (180, 190, 220))
            screen.blit(hint, (12, 10))
        else:
            for p in PART_ORDER:
                a = disp_anchor(p)
                if not a: continue
                if p == selected:
                    pygame.draw.circle(screen, ACCENT, (int(a[0]), int(a[1])), 6)
                    lab = small.render(p, True, ACCENT)
                    screen.blit(lab, (int(a[0]) + 7, int(a[1]) - 7))
                elif show_all:
                    pygame.draw.circle(screen, (0, 255, 255), (int(a[0]), int(a[1])), 4)
                    lab = small.render(p, True, (0, 255, 255))
                    screen.blit(lab, (int(a[0]) + 6, int(a[1]) - 6))
                else:
                    pygame.draw.circle(screen, (120, 135, 165), (int(a[0]), int(a[1])), 3)

            if selected:
                jn1, jn2 = base.PART_CONNECT[selected]
                s1, s2 = joint_screen(jn1), joint_screen(jn2)
                if s1 and s2:
                    pygame.draw.line(screen, (140, 155, 185),
                                     (int(s1[0]), int(s1[1])), (int(s2[0]), int(s2[1])), 1)
                for jn in (jn1, jn2):
                    s = joint_screen(jn)
                    if s:
                        pygame.draw.circle(screen, (255, 70, 70), (int(s[0]), int(s[1])), 5)
                        lab = small.render(jn, True, (255, 70, 70))
                        screen.blit(lab, (int(s[0]) + 6, int(s[1]) - 6))
            elif show_all:
                for jn in NODES:
                    s = joint_screen(jn)
                    if s:
                        pygame.draw.circle(screen, (255, 70, 70), (int(s[0]), int(s[1])), 3)

            if selected:
                x0, y0, pw, ph, z, dx, dy, iw, ih = preview_place()
                pane = pygame.Surface((pw, ph), pygame.SRCALPHA)
                pane.fill((15, 15, 25, 220))
                screen.blit(pane, (x0, y0))
                label = small.render("部件原图  点这儿定枢轴(关节):", True, (255, 255, 255))
                screen.blit(label, (x0 + 6, y0 + 2))
                img = mats[selected]
                scaled = pygame.transform.smoothscale(
                    img, (max(1, int(iw * z)), max(1, int(ih * z))))
                screen.blit(scaled, (int(dx), int(dy)))
                pxx, pyy = base.PART_PARAM[selected][0], base.PART_PARAM[selected][1]
                cx, cy = int(dx + pxx * z), int(dy + pyy * z)
                pygame.draw.line(screen, (255, 60, 60), (cx - 10, cy), (cx + 10, cy), 2)
                pygame.draw.line(screen, (255, 60, 60), (cx, cy - 10), (cx, cy + 10), 2)
                pygame.draw.circle(screen, (255, 255, 255), (cx, cy), 3)
            elif show_map:
                draw_map()

            yy = 6
            for ln in info_lines():
                if not ln:
                    continue
                t = small.render(ln, True, (255, 255, 255))
                screen.blit(t, (8, yy)); yy += 18
            pygame.draw.rect(screen, (255, 255, 255), (8, yy + 2, sw - 16, 60), 1)
            yy += 8
            for i, p in enumerate(PART_ORDER):
                col = ACCENT if p == selected else (200, 200, 200)
                k = str((i + 1) % 10)
                t = small.render(f"[{k}] {PART_CN[p]} ({p})", True, col)
                if i % 2 == 0:
                    screen.blit(t, (16, yy))
                else:
                    screen.blit(t, (sw // 2 + 10, yy)); yy += 18

        pygame.display.flip()
        if not paused:
            idx += 1
            if idx >= len(frames):
                if cycle_mode:
                    switch_action(1, play=True)
                else:
                    idx = 0
        clock.tick(30)


if __name__ == "__main__":
    main()
