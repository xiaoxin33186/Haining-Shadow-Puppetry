# -*- coding: utf-8 -*-
"""海宁皮影戏黄金评测集回归测试

用途：每次对项目（检索/润色/知识库）做出改动后，运行本脚本量化回答质量，
      防止改动引入回归（原来答对的题被改坏）。

评测集：51 题，固定不变（覆盖题库 26 个分类，易/中/难混合）。
评分规则：
  match_type = qa        → 1.0 分（题库标准答案命中）
  match_type = knowledge → 0.7 分（知识检索命中，建议抽检内容）
  match_type = none      → 0.0 分（未命中）

用法：
  python run_eval.py            # 第一级检索评测（快，不调用 LLM）
  python run_eval.py --full     # 全链路评测（含动作生成与润色，较慢）
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from local_knowledge_qa import KnowledgeQAConfig, KnowledgeQAEngine

# ── 黄金评测集（固定，勿改；如需更换题目请整体评审后更新版本号）──
GOLDEN_SET = [
    ("Q001", "什么是海宁皮影戏？"),
    ("Q003", "皮影戏还常被称为什么？"),
    ("Q021", "皮影戏起源于汉代的说法可靠吗？"),
    ("Q025", "嘉兴市政府资料记载宋代皮影戏流行在什么场所？"),
    ("Q041", "海宁皮影戏在哪一年列入第一批浙江省非遗名录？"),
    ("Q045", "海宁皮影戏在国家级非遗名录中属于哪一类？"),
    ("Q056", "中国非遗网列出的传统影人制作八道工序是什么？"),
    ("Q060", "除人物影偶外，传统皮影还要制作哪些舞台造型？"),
    ("Q081", "中国皮影数字博物馆把影偶制作分为哪些主要环节？"),
    ("Q085", "皮影雕刻中的阳刻“空底”是什么意思？"),
    ("Q101", "皮影戏舞台上的影像怎样形成？"),
    ("Q105", "皮影戏怎样用“远虚近实”表现恶龙盘旋？"),
    ("Q126", "数字博物馆记载海宁皮影武戏主要使用哪些伴奏乐器？"),
    ("Q130", "海盐腔擅长表现哪些内容？"),
    ("Q146", "权威网页可查到哪些海宁皮影代表剧目？"),
    ("Q150", "《南天国之狄青平南》的主要故事是什么？"),
    ("Q166", "公开资料可核实的海宁皮影戏国家级代表性传承人有哪些？"),
    ("Q170", "张坤荣自2004年起排演并主演了哪些剧目？"),
    ("Q191", "公开历史资料记录过哪些海宁皮影演出地点？"),
    ("Q195", "2024浙江非遗嘉年华怎样组织海宁皮影互动？"),
    ("Q201", "旧时海宁皮影艺人怎样前往水路沿线村镇演出？"),
    ("Q205", "旧时海宁乡村皮影戏怎样搭建临时舞台？"),
    ("Q221", "传统海宁影偶主要使用哪两种皮料？"),
    ("Q225", "海宁影偶按照哪些传统戏剧角色类型设计？"),
    ("Q241", "2019年“光影故事”展共展出多少件海宁影偶？"),
    ("Q245", "“光影故事”展中的清代影偶是怎样保存下来的？"),
    ("Q261", "传统海宁皮影戏中的“上手”负责什么？"),
    ("Q265", "改革后的海宁皮影戏怎样安排影偶操纵？"),
    ("Q281", "张坤荣官方页面记录的《鸡斗》是什么时期重新排演的？"),
    ("Q285", "2019年报道中的外地观众听不懂唱词后怎样评价演出？"),
    ("Q301", "张坤荣官方页面记录他近年来收了哪些徒弟？"),
    ("Q305", "数字博物馆列举了哪些南宋临安皮影艺人？"),
    ("Q321", "中国非遗网“海宁皮影戏演绎光影故事”报道发布于哪一年？"),
    ("Q325", "“光影故事”展现场演出的传统正本戏是哪一部？"),
    ("Q341", "海宁从什么时候开始整理皮影唱腔和剧本？"),
    ("Q345", "数字博物馆把皮影概括为包含哪些艺术门类的综合形式？"),
    ("Q361", "《童年的金庸》首演所对应的金庸纪念年份是什么？"),
    ("Q365", "《童年的金庸》主要讲述金庸几岁到几岁的经历？"),
    ("Q381", "2003年哪一项海外活动邀请海宁皮影戏演出？"),
    ("Q385", "2025年马耳他春节庙会在哪所校园举行？"),
    ("Q401", "2025年海宁非遗活动推出了什么皮影主题饮品？"),
    ("Q405", "徐芦燕为《过猴林》设计了哪些影偶？"),
    ("Q421", "2004年沈圣标重新组织了多少位老艺人？"),
    ("Q425", "2024年进博会浙江人文交流展区首次以什么为主线？"),
    ("Q441", "哪首老调让郭志英在剧团重组时重新唱出旧词？"),
    ("Q445", "海宁皮影剧团恢复后的汇报演出产生了什么结果？"),
    ("Q461", "横向比较海宁皮影时，唐山皮影属于哪一地域影系？"),
    ("Q465", "横向了解海宁皮影所属的中国皮影系统时，陕西皮影是哪一影系代表？"),
    ("Q481", "海宁皮影所属的中国皮影戏还常被称为什么？"),
    ("Q485", "理解海宁皮影剧目传承时，许多老皮影艺人能演多少传统剧目？"),
    ("Q501", "《童年的金庸》何时、在哪里首演？"),
    ("Q505", "海宁皮影艺术团为适应当代观众做过哪些具体改编？"),
    # ── 扩展样本（非题库题，用于覆盖 knowledge / none 通道）──
    ("X01", "[开放]海宁皮影戏与海宁灯彩有什么文化关联？"),
    ("X02", "[开放]AIGC技术如何赋能海宁皮影戏的数字化发展？"),
    ("X03", "[开放]海宁皮影戏进校园有哪些实践案例？"),
    ("X04", "[无关]今天天气怎么样？"),
    ("X05", "[无关]推荐一部科幻电影。"),
]

SCORES = {"qa": 1.0, "knowledge": 0.7, "none": 0.0}


def run_l1(engine):
    """第一级检索评测（本地引擎，不调用 LLM）"""
    results = []
    for qid, question in GOLDEN_SET:
        r = engine.query(question)
        score = SCORES.get(r.match_type, 0.0)
        results.append((qid, question, r.match_type, r.confidence, score))
        print(f"[{qid}] {r.match_type:<10s} conf={r.confidence:.2f}  {question[:34]}")
    n = len(results)
    qa_n = sum(1 for x in results if x[2] == "qa")
    kb_n = sum(1 for x in results if x[2] == "knowledge")
    none_n = sum(1 for x in results if x[2] == "none")
    total = sum(x[4] for x in results)
    print("\n=== 第一级检索评测结果 ===")
    print(f"总题数: {n} | QA命中: {qa_n} ({qa_n / n:.1%}) | "
          f"知识命中: {kb_n} ({kb_n / n:.1%}) | 未命中: {none_n} ({none_n / n:.1%})")
    print(f"加权得分: {total:.1f} / {n} = {total / n:.2%}")
    return results, total


def run_full():
    """全链路评测（含动作生成与润色）"""
    import asyncio
    import skill_chatchat_hybrid as hybrid

    system = hybrid.HybridAgentSystem({})
    for qid, question in GOLDEN_SET:
        acts, answer = asyncio.run(system.process_input(question))
        print(f"[{qid}] 动作={acts} | {answer[:60]}")


def main():
    parser = argparse.ArgumentParser(description="海宁皮影戏黄金评测集回归测试")
    parser.add_argument("--full", action="store_true", help="全链路评测（较慢）")
    args = parser.parse_args()

    engine = KnowledgeQAEngine(KnowledgeQAConfig(discovery_root=ROOT))
    h = engine.health()
    print(f"知识库状态: docs={h['documents']} qa={h['qa_pairs']} chunks={h['chunks']} status={h['status']}\n")

    run_l1(engine)
    if args.full:
        print("\n=== 全链路评测 ===")
        run_full()


if __name__ == "__main__":
    main()
