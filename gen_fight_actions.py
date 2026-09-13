# -*- coding: utf-8 -*-
"""
《孙悟空·打戏六动作》HTML 预览生成脚本。
模仿 actions/scratch.json 的数据格式与基准站姿：
- 读取 scratch.json 第 0 帧 joints 作为基础站姿；
- 通过「绕肩/绕髋旋转 + 躯干/骨盆/头/踝平移」的关键帧插值，生成六个打戏动作：
  出拳 punch / 踢腿 kick / 格挡 block / 前冲 lunge / 后撤 retreat / 受击 hit；
- 每个动作 90 帧 / 30fps / 1280x720，与现有动作文件同构；
- 输出单个 HTML 播放器（内嵌全部数据，可切换动作）。
"""
import json
import math
import os

BASE_JSON = os.path.join("actions", "scratch.json")
OUT_HTML = "fight_actions_preview.html"
FPS = 30.0
FRAMES = 90
W, H = 1280, 720


def rot_around(ax, ay, px, py, rad):
    c, s = math.cos(rad), math.sin(rad)
    dx, dy = px - ax, py - ay
    return ax + dx * c - dy * s, ay + dx * s + dy * c


def smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def lerp(a, b, t):
    if isinstance(a, tuple) and isinstance(b, tuple):
        return tuple(lerp(x, y, t) for x, y in zip(a, b))
    return a + (b - a) * t


def load_base():
    with open(BASE_JSON, "r", encoding="utf-8") as f:
        return dict(json.load(f)["frames"][0]["joints"])


def interpolate(keys, u):
    """keys: [(t, value), ...] 升序；value 为 float（旋转角）或 dict（位移组）。
    返回 u 处的插值结果。"""

    def zero_like(v):
        return tuple(0.0 for _ in v) if isinstance(v, tuple) else 0.0

    if u <= keys[0][0]:
        return keys[0][1]
    for i in range(len(keys) - 1):
        t0, v0 = keys[i]
        t1, v1 = keys[i + 1]
        if u <= t1:
            s = smooth((u - t0) / (t1 - t0))
            if isinstance(v0, dict) or isinstance(v1, dict):
                v0d, v1d = (v0 if isinstance(v0, dict) else {}), (v1 if isinstance(v1, dict) else {})
                return {k: lerp(v0d.get(k, zero_like(v1d[k]) if k in v1d else 0.0),
                                v1d.get(k, zero_like(v0d[k]) if k in v0d else 0.0), s)
                        for k in set(v0d) | set(v1d)}
            return lerp(v0, v1, s)
    return keys[-1][1]


def build_action(name, base, arm_keys, leg_keys, move_keys):
    """arm_keys/leg_keys: {side: [(t, rad), ...]}；move_keys: [(t, move), ...]
    move: {group: dxdy}，group ∈ thorax/pelvis/head/ankleL/ankleR/hipL/hipR"""
    frames = []
    for i in range(FRAMES):
        u = i / float(FRAMES)
        joints = {}
        for k, p in base.items():
            joints[k] = {"x": p["x"], "y": p["y"], "confidence": 1.0}

        # 手臂绕肩旋转
        for side, keys in arm_keys.items():
            rad = interpolate(keys, u)
            if abs(rad) < 1e-9:
                continue
            sh = base[f"{side}_shoulder"]
            for jn in (f"{side}_elbow", f"{side}_wrist"):
                p = base[jn]
                x, y = rot_around(sh["x"], sh["y"], p["x"], p["y"], rad)
                joints[jn]["x"], joints[jn]["y"] = x, y

        # 腿绕髋旋转（保持大腿/小腿长度）
        for side, keys in leg_keys.items():
            rad = interpolate(keys, u)
            if abs(rad) < 1e-9:
                continue
            hip = base[f"{side}_hip"]
            for jn in (f"{side}_knee", f"{side}_ankle"):
                p = base[jn]
                x, y = rot_around(hip["x"], hip["y"], p["x"], p["y"], rad)
                joints[jn]["x"], joints[jn]["y"] = x, y

        # 躯干/骨盆/头/踝平移
        mv = interpolate(move_keys, u)
        groups = {
            "thorax": ("thorax", "left_shoulder", "right_shoulder"),
            "pelvis": ("pelvis",),
            "head": ("nose", "upper_neck", "head_top"),
            "ankleL": ("left_ankle",),
            "ankleR": ("right_ankle",),
            "kneeL": ("left_knee",),
            "kneeR": ("right_knee",),
            "hipL": ("left_hip",),
            "hipR": ("right_hip",),
            "elbowL": ("left_elbow",),
            "elbowR": ("right_elbow",),
            "wristL": ("left_wrist",),
            "wristR": ("right_wrist",),
        }
        for g, joints_list in groups.items():
            if g in mv:
                dx, dy = mv[g]
                for jn in joints_list:
                    if jn in joints:
                        joints[jn]["x"] += dx
                        joints[jn]["y"] += dy

        # 骨盆平移也带动双髋（保持髋-膝-踝链条自然）
        if "pelvis" in mv:
            dx, dy = mv["pelvis"]
            for jn in ("left_hip", "right_hip"):
                joints[jn]["x"] += dx
                joints[jn]["y"] += dy

        frames.append({
            "frame_number": i,
            "timestamp": round(i / FPS, 4),
            "joints": joints,
        })
    return {"video_info": {"fps": FPS, "total_frames": FRAMES,
                           "resolution": [W, H]}, "frames": frames}


