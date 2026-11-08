#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
长文 -> 小红书图文（3:4 多页 PNG）

用法:
  python3 render.py --input note.md --out ../out/demo
  python3 render.py --input note.md --out ../out/demo --style magazine
  python3 render.py --input note.md --out ../out/demo --style dark --kicker "长期主义 · 05"

输入 markdown 约定:
  # 大字标题          -> 封面主标题（可写多行，用 \n 或空行分隔）
  > 导语一句话        -> 封面副标题
  其余段落            -> 正文，自动分页
  支持 **加粗**  和  ==高亮==
  以 - 开头的行      -> 列表项

输出:
  out/page-01.png ...  1080x1440
  out/meta.json        页数与文件清单，供发布端读取
"""

import argparse
import json
import re
import sys
from pathlib import Path
from string import Template

from PIL import Image
from playwright.sync_api import sync_playwright

W, H = 1080, 1440
PAD_X = 96
PAD_TOP = 100
PAD_BOTTOM = 96
TOPLINE_H = 60
CONTENT_W = W - PAD_X * 2          # 888
CONTENT_H = H - PAD_TOP - PAD_BOTTOM - TOPLINE_H  # 1184
DSF = 2                             # 先 2 倍渲染再降采样，字更锐

# ---------------------------------------------------------------- 风格

STYLES = {
    "minimal": {
        "bg": "#FDFDFB", "fg": "#1B1B1B", "muted": "#8C8C8C", "line": "#E6E6E2",
        "accent": "#E2452F", "hl": "#FFE27A",
        "font": "'PingFang SC','Hiragino Sans GB','STHeiti',system-ui,sans-serif",
        "title_font": "'PingFang SC','Hiragino Sans GB','STHeiti',sans-serif",
        "fs": 40, "lh": 1.95, "title_weight": 800, "quote_bg": "#F5F4EF",
    },
    "magazine": {
        "bg": "#F6F1E6", "fg": "#22201C", "muted": "#8A8073", "line": "#D8CFBC",
        "accent": "#A63B2A", "hl": "#E9C87A",
        "font": "'PingFang SC','Hiragino Sans GB','STHeiti',system-ui,sans-serif",
        "title_font": "'Songti SC','STSong',serif",
        "fs": 40, "lh": 2.0, "title_weight": 700, "quote_bg": "#EFE7D6",
    },
    "dark": {
        "bg": "#141414", "fg": "#EDEBE6", "muted": "#7E7B76", "line": "#2E2C29",
        "accent": "#E8503A", "hl": "#6E5A1F",
        "font": "'PingFang SC','Hiragino Sans GB','STHeiti',system-ui,sans-serif",
        "title_font": "'PingFang SC','Hiragino Sans GB','STHeiti',sans-serif",
        "fs": 40, "lh": 1.95, "title_weight": 700, "quote_bg": "#1F1E1C",
    },
    # 深色卡片风：整篇统一一个深色底，颜色在笔记之间轮换（001 蓝→002 绿→003 红→004 黑）
    "card": {
        "bg": "#2B3D6E",
        "fg": "#F0ECDF", "muted": "rgba(240,236,223,0.62)",
        "line": "rgba(240,236,223,0.26)",
        "accent": "#F0ECDF", "hl": "rgba(240,236,223,0.22)",
        "font": "'Songti SC','Noto Serif SC','STSong',serif",
        "title_font": "'Songti SC','Noto Serif SC','STSong',serif",
        "fs": 40, "lh": 2.0, "title_weight": 700, "quote_bg": "rgba(255,255,255,0.07)",
        "bg_cycle": ["#2B3D6E", "#2E4A3D", "#682A34", "#1E1D1B"],  # 蓝/绿/红/黑
    },
}

CSS_TMPL = Template("""
*{margin:0;padding:0;box-sizing:border-box;-webkit-font-smoothing:antialiased}
html,body{width:${W}px;height:${H}px;overflow:hidden}
.page{width:${W}px;height:${H}px;position:relative;background:$bg;color:$fg;
  padding:${PAD_TOP}px ${PAD_X}px ${PAD_BOTTOM}px;display:flex;flex-direction:column;
  font-family:$font}
#m{width:${CONTENT_W}px;padding-bottom:2px;overflow:hidden}

/* ---------- 正文 ---------- */
.p{font-size:${fs}px;line-height:${lh};letter-spacing:.015em;margin-bottom:32px;
   text-align:left}
