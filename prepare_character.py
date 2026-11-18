# -*- coding: utf-8 -*-
"""把"原始角色素材文件夹"转成引擎可直接用的 10 张标准素材。

用法:
    python prepare_character.py <原始目录> <输出目录名>
示例:
    python prepare_character.py 沙僧 sha_wujing_material
    python prepare_character.py 怪物 bull_demon_king_material

输出目录名与部件 PNG 一律英文命名。
支持三种命名习惯:
  A) 上/下分段的: 右胳膊 右手 右腿上 右腿下 左腿上 左腿下 ... (同"新孙悟空")
  B) 每侧独立命名的: 头 身体 右/左胳膊 右/左手 右/左腿(或大腿) 右/左脚 ... (同"牛魔王""沙僧")
  C) 只画一侧、不带左右的: 头 身体 胳膊 手臂 大腿 脚 ... (同"新沙僧")
同一侧缺图时自动用对侧镜像补齐。

可选: 在原始目录放 pivots.json 指定关节枢轴（源图像素坐标），例如
  {"pivots": {"head": [560, 10], "right_elbow": [828, 128]},
   "rot": {"head": -8.1}}
模板见仓库根目录 pivots.example.json（复制为 pivots.json 放进原始素材目录）。
未指定枢轴时沿用引擎默认（图片宽度一半、顶部）。
"""
import os, sys, json
import numpy as np
from PIL import Image

TARGET_BODY_H = 243

def load_arr(path):
    return np.array(Image.open(path).convert("RGBA"))

def crop_box(arr, pad=6):
    a = arr[..., 3] > 8
    ys, xs = np.where(a)
    x0 = max(0, xs.min() - pad); x1 = min(arr.shape[1] - 1, xs.max() + pad)
    y0 = max(0, ys.min() - pad); y1 = min(arr.shape[0] - 1, ys.max() + pad)
    return x0, y0, x1, y1

def crop_arr(arr, pad=6):
    x0, y0, x1, y1 = crop_box(arr, pad)
    return arr[y0:y1 + 1, x0:x1 + 1]

def mirror_arr(arr):
    return np.ascontiguousarray(arr[:, ::-1, :])

def scale_to_target(arr, factor):
    if factor <= 0 or abs(factor - 1.0) < 1e-6:
        return arr
    im = Image.fromarray(arr, "RGBA")
    im = im.resize((max(1, int(im.width * factor)), max(1, int(im.height * factor))), Image.LANCZOS)
    return np.array(im)

