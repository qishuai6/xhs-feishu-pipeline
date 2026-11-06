#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书图文笔记发布器 —— ego lite 浏览器版。

与 publish.py（Playwright + 系统 Chrome）CLI 兼容，但浏览器换成 ego lite：
登录态存在 ego lite 的用户配置里（人机共用一个浏览器），不再维护 publish/.profile。

用法（一般由 make.py 调用，也可单跑）：
    python3 ego_publish.py --meta ../notes/xxx/out --title 标题 --body body.txt \
        --tags "a,b" [--schedule "2026-11-06 19:31" | --draft | --publish] [--dry-run]

实现：把 CONFIG 注入 ego_publish.mjs 头部，经 stdin 交给 `ego-browser nodejs` 执行；
解析 EGO_RESULT JSON 行得到结构化结论，全程日志/截图仍写 publish/logs/。
"""

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

PUBLISH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PUBLISH_DIR))

from publish import (  # noqa: E402  复用输入解析/校验逻辑
    load_meta, normalize_tags, normalize_title, parse_schedule,
    read_body, resolve_images, validate_schedule_target,
)

DRIVER = PUBLISH_DIR / "ego_publish.mjs"
LOGS_DIR = PUBLISH_DIR / "logs"
RUN_TIMEOUT = 15 * 60


def build_config(args):
    script_dir = str(PUBLISH_DIR)
    meta = load_meta(args.meta, script_dir) if args.meta else {}

    if args.images:
        images = resolve_images(args.images, script_dir)
    elif meta.get("_images_resolved"):
        images = meta["_images_resolved"]
    else:
        raise SystemExit("❌ 必须给 --images（或 --meta 里带 images）")

    title = normalize_title(args.title or meta.get("title") or "")
    if not title:
        raise SystemExit("❌ 必须给 --title（或 --meta 里带 title）")
    body = read_body(args.body, script_dir) or (meta.get("body") or "")
    tags = normalize_tags(args.tags) or normalize_tags(meta.get("tags"))

    schedule_dt = parse_schedule(args.schedule) if args.schedule else None
    if schedule_dt is not None:
        schedule_dt = validate_schedule_target(schedule_dt)
        if args.publish:
            raise SystemExit("❌ --schedule 和 --publish 不能同时用")
        mode = "schedule-dry" if args.dry_run else "schedule"
    elif args.publish:
        mode = "publish"
    elif args.dry_run:
        mode = "dry-run"
    else:
        mode = "draft"

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return {
        "mode": mode.replace("-dry", "") if mode != "schedule-dry" else "dry-run",
        # schedule-dry 也走 dry-run 停在最后一步
        "space_name": "xhs-pub-%s" % stamp,
        "images": images,
        "title": title,
        "body": body,
        "tags": tags,
        "schedule": schedule_dt.strftime("%Y-%m-%d %H:%M") if schedule_dt else None,
        "logs_dir": str(LOGS_DIR),
        "stamp": stamp,
    }, meta


def run_ego(cfg):
    """把驱动 + CONFIG 拼成脚本，流式执行并解析 EGO_RESULT。"""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    script = "const CONFIG = %s;\n%s" % (json.dumps(cfg, ensure_ascii=False), DRIVER.read_text(encoding="utf-8"))

    proc = subprocess.Popen(
        ["ego-browser", "nodejs"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
    )
    result_line = None
    import threading

    def pump():
        nonlocal result_line
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            if line.startswith("EGO_RESULT "):
                result_line = line[len("EGO_RESULT "):]
                print("■ 驱动结论已返回")
            elif line.startswith("{") and '"ev"' in line:
                try:
                    d = json.loads(line)
                    print("  · %-14s %s" % (d.get("ev"), json.dumps(
                        {k: v for k, v in d.items() if k != "ev"}, ensure_ascii=False)[:160]))
                except ValueError:
                    print(" ", line[:160])
            else:
                print(" ", line[:200])

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    try:
        proc.stdin.write(script)
        proc.stdin.close()
        proc.wait(timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise SystemExit("❌ ego-browser 执行超时（%d 分钟）" % (RUN_TIMEOUT // 60))
    t.join(timeout=5)

    if result_line is None:
        raise SystemExit("❌ 没收到 EGO_RESULT（驱动异常退出，code=%s）。看上面的输出。" % proc.returncode)
    return json.loads(result_line)


def main():
    ap = argparse.ArgumentParser(description="小红书发布（ego lite 浏览器版）")
    ap.add_argument("--images", help="图片：目录 / 逗号分隔 / 通配符（或由 --meta 提供）")
    ap.add_argument("--title", help="标题（≤20 字）")
    ap.add_argument("--body", default="", help="正文文本或 .txt 路径")
    ap.add_argument("--tags", default="", help="话题，逗号分隔")
    ap.add_argument("--meta", default=None, help="render 产出的 meta.json")
    ap.add_argument("--draft", action="store_true", help="存草稿（默认）")
    ap.add_argument("--publish", action="store_true", help="立即发布（危险）")
    ap.add_argument("--schedule", default=None, help="定时发布 \"YYYY-MM-DD HH:MM\" 或 \"HH:MM\"")
    ap.add_argument("--dry-run", action="store_true", help="填好停在最后一步，不点任何提交按钮")
    ap.add_argument("--headed", action="store_true", help="兼容参数：ego lite 本来就是可见浏览器，忽略")
    args = ap.parse_args()

    cfg, _ = build_config(args)
    print("=" * 60)
    print("ego lite 发布：%d 图 / 标题 %d 字 / 正文 %d 字 / %d 话题 / 模式 %s"
          % (len(cfg["images"]), len(cfg["title"]), len(cfg["body"]), len(cfg["tags"]), cfg["mode"]))
    if cfg["schedule"]:
        print("定时：%s" % cfg["schedule"])
    print("=" * 60)

    result = run_ego(cfg)

    # 运行报告（与 publish.py 的 run-report 同目录同格式风格）
    report = {
        "stamp": cfg["stamp"],
        "engine": "ego-browser",
        "space": cfg["space_name"],
        "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "result": result.get("result"),
        "ok": result.get("ok"),
        "meta": {k: cfg[k] for k in ("mode", "title", "tags", "schedule")},
        "images": [Path(p).name for p in cfg["images"]],
        "tags_result": result.get("tags"),
        "schedule_result": result.get("schedule"),
        "verify": result.get("verify"),
        "buttons": result.get("buttons"),
        "clicks": result.get("clicks", []),
        "error_step": result.get("error_step"),
        "error": result.get("error"),
    }
    report_path = LOGS_DIR / ("run-report-%s-ego.json" % cfg["stamp"])
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n📄 运行报告：%s" % report_path)

    if result.get("error_step") == "login":
        print("\n❌ ego lite 未登录小红书。先跑：python3 publish/ego_login.py --qr")
        return 2
    if not result.get("ok"):
        print("\n❌ 失败（步骤 %s）：%s" % (result.get("error_step"), result.get("error")))
        print("   截图与过程见 publish/logs/")
        return 1

    mode = cfg["mode"]
    if mode == "dry-run":
        print("\n🧪 DRY-RUN：内容已填好，没有点击任何提交按钮（allow_submit 点击数 = 0）")
        audit = [c for c in result.get("clicks", []) if c.get("allow_submit")]
        assert not audit, "dry-run 出现了 allow_submit 点击！"
        print("✅ 安全断言通过")
    elif mode == "draft":
        print("\n✓ 已存入草稿箱（未点发布）。到 creator.xiaohongshu.com → 笔记管理 → 草稿箱 检查。")
    elif mode == "schedule":
        v = result.get("verify") or {}
        if v.get("found"):
            print("\n✓ 已提交定时发布 %s，并在「笔记管理→%s」确认到记录（时间 %r）"
                  % (cfg["schedule"], v.get("tab"), v.get("time")))
        else:
            print("\n⚠ 已点【定时发布】，但没在笔记管理验证到记录。请手动确认，确认前别重复发布。")
            return 1
    elif mode == "publish":
        print("\n✅ 已点击发布（立即发布）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