.p:last-child{margin-bottom:0}
.p.list{padding-left:34px;text-indent:-34px;margin-bottom:22px}
.p.list .bu{color:$accent;font-weight:700;margin-right:8px}
quote-block{display:block}
.q{background:$quote_bg;border-left:5px solid $accent;padding:30px 34px;margin:8px 0 34px;
   font-size:${fs_q}px;line-height:1.85;color:$fg}
.h2{font-size:44px;line-height:1.55;font-weight:700;letter-spacing:-.005em;
   margin:46px 0 28px;padding-left:24px;border-left:7px solid $accent}
.content>.h2:first-child{margin-top:0}
strong{font-weight:700}
.hl{background:linear-gradient(transparent 62%,$hl 62%);font-weight:600;padding:0 2px}
.u{background:linear-gradient(transparent 88%,$accent 88%,$accent 94%,transparent 94%)}

/* ---------- 页眉页脚 ---------- */
.topline{height:${TOPLINE_H}px;display:flex;justify-content:flex-end;align-items:flex-start}
.pg{font-size:22px;letter-spacing:.12em;color:$muted;font-variant-numeric:tabular-nums}
.content{flex:1;overflow:hidden}
.foot{height:44px;display:flex;justify-content:space-between;align-items:flex-end;
  font-size:19px;letter-spacing:.08em;color:$muted}
.foot .bar{width:44px;height:2px;background:$line;display:block}

/* ---------- 封面 ---------- */
.cover{flex:1;display:flex;flex-direction:column}
.kicker{display:flex;align-items:center;gap:14px;font-size:23px;letter-spacing:.18em;
  color:$muted}
.kicker i{display:block;width:36px;height:3px;background:$accent;margin-top:-1px}
.cover-mid{flex:1;display:flex;flex-direction:column;justify-content:center;padding-bottom:60px}
.t-main{font-family:$title_font;line-height:1.32;font-weight:${tw};
   letter-spacing:-.02em;color:$fg}
.t-sub{font-weight:500;line-height:1.75;color:$muted;margin-top:26px;letter-spacing:.005em}
.rule{width:96px;height:4px;background:$accent;margin:44px 0 0}
.lead{font-size:29px;line-height:1.85;color:$muted;margin-top:34px;max-width:820px}
.cover-foot{display:flex;justify-content:space-between;align-items:flex-end;font-size:22px;
  color:$muted;letter-spacing:.06em}

