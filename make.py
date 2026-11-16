#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条命令：稿子 → 卡片图 → 发布（桌面端入口）

三种用法
--------
1) 单文件（桌面端推荐）：一个 Markdown 文件，顶部 frontmatter 写平台参数

     python3 make.py --md inbox/稿子.md                  # 只出图，不碰账号
     python3 make.py --md inbox/稿子.md --schedule        # 小红书：当天 19:31 定时发布
     python3 make.py --md inbox/稿子.md --schedule 21:00  # 指定今天（已过则明天）21:00
     python3 make.py --md inbox/稿子.md --platform both   # 小红书定时 + 公众号草稿
     python3 make.py --md inbox/稿子.md --dry-run         # 空跑：填完停最后一步

2) 两文件（老用法，仍可用）

     python3 make.py --new 004-选题名                     # 从模板新建（不给名字=自动编号）
     # 编辑 notes/004-选题名/long.md（进图片）和 note.json（平台参数）
     python3 make.py --dir notes/004-选题名 --schedule

3) 只重渲染

     python3 make.py --dir notes/004-选题名 --keep

单文件格式
----------
    ---
    title: 小红书标题（平台口径 ≤20 字，必填）
    body: |
      小红书正文，也用作公众号描述。平台口径 ≤980 字。
      这里可以多段。

    tags: [复利思维, 长期主义]
    kicker: AI 与内容 · 004        # 可选：封面左上角小标签
    author: 七帅                   # 可选
    closing: |                     # 可选：结尾页大字
      多做有复利的事
    platform: both                 # 可选：xhs / wechat / both
    schedule: auto                 # 可选：auto=当天 19:31；或 "21:00" / "2026-11-18 09:00"
    style: card                    # 可选，固定 card
    ---

    # 封面主标题
    > 封面导语
    正文长文（进图片）……

长文写法
--------
  # 封面主标题        可写多行，第一行最大，后续行降级为副标题
  > 封面导语          以 > 开头
  ## 正文小标题       渲染成带红色竖线的段标题
  普通段落            自动分页
  ==要高亮的话==      黄色荧光笔
  **加粗**
  - 列表项
  Markdown 表格       渲染成带表头底色的可视化表格

注意
----
  - 平台字数按 UTF-16 + 组合 emoji 口径预检，超限直接拦下，不静默截断
  - 公众号「贴图」止步于保存草稿，发表按钮永远留给用户
  - 每次发布写运行报告到 publish/logs/，并汇总成 out/receipt.json
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
NOTES = BASE / "notes"
RENDER = BASE / "render" / "render.py"
PUBLISH_CHROME = BASE / "publish" / "publish.py"
PUBLISH_EGO = BASE / "publish" / "ego_publish.py"
PUBLISH_WECHAT = BASE / "publish" / "wechat_publish.py"
LOGS = BASE / "publish" / "logs"

sys.path.insert(0, str(BASE / "publish"))
from textlen import platform_len  # noqa: E402

PY = sys.executable
MAX_TITLE = 20
MAX_BODY = 1000
BODY_SAFE = 980          # 预检线：留 20 字余量，避免平台二次校验把稿子拦下
SLUG_MAX = 12


# ---------------------------------------------------------------- 小工具

def rel(path):
    """打印用：尽量显示相对流水线目录的路径。"""
    try:
        return str(Path(path).resolve().relative_to(BASE))
    except (ValueError, OSError):
        return str(path)


def next_number():
    """notes/ 里下一个没占用的三位序号（决定卡片配色轮换，001 蓝→002 绿→003 红→004 黑）。"""
    nums = []
    if NOTES.is_dir():
        for p in NOTES.iterdir():
            m = re.match(r"^(\d{3,})", p.name)
            if p.is_dir() and m:
                nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def slugify(title):
    """标题 → 目录名用的短 slug：去标点、限长，保留中文。"""
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", title or "").strip("-")
    s = s[:SLUG_MAX].strip("-")
    return s or "未命名"


# ---------------------------------------------------------------- frontmatter

def split_frontmatter(raw):
    """拆出文首 YAML frontmatter，返回 (元数据 dict, 正文)。

    只支持够用的子集：key: value、key: [a, b]、key: | 多行块、引号字符串。
    没有 frontmatter 时返回 ({}, 原文)，老格式不受影响。
    """
    lines = (raw or "").replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, raw
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return parse_frontmatter(lines[1:i]), "\n".join(lines[i + 1:])
    return {}, raw