def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    src, dst = sys.argv[1], sys.argv[2]
    if not os.path.isdir(src):
        print("原始目录不存在:", src)
        return 1
    os.makedirs(dst, exist_ok=True)

    names = {}
    # 只画一侧、不带“左/右”的文件名（如：胳膊 手臂 大腿 脚）
    single_side = {"胳膊": "右胳膊", "手臂": "右手",
                   "大腿": "右腿", "腿": "右腿", "脚": "右脚"}
    for f in os.listdir(src):
        if not f.endswith(".png"):
            continue
        base = f[:-4]
        for key in ("身体", "头"):
            if key in base:
                names[key] = f
        for side in ("右", "左"):
            for kind in ("胳膊", "手", "腿", "脚"):
                if side in base and kind in base:
                    names[side + kind] = f
        if "右" not in base and "左" not in base and base in single_side:
            names.setdefault(single_side[base], f)

    raw = {}
    for key, f in names.items():
        raw[key] = load_arr(os.path.join(src, f))
    opposite = {"左胳膊": "右胳膊", "右胳膊": "左胳膊",
                "左手": "右手", "右手": "左手",
                "左腿": "右腿", "右腿": "左腿",
                "左脚": "右脚", "右脚": "左脚",
                "左腿上": "右腿上", "右腿上": "左腿上",
                "左腿下": "右腿下", "右腿下": "左腿下"}
    has_leg_b = any(k in names for k in ("右腿", "左腿", "右脚", "左脚"))
    for key in ("身体", "头", "右胳膊", "右手", "左胳膊", "左手",
                "右腿", "右脚", "左腿", "左脚", "右腿上", "右腿下",
                "左腿上", "左腿下"):
        if key in ("右腿上", "右腿下", "左腿上", "左腿下") and has_leg_b:
            continue
        if key not in names and opposite.get(key) not in names:
            print("提示: 缺少", key)

    side_map = {"身体": "body", "头": "head",
                "右胳膊": "right_elbow", "右手": "right_wrist",
                "左胳膊": "left_elbow", "左手": "left_wrist"}
    out = {}

    def add(engine, key):
        if engine in out:
            return
        if key in raw:
            out[engine] = raw[key]
        else:
            mirror_of = {"right_elbow": "左胳膊", "right_wrist": "左手",
                         "left_elbow": "右胳膊", "left_wrist": "右手",
                         "right_hip": "左腿", "right_knee": "左脚",
                         "left_hip": "右腿", "left_knee": "右脚"}.get(engine)
            if mirror_of in raw:
                out[engine] = mirror_arr(raw[mirror_of])
            else:
                out[engine] = None

    if "右腿上" in names or "左腿上" in names:
        for cn, engine in side_map.items():
            add(engine, cn)
        for engine, side in (("right_hip", "左腿上"), ("right_knee", "左腿下"),
                             ("left_hip", "右腿上"), ("left_knee", "右腿下")):
            if side in raw:
                out[engine] = raw[side]
            else:
                out[engine] = None
    else:
        for cn, engine in side_map.items():
            add(engine, cn)
        for engine, key in (("right_hip", "右腿"), ("right_knee", "右脚"),
                            ("left_hip", "左腿"), ("left_knee", "左脚")):
            add(engine, key)

    missing = [k for k, v in out.items() if v is None]
    if missing:
        print("无法生成部件(原始目录缺少对应图且无对侧可镜像):", missing)
        return 1

    cropped = {engine: crop_arr(arr) for engine, arr in out.items()}
    body_h = max(1, cropped["body"].shape[0])
    factor = TARGET_BODY_H / body_h
    print(f"参考身体内容高 {body_h}px -> 缩放 {factor:.3f}")
    for engine, arr in cropped.items():
        scaled = scale_to_target(arr, factor)
        Image.fromarray(scaled, "RGBA").save(os.path.join(dst, engine + ".png"))

    # 可选 pivots.json：为部件指定关节枢轴（源图像素坐标）
    pivot_cfg = {}
    rot_cfg = {}
    pcfg_path = os.path.join(src, "pivots.json")
    if os.path.isfile(pcfg_path):
        with open(pcfg_path, "r", encoding="utf-8") as f:
            pdata = json.load(f)
        pivot_cfg = pdata.get("pivots", {})
        rot_cfg = pdata.get("rot", {})

    mirror_of = {"left_elbow": "right_elbow", "left_wrist": "right_wrist",
                 "left_hip": "right_hip", "left_knee": "right_knee"}
    boxes = {engine: crop_box(arr) for engine, arr in out.items()}
    pivots = {}
    for engine in out:
        src_engine = mirror_of.get(engine, engine)
        if engine in pivot_cfg:
            px, py = pivot_cfg[engine]
        elif src_engine in pivot_cfg:
            px, py = pivot_cfg[src_engine]
        else:
            continue
        x0, y0, _, _ = boxes[src_engine]
        spx = int(round((px - x0) * factor))
        spy = int(round((py - y0) * factor))
        if engine in mirror_of:
            # 左侧是右侧缩放后镜像得到的，枢轴也要跟着镜像
            sw = max(1, int(out[engine].shape[1] * factor))
            spx = sw - 1 - spx
        pivots[engine] = [spx, spy]

    parts = list(out)
    cfg = {"parts": {p: [0, 0, 0, 0] for p in parts},
           "rot": {p: float(rot_cfg.get(p, 0.0)) for p in parts},
           "mirror": {p: False for p in parts},
           "draw": {"body": 3, "head": 2, "right_hip": 1, "right_knee": 3,
                    "left_hip": 1, "left_knee": 3, "right_elbow": 4,
                    "right_wrist": 4, "left_elbow": 1, "left_wrist": 2},
           "pivot": {p: pivots[p] for p in pivots},
           "scene_mirror": False}
    for p, pv in pivots.items():
        cfg["parts"][p][0], cfg["parts"][p][1] = pv[0], pv[1]
        print("枢轴", p, pv)
    with open(os.path.join(dst, "adjust.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print("已生成素材目录:", dst)
    return 0

if __name__ == "__main__":
    sys.exit(main())