/* ---------- 结尾页 ---------- */
.end-mid{height:100%;display:flex;flex-direction:column;justify-content:center}
.end-tag{font-size:24px;letter-spacing:.2em;color:$accent;margin-bottom:36px}
.end-big{font-family:$title_font;font-size:58px;line-height:1.5;font-weight:700;letter-spacing:.01em}
.end-sub{font-size:30px;line-height:1.9;color:$muted;margin-top:44px}
""")

# ---------------------------------------------------------------- 文本处理

def md_to_html(text: str) -> str:
    """行内标记转 HTML"""
    s = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"==(.+?)==", r'<span class="hl">\1</span>', s)
    s = s.replace("\n", "<br>")
    return s


def parse_input(raw: str):
    lines = [l.rstrip() for l in raw.replace("\r\n", "\n").split("\n")]
    title_parts, lead, blocks, buf = [], "", [], []
    mode = "pre"

    def flush():
        nonlocal buf
        if buf:
            blocks.append(("p", "\n".join(buf).strip()))
            buf = []

    for ln in lines:
        s = ln.strip()
        if not s:
            if mode == "pre":
                continue
            flush()
            continue
        if s.startswith("# ") and mode == "pre":
            title_parts.append(s[2:].strip())
            continue
        if s.startswith("> ") and mode == "pre":
            lead = (lead + " " + s[2:].strip()).strip()
            continue
        mode = "body"
        if s.startswith("## "):
            flush()
            blocks.append(("h2", s[3:].strip()))
            continue
        if s.startswith(("- ", "· ", "• ")):
            flush()
            blocks.append(("list", s[2:].strip()))
            continue
        if s.startswith("> "):
            flush()
            blocks.append(("q", s[2:].strip()))
            continue
        buf.append(s)
    flush()
    return "\n".join(title_parts).strip(), lead, blocks


SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])")


def to_units(blocks):
    """切成最小排版单元：段落按句切（可在句子间断页），列表/引用/小标题整体不可分。
    返回 (kind, text, pid)，同一段的句子共享 pid，渲染时再合并回整段。"""
    units, pid = [], 0
    for kind, text in blocks:
        if kind in ("list", "q", "h2"):
            pid += 1
            units.append((kind, text, f"s{pid}"))
            continue
        for para in text.split("\n"):
            para = para.strip()
            if not para:
                continue
            pid += 1
            for sent in SENT_SPLIT.split(para):
                if sent.strip():
                    units.append(("p", sent.strip(), f"p{pid}"))
    return units


def unit_html(kind, text):
    if kind == "list":
        return f'<div class="p list"><span class="bu">—</span>{md_to_html(text)}</div>'
    if kind == "q":
        return f'<div class="q">{md_to_html(text)}</div>'
    if kind == "h2":
        return f'<div class="h2">{md_to_html(text)}</div>'
    return f'<div class="p">{md_to_html(text)}</div>'


def render_units(items):
    """把单元渲染成 HTML：同一段落的连续句子合并回一个 <p>，保证断页不断段。"""
    out, i, n = [], 0, len(items)
    while i < n:
        kind, text, pid = items[i]
        if kind != "p":
            out.append(unit_html(kind, text))
            i += 1
            continue
        buf, j = [], i
        while j < n and items[j][0] == "p" and items[j][2] == pid:
            buf.append(items[j][1])
            j += 1
        out.append(f'<div class="p">{md_to_html("".join(buf))}</div>')
        i = j
    return "".join(out)


def paginate(page, units, css):
    """贪心装页：逐单元塞入并实测高度，溢出就翻页。
    另外保证小标题后面至少跟得下 2 行正文，否则整体推到下一页。"""
    page.set_content(
        f'<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>'
        f'<body><div class="page"><div class="content"><div id="m"></div></div></div></body></html>'
    )

    def measure(items):
        page.eval_on_selector("#m", "(el,h)=>{el.innerHTML=h}", render_units(items))
        return page.eval_on_selector("#m", "el=>el.scrollHeight")

    pages, cur, h_cur = [], [], 0
    MIN_AFTER_H2 = 440
    for unit in units:
        if unit[0] == "h2" and cur and (CONTENT_H - h_cur) < MIN_AFTER_H2:
            pages.append(cur)
            cur, h_cur = [], 0
        cand = cur + [unit]
        h = measure(cand)
        if h > CONTENT_H and len(cand) > 1:
            pages.append(cur)
            cur = [unit]
            h_cur = measure(cur)
        else:
            cur, h_cur = cand, h
    if cur:
        pages.append(cur)
    for i in range(len(pages) - 1):
        while len(pages[i]) > 1 and pages[i][-1][0] == "h2":
            pages[i + 1].insert(0, pages[i].pop())
    return pages


# ---------------------------------------------------------------- 组页

def css_for(style_key, fs_override=None, bg_override=None):
    st = STYLES[style_key]
    fs = fs_override or st["fs"]
    return CSS_TMPL.substitute(
        W=W, H=H, PAD_TOP=PAD_TOP, PAD_X=PAD_X, PAD_BOTTOM=PAD_BOTTOM,
        CONTENT_W=CONTENT_W, TOPLINE_H=TOPLINE_H,
        bg=bg_override or st["bg"], fg=st["fg"], muted=st["muted"], line=st["line"],
        accent=st["accent"], hl=st["hl"], font=st["font"], title_font=st["title_font"],
        quote_bg=st["quote_bg"],
        fs=fs, fs_q=fs - 2, lh=st["lh"], tw=st["title_weight"],
        title_fs=title_size("")[0],
    )


def note_color_index(input_path, cycle_len):
    """由笔记目录名稳定决定本篇用第几个颜色：
    数字前缀优先（001→0、002→1、003→2……），无前缀则用目录名的稳定哈希。
    同一篇永远同一色，重渲染不变。"""
    import hashlib
    d = Path(input_path).resolve().parent.name
    m = re.match(r"^(\d+)", d)
    if m:
        return (int(m.group(1)) - 1) % max(cycle_len, 1)
    return int(hashlib.md5(d.encode("utf-8")).hexdigest(), 16) % max(cycle_len, 1)


def note_css(style_key, color_idx, fs_override=None):
    """card 风格整篇统一一个背景色（由笔记目录名轮换决定）。其余风格单色。"""
    st = STYLES[style_key]
    cycle = st.get("bg_cycle")
    if not cycle:
        return css_for(style_key, fs_override)
    return css_for(style_key, fs_override, bg_override=cycle[color_idx % len(cycle)])


def title_size(title):
    n = max((len(x) for x in title.split("\n") if x), default=6)
    if n <= 8:
        return 112, 1.26
    if n <= 12:
        return 96, 1.3
    if n <= 16:
        return 82, 1.34
    return 68, 1.4


def cover_html(st, title, lead, kicker, author, css):
    lines = [l for l in title.split("\n") if l.strip()]
    n0 = len(lines[0]) if lines else 0
    # 封面主标题：信息流里要一眼读清，整体调大一档（短标题拉满到 120px）
    main = 120 if n0 <= 6 else 108 if n0 <= 8 else 96 if n0 <= 10 else 84 if n0 <= 13 else 68
    parts = []
    for i, l in enumerate(lines):
        if i == 0:
            parts.append(f'<div class="t-main" style="font-size:{main}px">{md_to_html(l)}</div>')
        else:
            fs = max(40, min(54, int(CONTENT_W / max(len(l), 1) * 0.96)))
            parts.append(f'<div class="t-sub" style="font-size:{fs}px">{md_to_html(l)}</div>')
    t = "".join(parts)
    kick = f'<div class="kicker"><i></i>{md_to_html(kicker)}</div>' if kicker else "<div></div>"
    lead_html = f'<div class="lead">{md_to_html(lead)}</div>' if lead else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body>
<div class="page"><div class="cover">
{kick}
<div class="cover-mid">{t}<div class="rule"></div>{lead_html}</div>
<div class="cover-foot"><span>{md_to_html(author)}</span><span>01</span></div>
</div></div></body></html>"""