def build_actions(base):
    A = {}

    # 1) 出拳 punch：右手收拳蓄力 -> 前伸打出 -> 收回
    A["punch"] = build_action(
        "punch", base,
        arm_keys={"right": [(0.0, 0.0), (0.30, 0.75), (0.42, -1.2), (0.50, -1.35),
                            (0.65, -0.2), (1.0, 0.0)],
                  "left": [(0.0, 0.0), (0.35, -0.35), (0.50, -0.25), (1.0, 0.0)]},
        leg_keys={},
        move_keys=[(0.0, {}),
                   (0.30, {"thorax": (6, -6)}),
                   (0.50, {"thorax": (24, 10), "pelvis": (9, 3)}),
                   (0.65, {"thorax": (6, -2)}),
                   (1.0, {})],
    )

    # 2) 踢腿 kick：提膝更高、蹬出更有劲、收回放缓更从容（左臂后摆助势，支撑腿微屈）
    A["kick"] = build_action(
        "kick", base,
        arm_keys={"left": [(0.0, 0.0), (0.30, -0.6), (0.42, -0.3), (1.0, 0.0)]},
        leg_keys={"right": [(0.0, 0.0), (0.22, 1.2), (0.34, 1.75), (0.55, 0.9), (0.72, 0.0), (1.0, 0.0)]},
        move_keys=[(0.0, {}),
                   (0.22, {"thorax": (-6, -10), "head": (0, -4),
                           "pelvis": (0, 3), "kneeL": (1, 4)}),
                   (0.34, {"thorax": (-12, -16), "head": (-3, -8),
                           "pelvis": (0, 5), "kneeL": (1, 5)}),
                   (0.46, {"thorax": (-5, -5), "pelvis": (0, 3), "kneeL": (0, 2)}),
                   (1.0, {})],
    )

    # 3) 格挡 block：双臂自然交叉抱架于胸前（肘外展抬起、左臂略高于右臂）、重心放低、两脚分开、头略低
    A["block"] = build_action(
        "block", base,
        arm_keys={},
        leg_keys={},
        move_keys=[(0.0, {}),
                   (0.35, {"wristR": (11, -112), "elbowR": (16, -20),
                           "wristL": (-73, -112), "elbowL": (-14, -25),
                           "pelvis": (0, 14), "thorax": (0, 8), "head": (0, 8),
                           "ankleL": (-10, 0), "ankleR": (10, 0),
                           "kneeL": (-5, 8), "kneeR": (5, 8)}),
                   (0.7, {"wristR": (11, -112), "elbowR": (16, -20),
                          "wristL": (-73, -112), "elbowL": (-14, -25),
                          "pelvis": (0, 14), "thorax": (0, 8), "head": (0, 8),
                          "ankleL": (-10, 0), "ankleR": (10, 0),
                          "kneeL": (-5, 8), "kneeR": (5, 8)}),
                   (1.0, {})],
    )

    # 4) 前冲 lunge：身体前倾、弓步快速迈出、双膝弯曲、手臂前探持棒前指
    A["lunge"] = build_action(
        "lunge", base,
        arm_keys={"right": [(0.0, 0.0), (0.35, 0.5), (0.55, -1.0), (0.75, -0.8), (1.0, 0.0)],
                  "left": [(0.0, 0.0), (0.35, 0.2), (0.55, -0.35), (0.75, -0.3), (1.0, 0.0)]},
        leg_keys={},
        move_keys=[(0.0, {}),
                   (0.35, {"thorax": (10, -10), "pelvis": (6, -4),
                           "ankleR": (6, 0), "ankleL": (-6, 0)}),
                   (0.55, {"thorax": (32, 14), "pelvis": (30, 10),
                           "ankleR": (46, 0), "ankleL": (14, 0),
                           "kneeR": (4, 18), "kneeL": (0, 14), "head": (8, 2)}),
                   (0.75, {"thorax": (26, 16), "pelvis": (24, 12),
                           "ankleR": (50, 0), "ankleL": (16, 0),
                           "kneeR": (3, 16), "kneeL": (0, 12)}),
                   (1.0, {})],
    )

    # 5) 后撤 retreat：上身保持警惕、前脚向后蹬地、重心后移、后脚撑地、微微躬身
    A["retreat"] = build_action(
        "retreat", base,
        arm_keys={"right": [(0.0, 0.0), (0.4, 0.35), (0.75, 0.35), (1.0, 0.0)],
                  "left": [(0.0, 0.0), (0.4, -0.35), (0.75, -0.35), (1.0, 0.0)]},
        leg_keys={},
        move_keys=[(0.0, {}),
                   (0.4, {"pelvis": (-26, 4), "thorax": (-18, 6),
                          "ankleL": (-34, 0), "ankleR": (-36, 0), "head": (-2, -2)}),
                   (0.75, {"pelvis": (-30, 6), "thorax": (-22, 8),
                           "ankleL": (-38, 0), "ankleR": (-40, 0), "head": (-4, -3)}),
                   (1.0, {})],
    )

    # 6) 受击 hit：躯干后仰、双臂张开、头向后甩、一脚后撤踉跄、重心不稳
    A["hit"] = build_action(
        "hit", base,
        arm_keys={"right": [(0.0, 0.0), (0.3, 2.0), (0.5, 1.35), (1.0, 0.0)],
                  "left": [(0.0, 0.0), (0.3, -2.0), (0.5, -1.35), (1.0, 0.0)]},
        leg_keys={},
        move_keys=[(0.0, {}),
                   (0.3, {"thorax": (-14, -30), "head": (-10, -36), "pelvis": (-6, 16),
                          "ankleR": (-34, 14), "ankleL": (8, 6)}),
                   (0.5, {"thorax": (-8, -16), "head": (-6, -20), "pelvis": (-3, 10),
                          "ankleR": (-20, 10), "ankleL": (4, 4)}),
                   (1.0, {})],
    )
    return A


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>孙悟空 · 打戏六动作 - 骨架动画预览</title>
<style>
  body{font-family:system-ui,sans-serif;margin:0;background:#0f1720;color:#e5e7eb;
       display:flex;flex-direction:column;align-items:center;min-height:100vh}
  h1{font-size:18px;margin:14px 0 2px;color:#fbbf24;letter-spacing:1px}
  .sub{font-size:12px;color:#9ca3af;margin-bottom:10px}
  .tabs{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin-bottom:10px}
  .tab{background:#1e293b;color:#e5e7eb;border:1px solid #334155;padding:7px 14px;
       border-radius:6px;cursor:pointer;font-size:13px;font-weight:600}
  .tab.active{background:#fbbf24;color:#111;border-color:#fbbf24}
  .stage{background:#000;border-radius:10px;box-shadow:0 8px 30px rgba(0,0,0,.5)}
  canvas{display:block;border-radius:10px;background:#11161f;max-width:92vw}
  .controls{display:flex;gap:10px;align-items:center;margin:12px 0;flex-wrap:wrap;justify-content:center}
  button{background:#fbbf24;color:#111;border:none;padding:7px 16px;border-radius:6px;
         font-weight:600;cursor:pointer;font-size:13px}
  button:hover{background:#fcd34d}
  input[type=range]{width:280px;accent-color:#fbbf24}
  .meta{font-size:12px;color:#9ca3af}
  .legend{display:flex;gap:14px;font-size:12px;margin-bottom:6px}
  .line{width:16px;height:3px;display:inline-block;vertical-align:middle;margin-right:4px}
  .names{font-size:13px;color:#fbbf24;font-weight:600;min-height:18px}
</style>
</head>
<body>
<h1>🐒 孙悟空 · 打戏六动作</h1>
<div class="sub">参照 actions/scratch.json 骨架格式 · 关键帧插值生成</div>
<div class="names" id="actName">出拳 punch</div>
<div class="tabs" id="tabs"></div>
<div class="legend">
  <span><span class="line" style="background:#fbbf24"></span>骨骼</span>
  <span><span class="line" style="background:#f87171"></span>关节点</span>
</div>
<div class="stage"><canvas id="cv" width="960" height="540"></canvas></div>
<div class="controls">
  <button id="play"><span id="playIcon">❚❚</span> <span id="playLb">暂停</span></button>
  <input type="range" id="scrub" min="0" max="89" value="0">
  <input type="range" id="speed" min="10" max="200" value="100" title="速度%">
  <span class="meta" id="frameLb">帧 0/90</span>
</div>
<div class="meta" id="info"></div>
<script>
const DATA = __DATA__;
const ACT_NAMES = {punch:"出拳 punch",kick:"踢腿 kick",block:"格挡 block",
                   lunge:"前冲 lunge",retreat:"后撤 retreat",hit:"受击 hit"};
const conns = [["upper_neck","pelvis"],["head_top","upper_neck"],
  ["right_shoulder","right_elbow"],["right_elbow","right_wrist"],
  ["left_shoulder","left_elbow"],["left_elbow","left_wrist"],
  ["right_hip","right_knee"],["right_knee","right_ankle"],
  ["left_hip","left_knee"],["left_knee","left_ankle"],
  ["pelvis","left_hip"],["pelvis","right_hip"],
  ["left_shoulder","right_shoulder"]];
const cv=document.getElementById("cv"), ctx=cv.getContext("2d");
const keys=Object.keys(DATA);
let cur=keys[0], idx=0, playing=true, speed=100, last=0;
function jnt(j){const s=Math.min(cv.width/DATA[cur].video_info.resolution[0],
                               cv.height/DATA[cur].video_info.resolution[1]);
  return {x:j.x*s, y:j.y*s};}
function draw(){
  const fr=DATA[cur].frames[Math.min(idx,89)], j=fr.joints;
  ctx.clearRect(0,0,cv.width,cv.height);
  const gy=jnt(j.pelvis).y;
  ctx.strokeStyle="rgba(255,255,255,.08)";ctx.beginPath();
  ctx.moveTo(0,gy);ctx.lineTo(cv.width,gy);ctx.stroke();
  ctx.lineWidth=6;ctx.lineCap="round";
  for(const [a,b] of conns){if(!j[a]||!j[b])continue;
    const p1=jnt(j[a]),p2=jnt(j[b]);
    ctx.strokeStyle="#fbbf24";ctx.beginPath();ctx.moveTo(p1.x,p1.y);ctx.lineTo(p2.x,p2.y);ctx.stroke();}
  for(const k in j){const p=jnt(j[k]);
    ctx.fillStyle="#f87171";ctx.beginPath();ctx.arc(p.x,p.y,6,0,7);ctx.fill();}
  document.getElementById("frameLb").textContent="帧 "+idx+"/"+framesOf();
  document.getElementById("scrub").value=idx;
  document.getElementById("info").textContent=
    "fps="+DATA[cur].video_info.fps.toFixed(1)+"  帧数="+DATA[cur].video_info.total_frames+
    "  分辨率="+DATA[cur].video_info.resolution.join("x");
}
function framesOf(){return DATA[cur].video_info.total_frames;}
function tick(ts){
  if(playing){
    const total=framesOf();
    const step=Math.max(1,Math.round(1000/DATA[cur].video_info.fps*(100/speed)));
    if(ts-last>=step){idx=(idx+1)%total; last=ts;}
    else {setTimeout(t=>tick(performance.now()),Math.max(1,step-(ts-last)));return;}
  }
  draw(); requestAnimationFrame(tick);
}
// 标签页
const tabs=document.getElementById("tabs");
keys.forEach(k=>{const b=document.createElement("button");
  b.className="tab"+(k===cur?" active":""); b.textContent=ACT_NAMES[k]||k;
  b.onclick=()=>{cur=k; idx=0;
    document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
    b.classList.add("active");
    document.getElementById("actName").textContent=ACT_NAMES[k]||k;
    document.getElementById("scrub").max=framesOf()-1;
    draw();};
  tabs.appendChild(b);});
const playBtn=document.getElementById("play");
playBtn.onclick=()=>{playing=!playing;
  document.getElementById("playIcon").textContent=playing?"❚❚":"▶";
  document.getElementById("playLb").textContent=playing?"暂停":"播放";
  if(playing)last=performance.now();};
document.getElementById("scrub").oninput=e=>{idx=+e.target.value;draw();};
document.getElementById("speed").oninput=e=>speed=+e.target.value;
document.getElementById("scrub").max=framesOf()-1;
draw(); last=performance.now(); requestAnimationFrame(tick);
</script>
</body>
</html>
"""


def main():
    base = load_base()
    actions = build_actions(base)
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(actions, ensure_ascii=False))
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[OK] 生成 HTML: {OUT_HTML}")

    # 同时导出每个动作到 actions/*.json（与现有动作文件同构，供 main.py 引擎加载）
    for key, data in actions.items():
        p = os.path.join("actions", f"{key}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[OK] 导出 JSON: {p} ({len(data['frames'])}帧)")


if __name__ == "__main__":
    main()
