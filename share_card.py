# -*- coding: utf-8 -*-
"""
分享卡片系统（仅在剧本模式下由关键词触发）
- 从用户当前句抽取"学习点/主题"，结合知识库检索与大模型生成：
    * 知识卡片：标题 / 副标题 / 要点 / 品牌署名（合成到预制底图上）
    * 分享文案：正文 + 话题标签
- 预制底图放在 card_templates/（PNG/JPG）；为空时程序生成默认底图兜底
- 成品卡片保存到 share_cards/
- 严格保证六项内容齐全：标题、副标题、要点、品牌署名、分享文案、话题标签
"""

import json
import re
import random
import hashlib
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import pygame
    import pygame.freetype
    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    import chatchat_config as config
except Exception:  # noqa: BLE001
    config = None

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    import qrcode
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


BASE_DIR = Path(__file__).resolve().parent
FONT_PATH = str(BASE_DIR / "font.ttf")
TEMPLATES_DIR = BASE_DIR / "card_templates"
CARDS_DIR = BASE_DIR / "share_cards"
BRAND = "海宁皮影戏 · 海宁小状元"

# [分享] 云上传配置（demo：HTTP + IP + 端口）
CARD_UPLOAD_URL = "http://121.40.76.121:8085/upload"
CARD_UPLOAD_TOKEN = "haining-demo-token"
CARD_UPLOAD_TIMEOUT = 5

# 成品卡片保留上限（超出按修改时间删除最旧的，避免无限堆积）
MAX_KEEP_CARDS = 30

# 触发关键词（仅在剧本模式下判断当前这一句）
SHARE_KEYWORDS = [
    "分享文案", "生成分享", "分享卡片", "知识卡片",
    "发朋友圈", "发微博", "观后感", "分享",
]

# 默认布局：坐标为画布比例，size 为画布高度的比例
DEFAULT_LAYOUT = {
    "title":    {"x": 0.08, "y": 0.10, "w": 0.84, "h": 0.16, "size": 0.060, "color": [45, 30, 20]},
    "subtitle": {"x": 0.08, "y": 0.27, "w": 0.84, "h": 0.07, "size": 0.032, "color": [150, 80, 40]},
    "points":   {"x": 0.08, "y": 0.37, "w": 0.84, "h": 0.38, "size": 0.036, "color": [55, 40, 30]},
    "footer":   {"x": 0.08, "y": 0.82, "w": 0.84, "h": 0.13, "size": 0.026, "color": [110, 70, 40]},
}

DEFAULT_POINTS = [
    "海宁皮影戏自南宋传入，是江南皮影的代表，2006 年入选首批国家级非遗",
    "影偶少雕镂、重彩绘，唱白用海宁方言，声腔融弋阳腔与海宁小调",
    "一方幕布、一盏灯，就是一部活着的光影史",
]


@dataclass
class ShareResult:
    topic: str
    title: str
    subtitle: str
    points: List[str] = field(default_factory=list)
    copy: str = ""
    hashtags: List[str] = field(default_factory=list)
    grounded: bool = False


