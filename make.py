#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一条命令：长文 -> 小红书图文 -> （可选）塞进草稿箱

用法：
  python3 make.py --new 003-选题名                             # 从模板新建一篇
  python3 make.py --dir notes/003-选题名                       # 只出图，不碰账号
  python3 make.py --dir notes/003-选题名 --schedule            # 出图 + 当天 19:31 定时发布
  python3 make.py --dir notes/003-选题名 --draft               # 出图 + 存草稿
  python3 make.py --dir notes/003-选题名 --schedule --headed   # 同上，能看到浏览器操作

目录约定：
  notes/001-消费转资产/
    long.md      长文本正文
    note.json    小红书那边的标题 / 正文 / 话题 / 风格

long.md 写法：
  # 封面主标题        可写多行，第一行最大，后续行降级为副标题
  > 封面导语          以 > 开头
  ## 正文小标题       渲染成带红色竖线的段标题
  普通段落            自动分页
  ==要高亮的话==      黄色荧光笔
  **加粗**            加粗
  - 列表项            破折号列表

note.json 字段：
  style      minimal / magazine / dark   （默认 minimal）
  kicker     封面左上角小标签，也是结尾页标签
  author     署名，note.json 里改
  closing    结尾页大字，可含换行；留空则不生成结尾页
  title      【小红书标题】≤20 字
  body       【小红书正文】可直接写，也可以用 "body_file": "body.txt"
  tags       【话题】数组，不带 # 号
  out        输出目录名，默认 out/<目录名>
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
RENDER = BASE / "render" / "render.py"
PUBLISH = BASE / "publish" / "publish.py"
PUBLISH_EGO = BASE / "publish" / "ego_publish.py"

PY = sys.executable


def load_note(note_dir: Path):
    cfg_path = note_dir / "note.json"
    if not cfg_path.exists():
        sys.exit(f"缺少 {cfg_path}（可以复制 notes/_template/note.json 改）")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    md = note_dir / cfg.get("md", "long.md")
    if not md.exists():
        sys.exit(f"缺少正文文件 {md}")

    body = cfg.get("body", "")
    if cfg.get("body_file"):
        body = (note_dir / cfg["body_file"]).read_text(encoding="utf-8").strip()

    title = cfg.get("title", "").strip()
    if not title:
        sys.exit("note.json 里没有 title（小红书标题）")
    if len(title) > 20:
        print(f"⚠️  标题 {len(title)} 字，小红书上限 20 字，建议压短：{title}")

    tags = cfg.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in tags.replace("#", "").split(",") if t.strip()]

    return md, body, title, tags, cfg


