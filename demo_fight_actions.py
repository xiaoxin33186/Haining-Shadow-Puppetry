# -*- coding: utf-8 -*-
"""
《孙悟空 · 打戏六动作》皮影演示脚本。
完全复用 main.py 的动画链路：main.load_frames 加载动作 JSON，
pygame_Alpha.render 用皮影素材渲染，展示真实皮影孙悟空依次表演
出拳/踢腿/格挡/前冲/后撤/受击 六个动作（动作间插入待机帧）。
操作：ESC 退出，空格 暂停/继续。
"""
import sys
import time
import pygame
import pygame.freetype
import pygame_Alpha as pg_base
from main import load_frames

WIN_W, WIN_H = 1280, 720
FPS = 45
IDLE_JSON = "actions/normal.json"
DEMO_ACTIONS = [  # (显示名, json 文件名)
    ("出拳", "punch"),
    ("踢腿", "kick"),
    ("格挡", "block"),
    ("前冲", "lunge"),
    ("后撤", "retreat"),
    ("受击", "hit"),
]
IDLE_BETWEEN = FPS // 2  # 每个动作之间播 0.5 秒待机


def to_canvas(joints, sw, sh, tw=WIN_W, th=WIN_H):
    """把 joints 从源画布(sw,sh) 等比缩放到目标画布并居中（以骨盆为锚）。"""
    p = joints["pelvis"]
    s = min(tw / sw, th / sh)
    ox, oy = tw / 2, th / 2
    out = {}
    for k, v in joints.items():
        out[k] = {"x": ox + (v["x"] - p["x"]) * s, "y": oy + (v["y"] - p["y"]) * s}
    return out


def main():
    pygame.init()
    print("加载待机动作 normal.json ...")
    idle_frames, idle_init, sw, sh = load_frames([IDLE_JSON])
    if not idle_frames:
        print("[错误] 待机动作加载失败")
        pygame.quit()
        return

    # 预加载六个打戏动作
    action_datas = []
    for name, key in DEMO_ACTIONS:
        fr, _, aw, ah = load_frames([f"actions/{key}.json"])
        if not fr:
            print(f"[错误] 动作 {name}({key}) 加载失败")
            pygame.quit()
            return
        action_datas.append((name, fr, aw, ah))
        print(f"  加载 {name}: {len(fr)}帧 ({aw}x{ah})")

    screen = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption("孙悟空 · 打戏六动作演示 (ESC退出 / 空格暂停)")

    # 背景 + 皮影素材（与 main.py 初始化一致）
    try:
        background_img = pygame.image.load(pg_base.BACKGROUND_IMG).convert()
        bg = pygame.transform.smoothscale(background_img, (WIN_W, WIN_H))
    except Exception as e:
        print(f"[警告] 加载背景失败: {e}")
        bg = pygame.Surface((WIN_W, WIN_H))
        bg.fill((245, 235, 220))
    try:
        mats = pg_base.load_materials()
        for part, img in mats.items():
            if part in pg_base.PART_PARAM:
                mw, mh = img.get_size()
                pg_base.PART_PARAM[part][0] = mw // 2
                pg_base.PART_PARAM[part][1] = 0
        print(f"皮影素材加载成功 ({len(mats)} 个部位)")
    except Exception as e:
        print(f"[错误] 加载皮影素材失败: {e}")
        mats = {}

    init_idle_canvas = to_canvas(idle_init, sw, sh)
    # 待机帧转换为画布坐标
    idle_canvas = [to_canvas(fr["joints"], sw, sh) for fr in idle_frames]
    act_canvas = []
    for name, fr, aw, ah in action_datas:
        act_canvas.append((name, [to_canvas(f["joints"], aw, ah) for f in fr]))

    font = pygame.font.SysFont("simhei", 42)
    hint_font = pygame.font.SysFont("simhei", 20)

    # 播放序列：待机 -> 动作 -> 待机 -> 动作 ... 最后停在待机
    seq = []
    for name, frames in act_canvas:
        seq.append(("idle", None))
        seq.append((name, frames))
    seq.append(("idle", None))

    clock = pygame.time.Clock()
    si, ci, paused = 0, 0, False
    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key == pygame.K_SPACE:
                    paused = not paused

        if not paused:
            kind, frames = seq[si]
            if kind == "idle":
                joints = idle_canvas[ci % len(idle_canvas)]
                name_show = "待机"
            else:
                joints = frames[ci]
                name_show = kind
            ci += 1
            total = len(idle_canvas) if kind == "idle" else len(frames)
            if ci >= total:
                ci = 0
                si = min(si + 1, len(seq) - 1)

            screen.blit(bg, (0, 0))
            if mats and joints:
                pg_base.render(screen, bg, mats, joints, init_idle_canvas, frame_idx=0)
            text = font.render(f"动作：{name_show}", True, (255, 255, 255))
            screen.blit(text, (30, 30))
            hint = hint_font.render("ESC 退出   空格 暂停", True, (255, 255, 255))
            screen.blit(hint, (30, WIN_H - 40))
            pygame.display.flip()

        clock.tick(FPS)

    pygame.quit()
    print("演示结束")


if __name__ == "__main__":
    main()
