#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_ppt_ai.py — PPT 文本 AI 味扫描器（ppt-deai 技能配套）

输入格式（推荐）：每行一页，行首 "P<页码>\t"，页内多个形状文本用 \t 分隔。
    P1\t把想法\t做成\t作品\t—— 两条通往同一个目标的路径
    P2\t目录 · 全文导航\t02 / 22\t...

也接受纯文本（无 P 行首），此时按整篇一页处理（P0）。

输出：Markdown 报告（stdout）或 --json。模式编号见 references/ppt-ai-patterns.md。
"""
import json
import re
import sys
from collections import defaultdict
from difflib import SequenceMatcher

# ---------- A 类：词面模式 ----------

# A3 空词、万能动词与套话（按 references/ppt-ai-patterns.md 七组收录）
EMPTY_WORDS = [
    # 空词
    "赋能", "助力", "彰显", "夯实", "锚定", "聚焦", "统筹推进", "提质增效",
    "蝶变", "跃升", "至关重要", "全方位", "多层次", "一站式", "闭环",
    # 程度副词通胀
    "极大地", "显著地", "充分地", "深刻地", "不可或缺", "举足轻重",
    "发挥着重要", "日新月异", "层出不穷", "雨后春笋",
    # 万能动词
    "进行分析", "加以", "予以", "旨在", "致力于", "着眼于", "着力于",
    # 开篇套话
    "在当今", "众所周知", "不可否认", "古往今来", "从古至今", "大背景下",
    # 排比三连与对仗
    "有温度", "有深度", "有高度", "机遇与挑战", "传承与创新",
    "注入", "新活力", "新篇章", "新台阶", "新大门", "加速键", "加速度",
    # 陈词套话
    "明灯", "指明方向", "保驾护航", "添砖加瓦", "基石", "双刃剑",
    "催化剂", "助推器", "纽带", "桥梁", "沃土", "春风化雨",
    "润物细无声", "应运而生", "水到渠成",
    # 升华体与翻译腔
    "未来可期", "拭目以待", "共创美好", "携手共进", "新征程",
    "不负韶华", "星辰大海", "奔赴山海", "贡献力量",
    "被广泛应用", "在一定程度上", "相对而言", "总的来说",
    "伴随着", "不断深入",
]
# A3 开篇套话（正则）："随着……的不断发展"式长状语
CLICHE_OPEN = re.compile(r"随着[^，。；！？]{2,15}(发展|推进|深入|普及|应用|出台|落地|演进)")
# A4 AI 高频连接词与关联词
CONNECTIVES = [
    "此外", "与此同时", "综上所述", "由此可见", "值得一提的是", "不难发现",
    "换言之", "一方面", "另一方面", "总而言之",
    "不仅", "更是", "与其", "不如", "说到底", "本质上", "关键在于",
]

# A2 拆字排版：单独成段的单位后缀
SPLIT_UNITS = {"万+", "+", "＋", "岁", "层", "个", "所", "件", "名", "月", "周", "天", "条", "步", "问", "届"}

# A1 英文装饰标签：3 个以上连续字母的英文词（排除常见无害缩写）
EN_WORD = re.compile(r"[A-Za-z]{3,}")
EN_WHITELIST = {"PPT", "AI"}

# ---------- B 类：结构模式 ----------

# B1 数字框架
NUM_FRAME = re.compile(
    r"(三|四|五|六|七|八|九|十|十二|百)(个|大|步|层|问|件事|件套|类|维|位一体)"
    r"|\d+\s*(个|大|步|层|问|件事|条|类|Rules|RULES)"
    r"|第一|第二|第三"
)
# B2 对仗翻转与骈偶（「不是A而是B」是限制项，全稿 ≤2 处——句法规则二）
NOT_BUT = re.compile(r"不是[^，。；]{1,30}[，,]\s*(而)?是")
FIRST_THEN = re.compile(r"先[^，。；]{1,20}[，,]\s*再")
COMPARE = re.compile(r"[^，。；]{1,12}比[^，。；]{1,12}更")
# B3 祈使句标题：短段以动词/命令词开头
IMPERATIVE_START = re.compile(
    r"^(把|去|先做|删掉|别|走出|做够|记住|试试|让|要|得|必须|一定|马上|立刻|开始|坚持|相信)"
)
# B4 无灵主语：抽象主语 + 人类动词
INANIMATE = re.compile(
    r"(规则|比赛|时间|完美主义|报名|进度条|课堂|简历|舞台|评委席|台下的|这一步)"
    r"[^，。；]{0,12}(淘汰|逼|会给|送你|决定|意味着|见证|标志|是.{0,6}敌人|跑起来|关不掉)"
)
# B6 Manifesto 结尾模板
MANIFESTO_TAIL = re.compile(r"TAKEAWAYS|RULES|END OF|→\s*完|Manifesto|MANIFESTO|宣言")

# B7 逗号链长句：两个句号之间的逗号 ≥3 即命中（句法规则四）
SENT_SPLIT = re.compile(r"[。！？!?…]+")
COMMA_CHAIN_MIN = 3

# B8 感叹号 / B9 设问句：标题与正文分别判定（句法规则五）
TITLE_MAX_LEN = 16     # 与 B3 一致：短段视为标题
EXCLAMATION = re.compile(r"[！!]")
QUESTION_END = re.compile(r"[？?]\s*$")
QUESTION_MARK = re.compile(r"[？?]")

# B5 自我重复
MIN_DUP_LEN = 8        # 跨页重复片段最小长度（含中文）
MIN_DUP_LEN_EN = 10    # 纯英文/数字片段的最小长度
STOP_SUBSTR = (        # 公共长词，跨页出现属正常，不计
    "中国国际大学生创新大赛", "全国大学生课外学术科技作品", "挑战杯",
)
CJK = re.compile(r"[\u4e00-\u9fff]")


def parse_pages(text):
    pages = []
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        m = re.match(r"^P(\d+)\t(.*)$", line)
        if m:
            pages.append((int(m.group(1)), [s.strip() for s in m.group(2).split("\t") if s.strip()]))
        elif re.match(r"^P\d+\s", line):  # 宽容：空格分隔
            head, rest = line.split(None, 1)
            pages.append((int(head[1:]), [s.strip() for s in rest.split("\t") if s.strip()]))
        else:
            pages.append((0, [line.strip()]))
    if not pages:
        pages = [(0, [text.strip()])]
    return pages


def norm(s):
    return re.sub(r"[\s，。；：、！？·—…「」『』（）()\"']", "", s)


def find_duplicates(pages):
    """跨页找最长公共片段。返回 [(片段, [页码...], 示例原文)]，已合并重叠。"""
    # 每页拼接后的归一化文本 + 起始片段示例
    page_text = {}
    for pno, segs in pages:
        joined = norm("".join(segs))
        if joined:
            page_text[pno] = joined
    pnos = sorted(page_text)
    found = {}  # fragment -> set(pages)
    example = {}
    for i in range(len(pnos)):
        for j in range(i + 1, len(pnos)):
            a, b = page_text[pnos[i]], page_text[pnos[j]]
            sm = SequenceMatcher(None, a, b, autojunk=False)
            for block in sm.get_matching_blocks():
                frag = a[block.a:block.a + block.size]
                if len(frag) < MIN_DUP_LEN:
                    continue
                if not CJK.search(frag) and len(frag) < MIN_DUP_LEN_EN:
                    continue
                if any(stop in frag for stop in STOP_SUBSTR):
                    continue
                key = (frag, pnos[i], pnos[j])
                found.setdefault(frag, set()).update({pnos[i], pnos[j]})
                example.setdefault(frag, frag)
    # 合并互为子串的片段
    frags = sorted(found, key=len, reverse=True)
    merged = []
    for f in frags:
        if any(f in m for m in merged):
            for m in merged:
                if f in m:
                    found[m] |= found[f]
            continue
        merged.append(f)
    return [(f, sorted(found[f]), example[f]) for f in merged]


def scan(pages):
    hits = []
    page_flags = defaultdict(list)

    def add(ded, code, page, snippet, note, aggregate=False):
        hits.append({"deduction": ded, "code": code, "page": page,
                     "snippet": snippet[:60], "note": note})
        if not aggregate:
            page_flags[page].append(code)

    # ---- A1 英文标签 ----
    en_count = 0
    for pno, segs in pages:
        for s in segs:
            words = [w for w in EN_WORD.findall(s) if w.upper() not in EN_WHITELIST]
            if words:
                en_count += 1
                add(3, "A1", pno, s, "英文装饰标签：" + "/".join(words[:3]))
    if en_count > 6:
        add(0, "A1*", 0, f"共 {en_count} 处英文标签",
            ">6 处高危：只保留 1–2 个签名感标签（如 Field Note / MANIFESTO）", aggregate=True)

    # ---- A2 拆字排版 ----
    for pno, segs in pages:
        for s in segs:
            if s in SPLIT_UNITS:
                add(2, "A2", pno, s, "拆字排版：数字与单位拆成两段")

    # ---- A3 空词 / 套话 ----
    for pno, segs in pages:
        for s in segs:
            for w in EMPTY_WORDS:
                if w in s:
                    add(2, "A3", pno, s, f"空词/套话：{w}")
            m = CLICHE_OPEN.search(s)
            if m:
                add(2, "A3", pno, s, f"开篇套话（长状语）：{m.group(0)[:20]}")

    # ---- A4 连接词 ----
    for pno, segs in pages:
        for s in segs:
            for w in CONNECTIVES:
                if w in s:
                    add(1, "A4", pno, s, f"AI 高频连接词：{w}")

    # ---- B1 数字框架 ----
    for pno, segs in pages:
        for s in segs:
            m = NUM_FRAME.search(s)
            if m:
                add(3, "B1", pno, s, f"数字框架：{m.group(0)}")

    # ---- B2 对仗翻转与骈偶 ----
    for pno, segs in pages:
        for s in segs:
            for m in NOT_BUT.finditer(s):
                add(3, "B2", pno, s, "「不是A（而）是B」句式（限制项，全稿合计 ≤2 处）")
            for m in FIRST_THEN.finditer(s):
                add(3, "B2", pno, s, "「先X再Y」对仗句")
            for m in COMPARE.finditer(s):
                add(1, "B2", pno, s, "「A比B更」比较句")

    # ---- B3 祈使句标题 ----
    for pno, segs in pages:
        for s in segs:
            if len(s) <= 16 and IMPERATIVE_START.match(s):
                add(4, "B3", pno, s, "祈使句标题（全稿配额 ≤1，留给结尾）")

    # ---- B4 无灵主语 ----
    for pno, segs in pages:
        for s in segs:
            for m in INANIMATE.finditer(s):
                add(3, "B4", pno, s, f"无灵主语：{m.group(0)[:20]}")

    # ---- B5 自我重复（最强指标）----
    # 5a 整段（归一化后）跨页一字不差
    seg_pages = defaultdict(set)
    seg_example = {}
    for pno, segs in pages:
        for s in segs:
            nt = norm(s)
            if len(nt) >= 4:
                seg_pages[nt].add(pno)
                seg_example.setdefault(nt, s)
    covered = set()
    for nt, pset in seg_pages.items():
        if len(pset) >= 2:
            covered.add(nt)
            add(10, "B5", min(pset), seg_example[nt],
                f"整段一字不差重复于 {sorted(pset)} 页——最高优先级")
    # 5b 跨页最长公共片段（覆盖跨形状的整句重复）
    for frag, pset, ex in find_duplicates(pages):
        if any(frag in c for c in covered):
            continue
        add(4, "B5", min(pset), ex,
            f"片段「{frag}」重复于 {sorted(pset)} 页——先确认是否有意回环（目录↔章节标题算正常），无意则删")

    # ---- B7 逗号链长句 ----
    for pno, segs in pages:
        for s in segs:
            for sent in SENT_SPLIT.split(s):
                sent = sent.strip(" ，,、；;：:")
                if not sent:
                    continue
                n_comma = sent.count("，") + sent.count(",")
                if n_comma >= COMMA_CHAIN_MIN:
                    is_title = len(sent) <= 16
                    add(2 if is_title else 3, "B7", pno, sent,
                        f"逗号链：一句 {n_comma} 个逗号，拆成短句（标题 -2 / 正文 -3）")

    # ---- B6 Manifesto 结尾 ----
    if pages:
        tail_pages = pages[-2:] if len(pages) >= 2 else pages
        for pno, segs in tail_pages:
            hit = next((s for s in segs if MANIFESTO_TAIL.search(s)), None)
            if hit:
                add(10, "B6", pno, hit, "Manifesto 结尾模板（总结陈词/结束标记）")

    # ---- B8 感叹号 / B9 设问句 ----
    for pno, segs in pages:
        for s in segs:
            is_title = len(s) <= TITLE_MAX_LEN
            # B8 感叹号：标题本该零感叹号
            n_excl = len(EXCLAMATION.findall(s))
            if n_excl:
                if is_title:
                    add(4, "B8", pno, s, f"标题含感叹号 ×{n_excl}——标题零感叹号，删掉改陈述")
                else:
                    add(2, "B8", pno, s, f"感叹号 ×{n_excl}——删感叹号，句子改陈述")
            # B9 设问句：标题问号结尾，或正文问号（自问自答每个问号计一次）
            if is_title and QUESTION_END.search(s):
                add(3, "B9", pno, s, "设问句标题——判断直接给，改成陈述")
            else:
                n_q = len(QUESTION_MARK.findall(s))
                if n_q:
                    add(2, "B9", pno, s, f"设问句 ×{n_q}——有观点就直接说")

    # ---- 封顶 ----
    cap = {"A1": 12, "A2": 6, "A3": 12, "A4": 8, "B1": 15, "B2": 12,
           "B3": 12, "B4": 9, "B5": 20, "B6": 10, "B7": 10, "B8": 8, "B9": 6}
    by_code = defaultdict(int)
    for h in hits:
        by_code[h["code"].rstrip("*")] += h["deduction"]
    for code, total in by_code.items():
        if code in cap and total > cap[code]:
            ratio = cap[code] / total
            for h in hits:
                if h["code"].rstrip("*") == code:
                    h["deduction"] = round(h["deduction"] * ratio, 1)

    hits.sort(key=lambda h: -h["deduction"])
    score = max(0, round(100 - sum(h["deduction"] for h in hits)))
    return score, hits, dict(page_flags)


def grade(score):
    if score >= 85:
        return "🟢 人话"
    if score >= 70:
        return "🟡 微 AI 味"
    if score >= 50:
        return "🟠 中度 AI 味"
    if score >= 30:
        return "🔴 重度 AI 味"
    return "⚫ 完全 AI 体"


CODE_NAMES = {
    "A1": "英文装饰标签", "A2": "拆字排版", "A3": "空词/套话", "A4": "AI 连接词",
    "B1": "数字框架", "B2": "对仗翻转与骈偶", "B3": "祈使句标题", "B4": "无灵主语",
    "B5": "自我重复", "B6": "Manifesto 结尾", "B7": "逗号链长句",
    "B8": "感叹号滥用", "B9": "设问句滥用",
}


def report_md(path, score, hits, page_flags):
    by_code = defaultdict(float)
    for h in hits:
        by_code[h["code"].rstrip("*")] += h["deduction"]
    lines = ["# PPT AI 味扫描报告", "",
             f"**文件：** {path}",
             f"**总分：** {score} / 100　{grade(score)}（100 = 完全人话）", "",
             "## 模式命中总览", "",
             "| 模式 | 名称 | 扣分 |", "|---|---|---|"]
    for code in sorted(by_code):
        lines.append(f"| {code} | {CODE_NAMES.get(code, code)} | -{by_code[code]} |")
    lines += ["", "## Top 问题（按扣分排序）", "",
              "| # | 扣分 | 模式 | 页 | 命中 | 说明 |",
              "|---|---|---|---|---|---|"]
    for i, h in enumerate(hits[:15], 1):
        lines.append(f"| {i} | -{h['deduction']} | {h['code']} | P{h['page']} | `{h['snippet']}` | {h['note']} |")
    if not hits:
        lines.append("| - | 0 | - | - | 未命中已知模式 | - |")
    lines += ["", "## 逐页命中", ""]
    for p in sorted(page_flags):
        lines.append(f"- P{p}：{', '.join(sorted(set(page_flags[p])))}")
    lines += ["", "## 处置顺序", "",
              "1. **B5（自我重复）最优先**——一字不差的跨页重复是机器生成铁证，先删后改；",
              "2. B6 结尾模板 → 删总结陈词与结束标记；",
              "3. B3 祈使句标题 → 全稿留 ≤1 处给结尾；",
              "4. B4 无灵主语 → 换成「谁 + 做了什么」；",
              "5. B7 逗号链 → 一句 ≥3 个逗号就拆成短句，句号果断；",
              "6. B8/B9 感叹号与设问 → 标题零感叹号，设问改直陈；",
              "7. B1/B2 数字框架、对仗骈偶、「不是A而是B」→ 拆，关联词全稿 ≤2 处；",
              "8. A 类词面 → 英文标签留 1–2 个，空词连接词清零。", "",
              "> 本报告为启发式扫描，结构模式需人工确认；另请过一遍 `references/ppt-ai-patterns.md` 的 D 类结构信号检查单。改写方法见 `references/syntax-five-rules.md` 与 `references/page-type-playbook.md`。"]
    return "\n".join(lines)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    if not args:
        print(__doc__)
        sys.exit(1)
    path = args[0]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    pages = parse_pages(text)
    score, hits, page_flags = scan(pages)
    if as_json:
        print(json.dumps({"file": path, "score": score, "grade": grade(score),
                          "hits": hits, "page_flags": {str(k): v for k, v in page_flags.items()}},
                         ensure_ascii=False, indent=2))
    else:
        print(report_md(path, score, hits, page_flags))


if __name__ == "__main__":
    main()
