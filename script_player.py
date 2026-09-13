# -*- coding: utf-8 -*-
"""
剧本系统
- 播放：从 classic_plays.json 剧目库精确/主题调用
- 生成：剧目库未收录时，根据用户话语结合知识库与大模型生成新剧本
- 生成的剧本按内容哈希存档（重复内容覆盖），并限制存档数量
"""

import json
import time
import threading
import random
import re
import hashlib
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

try:
    from openai import OpenAI  # noqa: F401
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    import chatchat_config as config
except Exception:  # noqa: BLE001
    config = None

# 生成的剧本存档保留上限（超出按修改时间删除最旧的）
MAX_KEEP_SCRIPTS = 50


@dataclass
class ScriptLine:
    speaker: str
    text: str
    action: str = "idle"
    duration: float = 2.5
    emotion: str = "neutral"


@dataclass
class ScriptScene:
    scene_id: int
    title: str
    description: str
    lines: List[ScriptLine] = field(default_factory=list)
    background: str = ""


@dataclass
class PlayScript:
    title: str
    theme: str          # 用于分类
    scenes: List[ScriptScene]
    total_duration: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    source_type: str = "exact"  # 仅用于标记，现在固定为 "exact"

    def __post_init__(self):
        total = sum(line.duration for scene in self.scenes for line in scene.lines)
        self.total_duration = total


