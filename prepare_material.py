# -*- coding: utf-8 -*-
import os, sys, math, json
import numpy as np
from PIL import Image

SRC = "新孙悟空"
# 也可用命令行指定源目录: python prepare_material.py [原始素材目录]
if len(sys.argv) > 1:
    SRC = sys.argv[1]
if not os.path.isdir(SRC):
    sys.exit(
        f"找不到原始素材目录: {SRC}\n"
        "请把 `新孙悟空/` 放回项目根目录，或用命令行指定路径:\n"
        "  python prepare_material.py <原始素材目录>"
    )
DST = "xinwukong_material"
CX = 1600.0
RESAMPLE = 0.28

def load_arr(name):
    return np.array(Image.open(os.path.join(SRC, name + ".png")).convert("RGBA"))

def save_arr(arr, name):
    os.makedirs(DST, exist_ok=True)
    im = Image.fromarray(arr, "RGBA")
    if RESAMPLE != 1.0:
        im = im.resize((max(1, int(im.width * RESAMPLE)), max(1, int(im.height * RESAMPLE))), Image.LANCZOS)
    im.save(os.path.join(DST, name + ".png"))

def crop_only(arr, pad=6):
    a = arr[..., 3] > 8
    ys, xs = np.where(a)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    y0 = max(0, y0 - pad); y1 = min(arr.shape[0] - 1, y1 + pad)
    x0 = max(0, x0 - pad); x1 = min(arr.shape[1] - 1, x1 + pad)
    return arr[y0:y1 + 1, x0:x1 + 1]

def rotate_pivot(arr, P, Q, pad=6):
    H, W = arr.shape[:2]
    px, py = P
    vx, vy = Q[0] - px, Q[1] - py
    if vy == 0:
        raise ValueError("bad anchor")
    ang = math.atan2(vx, vy)
    c, s = math.cos(ang), math.sin(ang)
    a = arr[..., 3] > 8
    ys, xs = np.where(a)
    u = (xs - px) * c - (ys - py) * s
    v = (xs - px) * s + (ys - py) * c
    umin, umax = int(math.floor(u.min())), int(math.ceil(u.max()))
    vmin, vmax = int(math.floor(v.min())), int(math.ceil(v.max()))
    half = max(abs(umin), abs(umax))
    dx = half + 1
    v0 = min(0, vmin)
    dy = -v0
    Wn = 2 * half + 3
    Hn = int(vmax - v0) + pad + 2
    out = np.zeros((Hn, Wn, 4), dtype=np.uint8)
    oy, ox = np.mgrid[0:Hn, 0:Wn]
    dex = ox - dx
    dey = oy - dy
    sx = px + c * dex + s * dey
    sy = py - s * dex + c * dey
    sxi = np.clip(np.rint(sx).astype(int), 0, W - 1)
    syi = np.clip(np.rint(sy).astype(int), 0, H - 1)
    src_px = arr[syi, sxi]
    inside = (sx >= 0) & (sx < W) & (sy >= 0) & (sy < H)
    out[..., :] = np.where(inside[..., None], src_px, 0)
    return out

def mirror(P):
    return (CX - P[0], P[1])

SPECS = [
    ("头",     "head",        (944, 283),  (1010, 800)),
    ("身体",   "body",        (1003, 824), (870, 1600)),
    ("左腿上", "right_hip",   (754, 1540), (620, 2030)),
    ("左腿下", "right_knee",  (525, 1985), (380, 2400)),
    ("右腿上", "left_hip",    (970, 1590), (1050, 2140)),
    ("右腿下", "left_knee",   (1150, 2020), (1320, 2430)),
    ("右胳膊", "right_elbow", (909, 961),  (700, 1200)),
    ("左胳膊", "left_elbow",  mirror((909, 961)), mirror((700, 1200))),
    ("右手",   "right_wrist", None, None),
    ("左手",   "left_wrist",  None, None),
]

if __name__ == "__main__":
    report = {}
    for cn, en, P, Q in SPECS:
        arr = load_arr(cn)
        if P is not None:
            out = rotate_pivot(arr, P, Q)
        else:
            out = crop_only(arr)
        save_arr(out, en)
        ys, xs = np.where(out[..., 3] > 8)
        report[en] = [cn, out.shape[1], out.shape[0],
                      int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
    print(json.dumps(report, ensure_ascii=False, indent=2))