def parse_frontmatter(lines):
    data, key, block = {}, None, None

    def flush():
        nonlocal key, block
        if key is not None:
            data[key] = "\n".join(block).strip("\n")
            key, block = None, None

    for ln in lines:
        if block is not None:
            if ln.startswith("  ") or not ln.strip():
                block.append(ln[2:] if ln.startswith("  ") else "")
                continue
            flush()
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", ln)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        if v in ("|", ">"):
            key, block = k, []
        else:
            data[k] = scalar(v)
    flush()
    return data


def scalar(value):
    v = (value or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
    return v


def as_tags(value):
    """话题：支持 ["a","b"] 列表或 "a, b" / "#a #b" 字符串。"""
    if not value:
        return []
    if isinstance(value, str):
        items = re.split(r"[,\s]+", value.replace("#", ""))
    else:
        items = list(value)
    return [str(t).strip().lstrip("#") for t in items if str(t).strip()]


# ---------------------------------------------------------------- 输入

def load_note(note_dir):
    """读 notes/<目录>：返回 (long.md 路径, body, title, tags, note.json)。"""
    cfg_path = note_dir / "note.json"
    if not cfg_path.exists():
        sys.exit("✗ 缺少 %s（可用 python3 make.py --new 建一篇）" % rel(cfg_path))
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    md = note_dir / cfg.get("md", "long.md")
    if not md.exists():
        sys.exit("✗ 缺少正文文件 %s" % rel(md))

    body = cfg.get("body", "")
    if cfg.get("body_file"):
        body = (note_dir / cfg["body_file"]).read_text(encoding="utf-8").strip()

    title = (cfg.get("title") or "").strip()
    if not title:
        sys.exit("✗ note.json 里没有 title（小红书/公众号标题）")

    return md, body, title, as_tags(cfg.get("tags")), cfg


def import_md(path, args):
    """单文件 → notes/NNN-slug/（long.md + note.json），返回 (目录, frontmatter)。"""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        sys.exit("✗ 找不到稿子：%s" % p)

    fm, body_md = split_frontmatter(p.read_text(encoding="utf-8"))
    if not body_md.strip():
        sys.exit("✗ %s 只有 frontmatter，没有正文（图片需要正文）" % p)

    title = (args.title or fm.get("title") or "").strip()
    if not title:
        sys.exit("✗ 单文件模式要在 frontmatter 写 title: …（或命令行给 --title）")

    body = fm.get("body") or ""
    if fm.get("body_file"):
        bf = (p.parent / fm["body_file"]).resolve()
        if not bf.is_file():
            sys.exit("✗ body_file 不存在：%s" % bf)
        body = bf.read_text(encoding="utf-8")
    body = body.strip()

    num = next_number()
    dst = NOTES / ("%03d-%s" % (num, slugify(title)))
    if dst.exists():          # 同名同序号：加时间后缀，不动已有目录
        dst = NOTES / ("%03d-%s-%s" % (num, slugify(title), datetime.now().strftime("%H%M")))
    dst.mkdir(parents=True)

    (dst / "long.md").write_text(body_md.strip() + "\n", encoding="utf-8")
    cfg = {
        "md": "long.md",
        "style": fm.get("style") or "card",
        "title": title,
        "body": body,
        "tags": as_tags(fm.get("tags")),
    }
    for k in ("kicker", "author", "closing", "cover_title", "lead",
              "schedule_time", "out", "platform", "schedule"):
        if fm.get(k):
            cfg[k] = fm[k]
    (dst / "note.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("✓ 已导入单文件稿：%s" % rel(dst))
    return dst, fm


def create_note(name):
    """从 notes/模板 复制一篇空笔记。name 为空或没有数字前缀时自动编号。"""
    tpl = NOTES / "模板"
    if not tpl.is_dir() and (NOTES / "_模板").is_dir():   # 兼容历史仓库里叫 _模板 的目录
        tpl = NOTES / "_模板"
    if not tpl.is_dir():
        sys.exit("✗ 找不到模板目录 notes/模板")

    if not name:
        name = "未命名"
    if not re.match(r"^\d{3,}", name):
        name = "%03d-%s" % (next_number(), name)
    dst = NOTES / name
    if dst.exists():
        sys.exit("✗ 已存在：%s" % rel(dst))

    shutil.copytree(tpl, dst)
    print("✓ 已从模板新建：%s" % rel(dst))
    print("  1. 编辑 long.md 粘长文（进图片）")
    print("  2. 编辑 note.json 填 title / body / tags / style")
    print("  3. python3 make.py --dir %s --schedule" % rel(dst))


# ---------------------------------------------------------------- 预检

def resolve_schedule(arg, cfg):
    """定时时间：auto=当天 HH:MM（已过顺延次日）；也接受 HH:MM 与 YYYY-MM-DD HH:MM。"""
    now = datetime.now()
    raw = (arg or "").strip()

    if not raw or raw == "auto":
        hh, mm = (cfg.get("schedule_time") or "19:31").split(":")
        t = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if t <= now:
            t += timedelta(days=1)
        return t.strftime("%Y-%m-%d %H:%M")

    m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if m:
        t = now.replace(hour=int(m.group(1)), minute=int(m.group(2)),
                        second=0, microsecond=0)
        if t <= now:
            t += timedelta(days=1)
        return t.strftime("%Y-%m-%d %H:%M")

    try:
        t = datetime.strptime(raw, "%Y-%m-%d %H:%M")
    except ValueError:
        sys.exit('✗ 定时时间看不懂：%r（支持 "21:00"、"2026-11-18 21:00"、auto）' % raw)
    if t <= now:
        sys.exit("✗ 定时 %s 已经过去，换一个将来的时间" % raw)
    return t.strftime("%Y-%m-%d %H:%M")


def check_copy(title, body):
    """平台口径预检：超限直接停，绝不静默截断（截断后发出去的不是你写的）。"""
    n_title = platform_len(title)
    if n_title > MAX_TITLE:
        sys.exit("✗ 标题 %d 字（平台口径，上限 %d）：%s\n  压短后再发。" % (n_title, MAX_TITLE, title))

    n_body = platform_len(body)
    if n_body > BODY_SAFE:
        sys.exit("✗ 正文超长：平台口径 %d 字（上限 %d，预检线 %d）。\n"
                 "  平台按 UTF-16 + 组合 emoji 计数，肉眼字数会低估。" % (n_body, MAX_BODY, BODY_SAFE))
    return n_title, n_body


# ---------------------------------------------------------------- 渲染

def render_note(note_dir, cfg, style, keep=False):
    md = note_dir / cfg.get("md", "long.md")
    out_dir = note_dir / cfg.get("out", "out")

    if keep and (out_dir / "meta.json").is_file():
        meta0 = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
        if meta0.get("images") and all(Path(p).is_file() for p in meta0["images"]):
            print("✓ 图片已存在，跳过渲染（--keep）：%s" % rel(out_dir))
            return out_dir, meta0

    cmd = [
        PY, str(RENDER),
        "--input", str(md),
        "--out", str(out_dir),
        "--style", style,
        "--author", cfg.get("author", "署名"),
    ]
    if cfg.get("kicker"):
        cmd += ["--kicker", cfg["kicker"]]
    if cfg.get("closing"):
        cmd += ["--closing", cfg["closing"]]
    if cfg.get("lead"):
        cmd += ["--lead", cfg["lead"]]
    if cfg.get("cover_title"):
        cmd += ["--title", cfg["cover_title"]]

    print("▶ 渲染卡片图…")
    r = subprocess.run(cmd, text=True, capture_output=True)
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        sys.exit("✗ 渲染失败（检查 long.md 是否为空、Chrome 是否可用）")
    print(r.stdout.strip())

    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    return out_dir, meta


def write_pack(note_dir, out_dir, style, meta, title, body, tags):
    """给下游驱动用的打包清单（保留历史字段，外部脚本可能在读）。"""
    pack = {
        "dir": str(note_dir),
        "style": style,
        "images": meta["images"],
        "pages": meta["pages"],
        "xhs_title": title,
        "xhs_body": body,
        "tags": tags,
    }
    (out_dir / "pack.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 发布

def _reports(kind):
    pattern = "run-report-*-ego.json" if kind == "xhs" else "run-report-*-wechat.json"
    try:
        return set(LOGS.glob(pattern))
    except OSError:
        return set()


def _newest_report(kind, before):
    """只认本次运行新写的报告，避免把上一次的旧报告当成这次的结果。"""
    new = _reports(kind) - before
    if not new:
        return None
    return max(new, key=lambda p: p.stat().st_mtime)


def publish_xhs(out_dir, title, body, tags, schedule_time, args):
    if args.dry_run:
        what = "空跑"
    elif schedule_time:
        what = "定时发布 " + schedule_time
    else:
        what = "存草稿"
    print("\n▶ 小红书：%s…" % what)
    cmd = [
        PY, str(PUBLISH_EGO if args.browser == "ego" else PUBLISH_CHROME),
        "--meta", str(out_dir / "meta.json"),
        "--title", title,
        "--body", body,
        "--tags", ",".join(tags),
    ]
    if schedule_time:
        cmd += ["--schedule", schedule_time]
    elif args.draft:
        cmd.append("--draft")
    if args.dry_run:
        cmd.append("--dry-run")
    if args.headed:
        cmd.append("--headed")
    before = _reports("xhs")
    rc = subprocess.run(cmd).returncode
    return rc, _newest_report("xhs", before)


def publish_wechat(out_dir, title, body, args):
    print("\n▶ 公众号贴图：%s…" % ("空跑" if args.dry_run else "填好 + 保存草稿"))
    cmd = [
        PY, str(PUBLISH_WECHAT),
        "--images", str(out_dir),
        "--title", title,
        "--digest", body,          # 截断由 wechat_publish 按平台口径智能处理
    ]
    if args.dry_run:
        cmd.append("--dry-run")
    before = _reports("wechat")
    rc = subprocess.run(cmd).returncode
    return rc, _newest_report("wechat", before)


def describe(kind, rc, report_path):
    """把退出码 + 运行报告压成一句人话。"""
    rep = {}
    if report_path and Path(report_path).is_file():
        try:
            rep = json.loads(Path(report_path).read_text(encoding="utf-8"))
        except (ValueError, OSError):
            rep = {}
    meta = rep.get("meta") or {}
    ok = (rc == 0) and bool(rep) and bool(rep.get("ok"))

    if not ok:
        if not rep:
            err = "没拿到驱动结论（看上方输出：权限弹框/人工接管/超时都会这样）"
        else:
            err = rep.get("error") or "驱动返回非零（看运行报告与截图）"
        step = rep.get("error_step")
        return False, "❌ 失败%s：%s" % ("（步骤 %s）" % step if step else "", err)

    if kind == "xhs":
        if meta.get("mode") == "dry-run":
            return True, "🧪 空跑：内容已填好，未点任何提交按钮"
        if meta.get("mode") == "schedule":
            v = rep.get("verify") or {}
            if v.get("found"):
                return True, "✅ 定时发布 %s（已回读验证）" % meta.get("schedule")
            return True, "⚠️ 已点定时发布 %s，但没回读到记录，请手动确认" % meta.get("schedule")
        return True, "✅ 草稿已保存（未点发布）"

    if meta.get("mode") == "dry-run":
        return True, "🧪 空跑：内容已填好，未点任何提交按钮"
    return True, "✅ 贴图草稿已保存（未发表，发表由你手动点）"


def print_receipt(ctx):
    line = "═" * 46
    print("\n" + line)
    print(" 发布回执")
    print(line)
    print(" 标题   %s（%d/%d 字）" % (ctx["title"], ctx["n_title"], MAX_TITLE))
    print(" 正文   %d 字（平台口径 %d，预检线 %d）" % (len(ctx["body"]), ctx["n_body"], BODY_SAFE))
    print(" 图片   %d 张 → %s" % (ctx["pages"], rel(ctx["out_dir"])))
    for kind, label in (("xhs", "小红书"), ("wechat", "公众号")):
        item = ctx["platforms"].get(kind)
        if not item:
            continue
        print(" %s %s" % (label, item["detail"]))
        if item["report"]:
            print("        报告 %s" % rel(item["report"]))
    print(line)

    receipt = {
        "title": ctx["title"],
        "title_len": ctx["n_title"],
        "body_len": ctx["n_body"],
        "body_len_raw": len(ctx["body"]),
        "pages": ctx["pages"],
        "images": [rel(p) for p in ctx["images"]],
        "out_dir": rel(ctx["out_dir"]),
        "style": ctx["style"],
        "dry_run": ctx["dry_run"],
        "platforms": ctx["platforms"],
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    path = Path(ctx["out_dir"]) / "receipt.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(" 回执 %s" % rel(path))
    return receipt


# ---------------------------------------------------------------- 主流程

def main():
    # 输出被管道捕获时也按行刷新，保证「渲染 → 发布 → 回执」的顺序和终端看到的一致
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    ap = argparse.ArgumentParser(
        description="稿子 → 卡片图 → 小红书定时发布 / 公众号贴图草稿（桌面端入口）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("--md", metavar="文件", help="单文件导入：YAML frontmatter + Markdown 正文，自动建 notes/NNN-标题")
    ap.add_argument("--dir", help="已有笔记目录（含 long.md 和 note.json）")
    ap.add_argument("--new", nargs="?", const="", default=None, metavar="名字",
                    help="从模板新建一篇；省略名字时自动编号（如 005-未命名）")
    ap.add_argument("--draft", action="store_true", help="小红书：存储草稿（不点发布）")
    ap.add_argument("--headed", action="store_true", help="兼容参数：ego lite 本来就可见，忽略")
    ap.add_argument("--style", default=None, help="临时覆盖风格（默认 card）")
    ap.add_argument("--keep", action="store_true", help="配图已存在时跳过渲染")
    ap.add_argument("--dry-run", action="store_true", help="只试填，不点任何提交按钮")
    ap.add_argument("--browser", default="ego", choices=["ego", "chrome"],
                    help="发布用哪个浏览器：ego=ego lite（默认）；chrome=系统 Chrome + 独立 .profile（旧方式）")
    ap.add_argument("--platform", default=None, choices=["xhs", "wechat", "both"],
                    help="发到哪：xhs=小红书（默认）；wechat=公众号贴图草稿；both=两个都发")
    ap.add_argument("--schedule", nargs="?", const="auto", default=None, metavar="时间",
                    help='小红书定时发布："21:00" / "2026-11-18 21:00" / auto=当天 19:31（已过顺延次日）')
    ap.add_argument("--title", default=None, help="覆盖标题（单文件模式用）")
    args = ap.parse_args()

    # 1) 新建空笔记
    if args.new is not None:
        create_note(args.new)
        return

    # 2) 定位笔记目录
    fm = {}
    if args.md and args.dir:
        ap.error("--md 和 --dir 只能给一个")
    if args.md:
        note_dir, fm = import_md(args.md, args)
    elif args.dir:
        note_dir = Path(args.dir).expanduser().resolve()
        if not note_dir.is_dir():
            sys.exit("✗ 目录不存在：%s" % note_dir)
    else:
        ap.error("三选一：--md 单文件 / --dir 已有目录 / --new 新建")

    md, body, title, tags, cfg = load_note(note_dir)
    style = args.style or cfg.get("style", "card")

    # 发布动作只能由命令行显式触发（--schedule / --draft / --dry-run）。
    # frontmatter 的 platform / schedule 只是默认值：没给动作就只渲染，绝不碰账号。
    wants_send = bool(args.schedule is not None or args.draft or args.dry_run)
    platform = args.platform or ("xhs" if not wants_send
                                 else (fm.get("platform") or cfg.get("platform") or "xhs"))

    schedule_time = None
    if args.schedule is not None:
        raw = args.schedule
        if raw == "auto":     # 命令行没指定具体时间：优先用稿子/note.json 里的约定
            raw = fm.get("schedule") or cfg.get("schedule") or "auto"
        schedule_time = resolve_schedule(raw, cfg)

    # 3) 渲染
    out_dir, meta = render_note(note_dir, cfg, style, keep=args.keep)
    write_pack(note_dir, out_dir, style, meta, title, body, tags)

    print("\n📦 打包完成：%d 页图 + 1 条文案 → %s" % (meta["pages"], rel(out_dir)))
    print("   标题（%d 字）：%s" % (len(title), title))
    print("   话题：%s" % (" ".join("#" + t for t in tags) if tags else "（无）"))
    print("   正文：%d 字" % len(body))

    # 4) 没给发布动作：到此为止
    if not wants_send:
        print("\n（未指定发布动作，到此为止，不碰账号）")
        print("  小红书定时：--schedule [时间]    公众号草稿：--platform wechat    两个都发：--platform both")
        return

    if not body:
        sys.exit("✗ 没有正文（note.json 的 body 或 frontmatter 的 body），无法发布")

    n_title, n_body = check_copy(title, body)
    if args.dry_run:
        print("\n🧪 DRY-RUN：只填表，点 0 次提交按钮")

    # 5) 发布（both 时单平台失败不拦另一个，最后汇总退出码）
    platforms = {}
    codes = []

    if platform in ("xhs", "both"):
        rc, report = publish_xhs(out_dir, title, body, tags, schedule_time, args)
        ok, detail = describe("xhs", rc, report)
        platforms["xhs"] = {
            "ok": ok, "detail": detail,
            "schedule": schedule_time,
            "report": rel(report) if report else "",
        }
        codes.append(rc)

    if platform in ("wechat", "both"):
        if schedule_time:
            print("\n  ℹ️ 公众号贴图不支持自动定时；本次只存草稿，定时/发表到草稿箱手动设置")
        rc, report = publish_wechat(out_dir, title, body, args)
        ok, detail = describe("wechat", rc, report)
        platforms["wechat"] = {
            "ok": ok, "detail": detail,
            "report": rel(report) if report else "",
        }
        codes.append(rc)

    # 6) 回执
    print_receipt({
        "title": title, "body": body, "n_title": n_title, "n_body": n_body,
        "pages": meta["pages"], "images": meta["images"], "out_dir": out_dir,
        "style": style, "dry_run": args.dry_run, "platforms": platforms,
    })

    if any(c != 0 for c in codes):
        sys.exit(1)


if __name__ == "__main__":
    main()