class ScriptGenerator:
    def get_play_names_by_theme(self) -> Dict[str, List[str]]:
        """返回按主题分组的剧名列表（仅包含有完整内容的剧目）"""
        return {theme: plays.copy() for theme, plays in self.theme_to_plays.items()}
    def __init__(self, action_map: Dict[str, str], fps: int = 45, engine=None, llm_client=None):
        self.action_map = action_map
        self.fps = fps
        self.engine = engine            # KnowledgeQAEngine，用于生成时检索证据
        self.llm_client = llm_client    # Ollama OpenAI 兼容客户端
        self.scripts_dir = Path("generated_scripts")  # 生成剧本的存档目录
        self.scripts_dir.mkdir(exist_ok=True)

        # 加载剧目库
        self.classic_plays = {}       # 剧名 -> 完整数据
        self.theme_to_plays = {}      # 主题 -> [剧名列表]（仅包含有内容的）
        self._load_classic_plays()

    def _load_classic_plays(self):
        json_path = Path("generated_scripts/classic_plays.json")
        json_path.parent.mkdir(parents=True, exist_ok=True)
        if not json_path.exists():
            print(f"[警告] 未找到剧目库文件: {json_path}，将使用空剧目库。")
            return
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # 新结构：data 是一个 {"plays": [...]}
            plays_list = data.get("plays", [])
            self.classic_plays = {}   # 清空后重新填充
            for item in plays_list:
                name = item.get("name")
                if not name:
                    continue
                self.classic_plays[name] = item   # 以 name 为键存储完整数据

            # 构建主题到剧名的映射（只包含有内容的）
            self.theme_to_plays = {}
            for name, info in self.classic_plays.items():
                if info.get("scenes") and len(info["scenes"]) > 0:
                    theme = info.get("theme", info.get("category", "通用"))
                    self.theme_to_plays.setdefault(theme, []).append(name)
            print(f"[剧目库] 加载成功，共 {len(self.classic_plays)} 个剧目，其中 {sum(len(v) for v in self.theme_to_plays.values())} 个有完整内容。")
        except Exception as e:
            print(f"[剧目库] 加载失败: {e}")

    def get_script(self, play_name: str) -> Tuple[Optional[PlayScript], str]:
        """
        根据输入获取剧本，返回 (剧本对象, 提示信息)
        策略：
          1. 精确匹配剧名，且 scenes 非空 -> 直接使用
          2. 精确匹配但 scenes 为空 -> 提示未收录（无详细内容）
          3. 输入为主题名（在 theme_to_plays 中） -> 从该主题下选一个完整剧目播放
          4. 否则 -> 未收录
        """
        play_name = play_name.strip()
        # 1. 精确匹配
        if play_name in self.classic_plays:
            data = self.classic_plays[play_name]
            if data.get("scenes") and len(data["scenes"]) > 0:
                script = self._parse_play_data(data, play_name)
                hint = f"为您表演经典剧目《{play_name}》"
                return script, hint
            else:
                return None, f"抱歉，暂未收录《{play_name}》的详细内容"

        # 2. 主题匹配（随机选取一个，并显示序号）
        if play_name in self.theme_to_plays:
            plays = self.theme_to_plays[play_name]
            if plays:
                # 排序以保证序号一致性
                sorted_plays = sorted(plays)
                chosen = random.choice(sorted_plays)
                index = sorted_plays.index(chosen) + 1  # 序号从1开始
                data = self.classic_plays[chosen]
                script = self._parse_play_data(data, chosen)
                hint = f"为您表演主题《{play_name}》下的剧目《{chosen}》（{play_name}{index}）"
                return script, hint
            else:
                return None, f"抱歉，主题《{play_name}》下暂无收录剧目"

        # 3. 未收录
        return None, f"抱歉，暂未收录《{play_name}》"

    def _parse_play_data(self, data: Dict, fallback_title: str = None) -> PlayScript:
        """将JSON数据转换为PlayScript对象"""
        title = data.get("title", fallback_title or "未命名剧目")
        theme = data.get("theme", data.get("category", "通用"))
        scenes = []
        for scene_data in data.get("scenes", []):
            lines = []
            for line_data in scene_data.get("lines", []):
                action = line_data.get("action", "idle")
                # 映射动作
                if action in self.action_map:
                    action = self.action_map[action]
                lines.append(ScriptLine(
                    speaker=line_data.get("speaker", "旁白"),
                    text=line_data.get("text", ""),
                    action=action,
                    duration=float(line_data.get("duration", 3.0)),
                    emotion=line_data.get("emotion", "neutral")
                ))
            scenes.append(ScriptScene(
                scene_id=scene_data.get("scene_id", len(scenes)+1),
                title=scene_data.get("title", f"场景{len(scenes)+1}"),
                description=scene_data.get("description", ""),
                lines=lines,
                background=scene_data.get("background", "")
            ))
        return PlayScript(
            title=title,
            theme=theme,
            scenes=scenes,
            source_type="exact"
        )

    def can_generate(self) -> bool:
        return bool(self.llm_client and HAS_OPENAI)

    def generate_from_prompt(self, prompt: str) -> Optional[PlayScript]:
        """根据用户话语 + 知识库证据，用大模型生成一个新剧本。"""
        if not self.can_generate():
            return None

        evidence: List[str] = []
        if self.engine:
            try:
                retrieval = self.engine.retrieve(prompt)
                evidence = [c for c in retrieval.contexts[:3] if c]
            except Exception as e:  # noqa: BLE001
                print(f"[剧本生成] 知识库检索失败: {e}")

        action_names = "、".join(self.action_map.keys())
        evidence_text = "\n".join(f"- {e}" for e in evidence) or "（无）"
        model = getattr(config, "ACTION_MODEL", "gemma3:4b") if config else "gemma3:4b"

        sys_prompt = (
            "你是海宁皮影戏的编剧，为皮影数字人编写简短、可直接演出的剧本。"
            "严格依据参考资料，不要编造与海宁皮影戏无关的事实；只输出 JSON。"
        )
        user_content = f"""用户想看的剧目/主题：{prompt}

可用皮影动作（action 字段只能从下列选择）：{action_names}

参考资料：
{evidence_text}

请输出 JSON（字段齐全）：
{{
  "title": "剧目名",
  "theme": "主题分类",
  "scenes": [
    {{"scene_id": 1, "title": "场次名", "description": "一句话说明",
      "lines": [
        {{"speaker": "角色", "text": "台词", "action": "动作", "duration": 2.5}}
      ]}}
  ]
}}
要求：3 场左右，每场 3-5 句；台词口语化、有皮影戏韵味；duration 约 2-3 秒；只输出 JSON。"""

        try:
            resp = self.llm_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.8,
            )
            content = (resp.choices[0].message.content or "").strip()
            data = self._parse_json(content)
            if not data or not data.get("scenes"):
                return None
            script = self._parse_play_data(data, data.get("title") or prompt)
            script.source_type = "generated"
            return script
        except Exception as e:  # noqa: BLE001
            print(f"[剧本生成] 大模型调用失败: {e}")
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

    def save_script(self, script: PlayScript, play_name: str = None, category: str = None) -> Path:
        """按内容哈希存档（重复内容覆盖同一文件），并限制存档数量。"""
        base_name = play_name if play_name else script.title
        scenes = [
            {
                "scene_id": s.scene_id,
                "title": s.title,
                "description": s.description,
                "lines": [
                    {"speaker": l.speaker, "text": l.text,
                     "action": l.action, "duration": l.duration}
                    for l in s.lines
                ],
            }
            for s in script.scenes
        ]
        digest = hashlib.md5(
            json.dumps({"title": script.title, "theme": script.theme, "scenes": scenes},
                       ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:8]
        filename = re.sub(r'[<>:"/\\|?*]', '_', f"{base_name}_{digest}.json")

        category = category or script.theme or "通用"
        save_dir = self.scripts_dir / category
        save_dir.mkdir(exist_ok=True)
        filepath = save_dir / filename

        data = {
            "title": script.title,
            "theme": script.theme,
            "total_duration": script.total_duration,
            "created_at": script.created_at,
            "source_type": script.source_type,
            "scenes": scenes,
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self._prune_scripts()
        return filepath

    def _prune_scripts(self) -> None:
        """只保留最近 MAX_KEEP_SCRIPTS 个生成剧本，避免无限堆积（不动剧目库）。"""
        try:
            files = [p for p in self.scripts_dir.rglob("*.json")
                     if p.name != "classic_plays.json"]
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[MAX_KEEP_SCRIPTS:]:
                try:
                    old.unlink()
                except OSError:
                    pass
        except Exception:  # noqa: BLE001
            pass


# 用于播放和UI控制
class ScriptPlayer:
    def __init__(self, action_queue, fps: int = 45):
        self.action_queue = action_queue
        self.fps = fps
        self.is_playing = False
        self.is_paused = False
        self.should_stop = False
        self.current_scene = 0
        self.current_line = 0
        self.progress = 0.0
        self.current_script_path = None
        self.current_play_name = None

    def play_script(self, script: PlayScript, script_path: Path = None, play_name: str = None):
        if self.is_playing:
            if self.is_paused:
                self.is_paused = False
                return
            self.stop()
        self.is_playing = True
        self.is_paused = False
        self.should_stop = False
        self.current_scene = 0
        self.current_line = 0
        self.progress = 0.0
        self.current_script_path = script_path
        self.current_play_name = play_name
        threading.Thread(target=self._play_thread, args=(script,), daemon=True).start()

    def _play_thread(self, script: PlayScript):
        total_lines = sum(len(scene.lines) for scene in script.scenes)
        line_index = 0
        for scene_idx, scene in enumerate(script.scenes):
            if self.should_stop:
                break
            self.current_scene = scene_idx
            for line_idx, line in enumerate(scene.lines):
                if self.should_stop or not self.is_playing:
                    break
                while self.is_paused and not self.should_stop:
                    time.sleep(0.1)
                if self.should_stop:
                    break
                self.current_line = line_idx
                self.progress = line_index / total_lines if total_lines > 0 else 0

                display_text = f"[{line.speaker}] {line.text}"
                self.action_queue.put(([line.action], display_text))
                print(f"[播放] {display_text}")

                time.sleep(line.duration)
                line_index += 1
            if scene_idx < len(script.scenes) - 1:
                time.sleep(0.15)

        self.is_playing = False
        self.progress = 1.0
        end_msg = "🎭 演出结束，感谢观看！"
        if self.current_script_path:
            end_msg += f"\n剧本已保存至: {self.current_script_path}"
        if self.current_play_name:
            end_msg += f"\n输入“表演 {self.current_play_name}”可重新演出"
        self.action_queue.put((["idle"], end_msg))

    def pause(self):
        if self.is_playing and not self.is_paused:
            self.is_paused = True

    def stop(self):
        self.should_stop = True
        self.is_playing = False
        self.is_paused = False

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_playing": self.is_playing,
            "is_paused": self.is_paused,
            "current_scene": self.current_scene,
            "current_line": self.current_line,
            "progress": self.progress
        }


class ScriptUI:
    def __init__(self, generator: ScriptGenerator, player: ScriptPlayer):
        self.generator = generator
        self.player = player
        self.current_script: Optional[PlayScript] = None
        self.current_play_name: Optional[str] = None
    def list_plays(self) -> str:
        """生成剧目目录，每主题一行，剧名用顿号分隔"""
        if not self.generator.classic_plays:
            return "📭 剧目库为空，请检查 generated_scripts/classic_plays/classic_plays.json 是否正确加载。"
        
        theme_plays = self.generator.get_play_names_by_theme()
        if not theme_plays:
            return "📭 剧目库中没有完整内容的剧目（scenes 为空或缺失）。"
        
        lines = ["📚 当前剧目库："]
        for theme, names in sorted(theme_plays.items()):
            play_list = "、".join(sorted(names))# 用顿号连接剧名
            lines.append(f"【{theme}】：{play_list}")
        return "\n".join(lines)
    def load_and_play(self, play_name: str):
        # 先查剧目库；未收录则根据用户话语现场生成
        script, hint = self.generator.get_script(play_name)
        if script:
            self.current_script = script
            self.current_play_name = play_name
            self.player.action_queue.put((["idle"], f"🎭 开始播放《{script.title}》"))
            self.player.play_script(script, None, play_name)
        elif self.generator.can_generate():
            self.generate_and_play(play_name)
        else:
            self.player.action_queue.put((["idle"], hint))

    def generate_and_play(self, prompt: str):
        """根据用户话语生成新剧本并播放（生成内容存档去重）。"""
        prompt = (prompt or "").strip() or "海宁皮影戏"
        self.player.action_queue.put((["idle"], f"✍️ 正在为您生成《{prompt}》的剧本，请稍候…"))
        script = self.generator.generate_from_prompt(prompt)
        if not script:
            self.player.action_queue.put((["idle"], f"抱歉，暂时无法生成《{prompt}》的剧本。"))
            return
        self.current_script = script
        self.current_play_name = prompt
        filepath = self.generator.save_script(script, prompt, category=script.theme)
        print(f"[剧本生成] 已生成《{script.title}》，存档: {filepath}")
        self.player.action_queue.put((["idle"], f"🎭 已生成并开始播放《{script.title}》"))
        self.player.play_script(script, filepath, prompt)

    def play_current(self):
        #播放当前已加载的剧本
        if self.current_script:
            self.player.play_script(self.current_script, None, self.current_play_name)
        else:
            self.player.action_queue.put((["idle"], "⚠️ 没有已加载的剧本，请先使用“表演 剧名”"))

    # 兼容 main.py 的调用名（内部方法名为 play_current）
    play = play_current

    def pause(self):
        self.player.pause()

    def stop(self):
        self.player.stop()

    def get_status(self):
        return self.player.get_status()


def create_script_system(action_map: Dict[str, str], action_queue, fps: int = 45,
                         engine=None, llm_client=None) -> ScriptUI:
    generator = ScriptGenerator(action_map, fps, engine=engine, llm_client=llm_client)
    player = ScriptPlayer(action_queue, fps)
    return ScriptUI(generator, player)