class ShareCardSystem:
    def __init__(self, action_queue, engine=None, llm_client=None):
        self.action_queue = action_queue
        self.engine = engine
        self.llm_client = llm_client
        self.templates_dir = TEMPLATES_DIR
        self.cards_dir = CARDS_DIR
        self.templates_dir.mkdir(exist_ok=True)
        self.cards_dir.mkdir(exist_ok=True)
        self.layout = self._load_layout()
        self._font_cache: Dict[int, Any] = {}
        self.pending_result: Optional[ShareResult] = None
        self.current_card = None
        self.current_card_path: Optional[Path] = None
        self.card_url: Optional[str] = None
        self._lock = threading.Lock()

    # ==================== 触发与主题 ====================
    def is_share_intent(self, text: str) -> bool:
        if not text:
            return False
        return any(kw in text for kw in SHARE_KEYWORDS)

    def extract_topic(self, text: str, script=None) -> str:
        t = text.strip()
        for kw in ["帮我生成一个", "帮我生成", "生成一个", "生成", "分享文案",
                   "分享卡片", "知识卡片", "发朋友圈", "发微博", "观后感", "分享"]:
            t = t.replace(kw, " ")
        t = re.sub(r"[，。,.!！?？、；;：:\s]+", " ", t).strip()

        m = re.search(r"(?:今天)?(?:我)?(?:学到了|了解了|知道了|学会了|见识了|get到了|读了)\s*(.+)", t)
        if m and m.group(1).strip():
            return m.group(1).strip()

        if t and len(t) <= 30:
            return t

        if script is not None and getattr(script, "title", None):
            return str(script.title)
        return "海宁皮影戏"

    # ==================== 生成 ====================
    def handle(self, text: str, script=None) -> None:
        """在后台线程调用：生成数据并把文案入队，卡片渲染交由主线程。"""
        topic = self.extract_topic(text, script)
        evidence = self._retrieve_evidence(topic)
        data = self._llm_generate(topic, text, script, evidence)
        if data:
            result = self._normalize(data, topic, evidence, grounded=True)
        else:
            result = self._fallback_result(topic, evidence, script)

        print(f"[分享] 主题='{topic}' 要点={len(result.points)} 条，已生成分享文案")
        self.action_queue.put((["idle"], result.copy))
        with self._lock:
            self.pending_result = result

    def _retrieve_evidence(self, topic: str) -> List[str]:
        if not self.engine or not topic:
            return []
        try:
            retrieval = self.engine.retrieve(topic)
            evidence: List[str] = []
            auth = getattr(retrieval, "authoritative_answer", "") or ""
            if auth.strip():
                evidence.append(auth.strip())
            for c in retrieval.contexts[:4]:
                if c and c.strip() and c.strip() not in evidence:
                    evidence.append(c.strip())
            return evidence
        except Exception as e:  # noqa: BLE001
            print(f"[分享] 知识库检索失败: {e}")
            return []

    def _script_context(self, script) -> str:
        if script is None:
            return ""
        parts = [f"剧目：{getattr(script, 'title', '')}", f"主题：{getattr(script, 'theme', '')}"]
        for sc in getattr(script, "scenes", [])[:3]:
            parts.append(f"第{getattr(sc, 'scene_id', '')}场 {getattr(sc, 'title', '')}：{getattr(sc, 'description', '')}")
            for ln in getattr(sc, "lines", [])[:2]:
                parts.append(f"  {getattr(ln, 'speaker', '')}：{getattr(ln, 'text', '')}")
        return "\n".join(parts)

    def _llm_generate(self, topic: str, text: str, script, evidence: List[str]) -> Optional[Dict]:
        if not (self.llm_client and HAS_OPENAI):
            return None
        model = getattr(config, "ACTION_MODEL", "gemma3:4b") if config else "gemma3:4b"
        evidence_text = "\n".join(f"- {e}" for e in evidence) or "（无）"
        script_text = self._script_context(script) or "（无）"

        sys_prompt = (
            "你是海宁皮影戏数字人的社媒运营助手。"
            "根据主题、用户原话、剧本资料与知识库证据，生成一张知识卡片和一条分享文案。"
            "要求：严格依据给定资料，禁止编造；语言自然、有感染力；只输出 JSON。"
        )
        user_content = f"""主题：{topic}
用户原话：{text}
剧本资料：
{script_text}

知识库证据：
{evidence_text}

请输出如下 JSON（字段必须齐全）：
{{
  "title": "知识卡片主标题（一句话，突出'学到了什么'，如：今天，我读懂了{topic}）",
  "subtitle": "副标题（短语，概括看点）",
  "points": ["要点1", "要点2", "要点3", "要点4"],
  "copy": "分享文案正文，150-250字，第一人称'今天学到了……'口吻，可含 emoji 与换行",
  "hashtags": ["#海宁皮影戏", "#非遗", "#传统文化"]
}}
只输出 JSON，不要任何解释。"""

        try:
            resp = self.llm_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.6,
            )
            content = (resp.choices[0].message.content or "").strip()
            return self._parse_json(content)
        except Exception as e:  # noqa: BLE001
            print(f"[分享] 大模型生成失败，改用模板兜底: {e}")
            return None

    def _parse_json(self, content: str) -> Optional[Dict]:
        if not content:
            return None
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            return None

    def _fallback_result(self, topic: str, evidence: List[str], script) -> ShareResult:
        data: Dict[str, Any] = {
            "title": f"今天，我了解了{topic}",
            "subtitle": "海宁皮影戏 · 国家级非遗",
            "points": [],
            "copy": "",
            "hashtags": ["#海宁皮影戏", "#非遗", "#传统文化"],
        }
        for ctx in evidence:
            sent = self._first_sentence(ctx)
            if sent and self._usable_point(sent) and sent not in data["points"]:
                data["points"].append(sent)
            if len(data["points"]) >= 4:
                break
        return self._normalize(data, topic, evidence, grounded=bool(evidence))

    def _normalize(self, data: Dict, topic: str, evidence: List[str], grounded: bool) -> ShareResult:
        data = data or {}
        title = str(data.get("title") or "").strip() or f"今天，我了解了{topic}"
        subtitle = str(data.get("subtitle") or "").strip() or "海宁皮影戏 · 国家级非遗"

        points = [str(p).strip() for p in (data.get("points") or []) if str(p).strip()]
        if len(points) < 3:
            for ctx in evidence:
                sent = self._first_sentence(ctx)
                if sent and self._usable_point(sent) and sent not in points:
                    points.append(sent)
                if len(points) >= 3:
                    break
        if len(points) < 3:
            for d in DEFAULT_POINTS:
                if d not in points:
                    points.append(d)
                if len(points) >= 3:
                    break
        points = points[:5]

        copy = str(data.get("copy") or "").strip()
        if not copy:
            joined = "；".join(p.rstrip("。；;.") for p in points)
            copy = (f"今天学到了{topic}📜 {joined}。"
                    "一方幕布、一盏灯，就是一部活着的光影史。\n#海宁皮影戏 #非遗")

        hashtags = [str(h).strip() for h in (data.get("hashtags") or []) if str(h).strip()]
        if not hashtags:
            hashtags = ["#海宁皮影戏", "#非遗", "#传统文化"]
        hashtags = [h if h.startswith("#") else f"#{h}" for h in hashtags]

        return ShareResult(
            topic=topic, title=title, subtitle=subtitle,
            points=points, copy=copy, hashtags=hashtags, grounded=grounded,
        )

    # ==================== 卡片渲染（主线程） ====================
    def poll_card(self):
        """主线程调用：若有待渲染结果则渲染并保存，返回当前弹层 Surface 或 None。"""
        with self._lock:
            result = self.pending_result
            self.pending_result = None
        if result is not None:
            try:
                surf = self.build_card_surface(result)
                self.current_card_path = self._save_card(surf, result)
                self.current_card = surf
                print(f"[分享] 知识卡片已保存: {self.current_card_path}")
                threading.Thread(
                    target=self._upload_async,
                    args=(surf, self.current_card_path),
                    daemon=True,
                ).start()
            except Exception as e:  # noqa: BLE001
                print(f"[分享] 卡片渲染失败: {e}")
        return self.current_card

    def _upload_async(self, surf, path: Path) -> None:
        url = self._upload_file(path)
        if not url or not HAS_QRCODE:
            return
        try:
            qr = self._make_qr_surface(url)
            if qr is None:
                return
            new_surf = surf.copy()
            self._stamp_qr(new_surf, qr)
            with self._lock:
                if self.current_card is surf:
                    self.current_card = new_surf
                    self.card_url = url
            print(f"[分享] 二维码已合成: {url}")
        except Exception as e:  # noqa: BLE001
            print(f"[分享] 二维码合成失败: {e}")

    def _upload_file(self, path: Path) -> Optional[str]:
        if not HAS_HTTPX:
            print("[分享] 未安装 httpx，跳过上传")
            return None
        try:
            data = Path(path).read_bytes()
            resp = httpx.post(
                CARD_UPLOAD_URL,
                params={"name": Path(path).name},
                headers={"X-Upload-Token": CARD_UPLOAD_TOKEN},
                content=data,
                timeout=CARD_UPLOAD_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json().get("url")
        except Exception as e:  # noqa: BLE001
            print(f"[分享] 上传失败，跳过二维码: {e}")
            return None

    def _make_qr_surface(self, url: str, pixels: int = 6):
        qr = qrcode.QRCode(border=2)
        qr.add_data(url)
        qr.make(fit=True)
        mat = qr.get_matrix()
        n = len(mat)
        surf = pygame.Surface((n * pixels, n * pixels))
        surf.fill((255, 255, 255))
        for y, row in enumerate(mat):
            for x, cell in enumerate(row):
                if cell:
                    pygame.draw.rect(surf, (0, 0, 0), (x * pixels, y * pixels, pixels, pixels))
        return surf

    def _stamp_qr(self, card_surf, qr_surf, margin: int = 24) -> None:
        W, H = card_surf.get_size()
        target = max(120, int(W * 0.22))
        qr_surf = pygame.transform.smoothscale(qr_surf, (target, target))
        pad = 10
        x = W - target - margin
        y = H - target - margin
        pygame.draw.rect(card_surf, (255, 255, 255),
                         (x - pad, y - pad, target + 2 * pad, target + 2 * pad))
        card_surf.blit(qr_surf, (x, y))
        font = self._get_font(max(14, int(W * 0.020)))
        font.render_to(card_surf, (x - pad, y - pad - int(W * 0.028)), "扫码保存卡片", (60, 40, 30))

    def is_card_visible(self) -> bool:
        return self.current_card is not None

    def close_card(self) -> None:
        self.current_card = None
        self.card_url = None

    def _load_template_paths(self) -> List[Path]:
        """收集可用底图：card_templates/ 内的图片 + templates.json 里引用的外部图片。

        用 templates.json 引用已有大图（如 workbackground/），避免重复拷贝占空间。
        """
        if not self.templates_dir.exists():
            return []
        files = (sorted(self.templates_dir.glob("*.png"))
                 + sorted(self.templates_dir.glob("*.jpg"))
                 + sorted(self.templates_dir.glob("*.jpeg")))
        manifest = self.templates_dir / "templates.json"
        if manifest.exists():
            try:
                with open(manifest, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for rel in data.get("extra", []):
                    p = (BASE_DIR / rel).resolve()
                    if p.exists() and p not in files:
                        files.append(p)
                    elif not p.exists():
                        print(f"[分享] templates.json 引用的底图不存在: {rel}")
            except Exception as e:  # noqa: BLE001
                print(f"[分享] templates.json 解析失败: {e}")
        return files

    def pick_template(self, topic: str = "") -> Optional[Path]:
        files = self._load_template_paths()
        if not files:
            return None
        topic_l = (topic or "").lower()
        for p in files:
            if p.stem.lower() and p.stem.lower() in topic_l:
                return p
        return random.choice(files)

    def build_card_surface(self, result: ShareResult):
        if not HAS_PYGAME:
            raise RuntimeError("pygame 不可用")
        if not pygame.get_init():
            pygame.init()

        template = self.pick_template(result.topic)
        base = None
        if template is not None:
            try:
                base = pygame.image.load(str(template)).convert_alpha()
            except Exception as e:  # noqa: BLE001
                print(f"[分享] 模板加载失败 {template}: {e}")
                base = None
        if base is None:
            base = self._make_default_template()

        w, h = base.get_size()
        layout = self._load_layout(template) if template is not None else self.layout
        self._draw_zone(base, result.title, layout["title"], w, h)
        self._draw_zone(base, result.subtitle, layout["subtitle"], w, h)
        self._draw_points(base, result.points, layout["points"], w, h)
        footer = " ".join(result.hashtags) + "\n" + BRAND
        self._draw_zone(base, footer, layout["footer"], w, h, line_gap=1.4)
        return base

    def _make_default_template(self, w: int = 1080, h: int = 1440):
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        top, bottom = (252, 245, 232), (238, 218, 192)
        for yy in range(h):
            t = yy / max(1, h - 1)
            col = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
            pygame.draw.line(surf, col, (0, yy), (w, yy))
        pygame.draw.rect(surf, (200, 160, 110), (0, 0, w, h), 10)
        pygame.draw.rect(surf, (180, 30, 30), (0, 0, w, int(h * 0.055)))
        return surf

    def _get_font(self, size: int):
        size = max(12, int(size))
        if size in self._font_cache:
            return self._font_cache[size]
        font = None
        try:
            font = pygame.freetype.Font(FONT_PATH, size)
        except Exception:  # noqa: BLE001
            try:
                font = pygame.freetype.SysFont("simhei", size)
            except Exception:  # noqa: BLE001
                font = pygame.freetype.SysFont(None, size)
        self._font_cache[size] = font
        return font

    def _wrap(self, font, text: str, max_w: int) -> List[str]:
        lines: List[str] = []
        cur = ""
        for ch in str(text):
            if ch == "\n":
                lines.append(cur)
                cur = ""
                continue
            if not cur:
                cur = ch
                continue
            if font.get_rect(cur + ch).width <= max_w:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        if cur or not lines:
            lines.append(cur)
        return lines

    def _draw_zone(self, surf, text: str, zone: Dict, W: int, H: int, line_gap: float = 1.25):
        x = int(zone["x"] * W)
        y = int(zone["y"] * H)
        maxw = max(20, int(zone["w"] * W))
        size = max(14, int(zone["size"] * H))
        color = tuple(zone.get("color", [45, 30, 20]))
        font = self._get_font(size)
        lines = self._wrap(font, text, maxw)
        line_h = font.get_sized_height() * line_gap
        max_lines = max(1, int((zone["h"] * H) // max(1.0, line_h)))
        for i, line in enumerate(lines[:max_lines]):
            font.render_to(surf, (x, int(y + i * line_h)), line, color)

    def _draw_points(self, surf, points: List[str], zone: Dict, W: int, H: int):
        x = int(zone["x"] * W)
        y = int(zone["y"] * H)
        maxw = max(20, int(zone["w"] * W))
        size = max(14, int(zone["size"] * H))
        color = tuple(zone.get("color", [55, 40, 30]))
        font = self._get_font(size)
        line_h = font.get_sized_height() * 1.35
        bullet_w = font.get_rect("· ").width
        bottom = y + int(zone["h"] * H)
        cy = y
        for p in points:
            lines = self._wrap(font, p, maxw - bullet_w)
            for i, line in enumerate(lines):
                if cy + line_h > bottom:
                    return
                prefix = "· " if i == 0 else "  "
                font.render_to(surf, (x, int(cy)), prefix, color)
                font.render_to(surf, (x + bullet_w, int(cy)), line, color)
                cy += line_h
            cy += line_h * 0.25

    def _save_card(self, surf, result: ShareResult) -> Path:
        """按内容哈希命名，重复内容覆盖同一文件；并裁剪历史成品数量。"""
        self.cards_dir.mkdir(exist_ok=True)
        safe = re.sub(r'[<>:"/\\|?*\s]+', "_", result.topic or "share")[:30] or "share"
        key = "|".join([result.title, result.subtitle, result.copy,
                        "；".join(result.points), " ".join(result.hashtags)])
        digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:8]
        path = self.cards_dir / f"分享_{safe}_{digest}.png"
        pygame.image.save(surf, str(path))
        self._prune_cards()
        return path

    def _prune_cards(self) -> None:
        """只保留最近 MAX_KEEP_CARDS 张成品卡片，避免磁盘无限增长。"""
        try:
            files = sorted(self.cards_dir.glob("分享_*.png"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[MAX_KEEP_CARDS:]:
                try:
                    old.unlink()
                except OSError:
                    pass
        except Exception:  # noqa: BLE001
            pass

    # ==================== 工具 ====================
    def _first_sentence(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text or "").strip()
        if not text:
            return ""
        for sep in ["。", "！", "？", "；", ".", "!", "?"]:
            idx = text.find(sep)
            if 0 < idx <= 60:
                return text[:idx + 1].strip()
        return text[:60].strip()

    def _usable_point(self, sent: str) -> bool:
        """过滤掉问句/标准问题式证据，保证要点是陈述性知识。"""
        s = (sent or "").strip()
        if len(s) < 8:
            return False
        if s.endswith("？") or s.endswith("?"):
            return False
        if "标准问题" in s or s.startswith("问题"):
            return False
        if s[:2] in ("如何", "什么", "怎么", "为何", "哪些", "哪一", "是否", "能否"):
            return False
        return True

    def _load_layout(self, template_path: Optional[Path] = None) -> Dict:
        layout = {k: dict(v) for k, v in DEFAULT_LAYOUT.items()}
        candidates = [self.templates_dir / "layout.json"]
        if template_path is not None:
            candidates.append(self.templates_dir / f"{template_path.stem}.layout.json")
        for cfg_path in candidates:
            if not cfg_path.exists():
                continue
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    override = json.load(f)
                for key, val in override.items():
                    if key in layout and isinstance(val, dict):
                        layout[key].update(val)
                print(f"[分享] 已加载卡片布局: {cfg_path.name}")
            except Exception as e:  # noqa: BLE001
                print(f"[分享] 布局文件解析失败，使用默认布局: {e}")
        return layout


def create_share_system(action_queue, engine=None, llm_client=None) -> ShareCardSystem:
    return ShareCardSystem(action_queue, engine=engine, llm_client=llm_client)