def resolve_schedule(arg, cfg):
    """--schedule 不传值或传 auto 时，取当天 19:31；已过则顺延到次日 19:31。"""
    from datetime import datetime, timedelta
    if not arg or arg == "auto":
        hh, mm = cfg.get("schedule_time", "19:31").split(":")
        t = datetime.now().replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if t <= datetime.now():
            t += timedelta(days=1)
        return t.strftime("%Y-%m-%d %H:%M")
    t = datetime.strptime(arg.strip(), "%Y-%m-%d %H:%M")
    if t <= datetime.now():
        sys.exit(f"✗ 定时时间 {arg} 已经过去，换一个将来的时间")
    return t.strftime("%Y-%m-%d %H:%M")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", help="一个笔记目录（含 long.md 和 note.json）")
    ap.add_argument("--new", default=None, metavar="名字", help="从模板新建一篇笔记目录，例如 --new 003-标题")
    ap.add_argument("--draft", action="store_true", help="渲染完直接存进小红书草稿箱")
    ap.add_argument("--headed", action="store_true", help="发布时打开浏览器窗口，看得见操作")
    ap.add_argument("--style", default=None, help="临时覆盖风格 minimal/magazine/dark")
    ap.add_argument("--keep", action="store_true", help="图片已存在时不重渲染")
    ap.add_argument("--dry-run", action="store_true", help="只试填，不点任何提交按钮")
    ap.add_argument("--browser", default="ego", choices=["ego", "chrome"],
                    help="发布用哪个浏览器：ego=ego lite（默认，人机共用登录态）；chrome=系统 Chrome+独立 .profile（旧方式）")
    ap.add_argument("--platform", default="xhs", choices=["xhs", "wechat"],
                    help="发布到哪个平台：xhs=小红书（默认）；wechat=微信公众号贴图（填好+存草稿）")
    ap.add_argument("--schedule", nargs="?", const="auto", default=None, metavar="时间",
                    help="定时发布，如 \"2026-11-05 19:31\"；不带值=当天 19:31（已过则顺延到次日）")
    args = ap.parse_args()

    if args.new:
        dst = BASE / "notes" / args.new
        if dst.exists():
            sys.exit(f"✗ 已存在：{dst}")
        shutil.copytree(BASE / "notes" / "_模板", dst)
        print(f"✓ 已从模板新建：{dst}")
        print("  1. 编辑 long.md 粘长文")
        print("  2. 编辑 note.json 填标题 / 正文 / 话题 / 风格")
        print(f"  3. python3 make.py --dir {dst} --draft")
        return
    if not args.dir:
        ap.error("要么用 --new 新建一篇，要么用 --dir 指定已有目录")

    note_dir = Path(args.dir).expanduser().resolve()
    if not note_dir.is_dir():
        sys.exit(f"目录不存在：{note_dir}")

    md, body, title, tags, cfg = load_note(note_dir)
    schedule_time = resolve_schedule(args.schedule, cfg)
    style = args.style or cfg.get("style", "card")
    out_dir = note_dir / cfg.get("out", "out")

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

    print("▶ 渲染图文…")
    r = subprocess.run(cmd, text=True, capture_output=True)
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        sys.exit("✗ 渲染失败")
    print(r.stdout.strip())

    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))

    pack = {
        "dir": str(note_dir),
        "style": style,
        "images": meta["images"],
        "pages": meta["pages"],
        "xhs_title": title,
        "xhs_body": body,
        "tags": tags,
    }
    (out_dir / "pack.json").write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n📦 打包完成：{meta['pages']} 页图 + 1 条文案 -> {out_dir}")
    print(f"   标题（{len(title)} 字）：{title}")
    print(f"   话题：{' '.join('#' + t for t in tags) if tags else '（无）'}")
    print(f"   正文：{len(body)} 字")

    if not args.draft and not args.dry_run and not args.schedule:
        print("\n（未加 --draft，到此为止，不碰账号）")
        return

    if not body:
        sys.exit("✗ note.json 里没有 body（小红书正文），无法填草稿")

    if args.platform == "wechat":
        # 贴图模式：填好 + 存草稿（公众号每天仅 1 次群发额度，发表动作留给用户）
        cmdp = [
            PY, str(BASE / "publish" / "wechat_publish.py"),
            "--images", str(out_dir),
            "--title", title,
            "--digest", body.strip()[:1000],
        ]
        if args.dry_run:
            cmdp.append("--dry-run")
        print("\n▶ 公众号贴图（填好 + 保存草稿）…")
        if args.schedule:
            print(f"  ℹ️ 贴图模式不自动发表；如需定时 {schedule_time}，到草稿箱手动设置")
        rp = subprocess.run(cmdp, text=True)
        if rp.returncode != 0:
            sys.exit("✗ 公众号贴图失败，看上面的报错和 publish/logs/ 里的截图")
        return

    if args.schedule:
        cmdp = [
            PY, str(PUBLISH_EGO if args.browser == "ego" else PUBLISH),
            "--meta", str(out_dir / "meta.json"),
            "--title", title,
            "--body", body,
            "--tags", ",".join(tags),
            "--schedule", schedule_time,
        ]
        if args.headed:
            cmdp.append("--headed")
        if args.dry_run:
            cmdp.append("--dry-run")
        print(f"\n▶ 定时发布（{schedule_time}）…")
        rp = subprocess.run(cmdp, text=True)
        if rp.returncode != 0:
            sys.exit("✗ 定时发布失败，看上面的报错和 publish/logs/ 里的截图")
        return

    cmdp = [
        PY, str(PUBLISH_EGO if args.browser == "ego" else PUBLISH),
        "--meta", str(out_dir / "meta.json"),
        "--title", title,
        "--body", body,
        "--tags", ",".join(tags),
        "--draft",
    ]
    if args.headed:
        cmdp.append("--headed")
    if args.dry_run:
        cmdp.append("--dry-run")

    print("\n▶ 填入小红书草稿箱…")
    rp = subprocess.run(cmdp, text=True)
    if rp.returncode != 0:
        sys.exit("✗ 填草稿失败，看上面的报错和 publish/logs/ 里的截图")


if __name__ == "__main__":
    main()
