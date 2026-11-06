#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信公众号文章发布器（ego lite 浏览器版）。

流程：文章编辑器插入卡片图（正文竖排）+ 标题 + 作者 + 摘要 → 保存草稿 / 定时发表。
公众号每天只有 1 次群发（定时）额度，定时设置后可取消（恢复草稿，不占次数）。

用法（一般由 make.py --platform wechat 调用）：
    python3 wechat_publish.py --images ../notes/xxx/out --title 标题 \
        --digest 摘要 --author 七帅 [--schedule "2026-11-06 19:00" | --draft | --dry-run]
"""

import argparse
import datetime
import json
import subprocess
import sys
import threading
from pathlib import Path

PUBLISH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PUBLISH_DIR))
from publish import normalize_title, parse_schedule, resolve_images, validate_schedule_target  # noqa: E402

DRIVER = PUBLISH_DIR / "wechat_publish.mjs"
LOGS_DIR = PUBLISH_DIR / "logs"
RUN_TIMEOUT = 15 * 60
MAX_TITLE = 64
MAX_DIGEST = 120


def run_ego(cfg):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    script = "const CONFIG = %s;\n%s" % (json.dumps(cfg, ensure_ascii=False),
                                         DRIVER.read_text(encoding="utf-8"))
    proc = subprocess.Popen(["ego-browser", "nodejs"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    result_line = None

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
                        {k: v for k, v in d.items() if k != "ev"}, ensure_ascii=False)[:150]))
                except ValueError:
                    print(" ", line[:150])
            else:
                print(" ", line[:180])

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    try:
        proc.stdin.write(script)
        proc.stdin.close()
        proc.wait(timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise SystemExit("❌ ego-browser 执行超时")
    t.join(timeout=5)
    if result_line is None:
        raise SystemExit("❌ 没收到 EGO_RESULT（code=%s）" % proc.returncode)
    return json.loads(result_line)


def main():
    ap = argparse.ArgumentParser(description="公众号文章发布（ego lite）")
    ap.add_argument("--images", required=True, help="图片目录 / 逗号分隔 / 通配符")
    ap.add_argument("--title", required=True, help="标题（≤64 字）")
    ap.add_argument("--digest", default="", help="摘要（≤120 字，转发卡片展示；不填抓正文开头）")
    ap.add_argument("--author", default="七帅", help="作者名（≤8 字）")
    ap.add_argument("--schedule", default=None, help="定时发表 \"YYYY-MM-DD HH:MM\"（5分钟后~7天内）")
    ap.add_argument("--draft", action="store_true", help="只保存草稿（默认）")
    ap.add_argument("--dry-run", action="store_true", help="填好+保存草稿，不点任何发表按钮")
    args = ap.parse_args()

    images = resolve_images(args.images, str(PUBLISH_DIR))
    title = normalize_title(args.title)[:MAX_TITLE]
    digest = (args.digest or "")[:MAX_DIGEST]

    schedule_dt = parse_schedule(args.schedule) if args.schedule else None
    if schedule_dt:
        validate_schedule_target(schedule_dt)
        mode = "dry-run" if args.dry_run else "schedule"
    else:
        mode = "dry-run" if args.dry_run else "draft"

    now = datetime.datetime.now()
    schedule_day = None
    schedule_time = None
    if schedule_dt:
        delta = (schedule_dt.date() - now.date()).days
        if delta < 0 or delta > 6:
            raise SystemExit("❌ 公众号定时仅支持 5 分钟后 ~ 7 天内（你给的：%s）" % schedule_dt)
        schedule_day = "today" if delta == 0 else ("tomorrow" if delta == 1 else str(delta))
        schedule_time = schedule_dt.strftime("%H:%M")
        if delta == 0 and schedule_dt <= now + datetime.timedelta(minutes=5):
            raise SystemExit("❌ 定时时间至少在 5 分钟后")

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    cfg = {
        "mode": mode,
        "space_name": "wx-pub-%s" % stamp,
        "images": images,
        "title": title,
        "digest": digest,
        "author": args.author[:8],
        "schedule": schedule_dt.strftime("%Y-%m-%d %H:%M") if schedule_dt else None,
        "schedule_day": schedule_day,
        "schedule_time": schedule_time,
        "logs_dir": str(LOGS_DIR),
        "stamp": stamp,
    }
    print("=" * 60)
    print("公众号发布：%d 图 / 标题 %d 字 / 摘要 %d 字 / 模式 %s%s"
          % (len(images), len(title), len(digest), mode,
             (" / 定时 " + cfg["schedule"]) if cfg["schedule"] else ""))
    print("=" * 60)

    result = run_ego(cfg)

    report = {
        "stamp": stamp, "engine": "ego-browser", "platform": "wechat",
        "space": cfg["space_name"], "result": result.get("result"), "ok": result.get("ok"),
        "meta": {k: cfg[k] for k in ("mode", "title", "digest", "author", "schedule")},
        "images": [Path(p).name for p in images],
        "clicks": result.get("clicks", []),
        "verify": result.get("verify"),
        "error_step": result.get("error_step"), "error": result.get("error"),
    }
    report_path = LOGS_DIR / ("run-report-%s-wechat.json" % stamp)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n📄 运行报告：%s" % report_path)

    if result.get("error_step") == "login":
        print("\n❌ ego lite 未登录公众号。请先跑：python3 publish/wechat_login.py --qr")
        return 2
    if not result.get("ok"):
        print("\n❌ 失败（步骤 %s）：%s" % (result.get("error_step"), result.get("error")))
        return 1
    if mode == "schedule":
        print("\n✓ 已提交定时发表 %s（公众号到点自动群发）" % cfg["schedule"])
        print("  发表前可在 mp.weixin.qq.com → 发表记录 取消（恢复草稿，不占次数）")
    else:
        print("\n✓ 已保存草稿（未点任何发表按钮）。到 mp.weixin.qq.com → 内容管理 查看。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