def body_html(units, page_no, total, css, author):
    inner = render_units(units)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body>
<div class="page">
<div class="topline"><span class="pg">{page_no:02d} / {total:02d}</span></div>
<div class="content">{inner}</div>
<div class="foot"><span>{md_to_html(author)}</span><span class="bar"></span></div>
</div></body></html>"""


def end_html(closing, page_no, total, css, author, kicker):
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body>
<div class="page">
<div class="topline"><span class="pg">{page_no:02d} / {total:02d}</span></div>
<div class="content"><div class="end-mid">
<div class="end-tag">{md_to_html(kicker or '写在最后')}</div>
<div class="end-big">{md_to_html(closing)}</div>
</div></div>
<div class="foot"><span>{md_to_html(author)}</span><span class="bar"></span></div>
</div></body></html>"""


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="长文 markdown")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--style", default="card", choices=list(STYLES))
    ap.add_argument("--kicker", default="", help="封面左上小标签 / 结尾页小标签")
    ap.add_argument("--author", default="署名")
    ap.add_argument("--closing", default="", help="结尾页大字，留空则不生成结尾页")
    ap.add_argument("--lead", default="", help="覆盖封面导语")
    ap.add_argument("--title", default="", help="覆盖封面标题")
    ap.add_argument("--fs", type=int, default=None, help="正文字号，默认 40；调到 44-46 更易读但页数变多")
    args = ap.parse_args()

    raw = Path(args.input).read_text(encoding="utf-8")
    title, lead, blocks = parse_input(raw)
    title = args.title or title or "无标题"
    lead = args.lead or lead
    units = to_units(blocks)
    if not units:
        sys.exit("正文为空，检查输入文件格式")

    css = css_for(args.style, args.fs)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "_hi"
    tmp.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        mpage = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        pages = paginate(mpage, units, css)
        mpage.close()

        has_end = bool(args.closing)
        total = 1 + len(pages) + (1 if has_end else 0)

        shot = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=DSF)
        cycle = STYLES[args.style].get("bg_cycle") or [STYLES[args.style]["bg"]]
        color_idx = note_color_index(args.input, len(cycle))
        chosen_bg = cycle[color_idx % len(cycle)]
        css_note = note_css(args.style, color_idx, args.fs)
        jobs = [(1, cover_html(STYLES[args.style], title, lead, args.kicker, args.author, css_note))]
        for i, pgs in enumerate(pages, start=2):
            jobs.append((i, body_html(pgs, i, total, css_note, args.author)))
        if has_end:
            jobs.append((total, end_html(args.closing, total, total, css_note,
                                         args.author, args.kicker)))

        files = []
        for idx, html in jobs:
            shot.set_content(html)
            shot.wait_for_timeout(60)
            hi = tmp / f"p{idx:02d}.png"
            shot.screenshot(path=str(hi))
            im = Image.open(hi).convert("RGB")
            im = im.resize((W, H), Image.LANCZOS)
            dst = out / f"page-{idx:02d}.png"
            im.save(dst, "PNG", optimize=True)
            files.append(str(dst))
        shot.close()
        browser.close()

    for f in tmp.glob("*.png"):
        f.unlink()
    tmp.rmdir()

    meta = {
        "style": args.style, "title": title, "lead": lead, "bg": chosen_bg,
        "pages": len(files), "images": files, "out_dir": str(out),
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 生成 {len(files)} 页 -> {out}")
    for f in files:
        print("   ", Path(f).name)


if __name__ == "__main__":
    main()
