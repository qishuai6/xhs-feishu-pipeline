#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信公众号「贴图」发布器（ego lite 浏览器版）。

流程：新建贴图 → 图片上传（首张直传+后续入素材库勾选）→ 标题(≤20字) + 描述(≤1000字)
→ 保存草稿。按用户需求：填好即存草稿，不自动发表（公众号每天仅 1 次群发额度）。

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
MAX_TITLE = 20          # 贴图模式标题上限（同小红书）
MAX_DIGEST = 1000        # 贴图描述上限


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
    ap.add_argument("--author", default="署名", help="兼容参数：贴图模式无作者字段，忽略")
    ap.add_argument("--schedule", default=None, help="定时发表 \"YYYY-MM-DD HH:MM\"（5分钟后~7天内）")
    ap.add_argument("--draft", action="store_true", help="只保存草稿（默认）")
    ap.add_argument("--dry-run", action="store_true", help="填好+保存草稿，不点任何发表按钮")
    args = ap.parse_args()

    images = resolve_images(args.images, str(PUBLISH_DIR))
    # 复制到临时目录并加唯一前缀：素材库按文件名勾选，必须避免与历史图同名
    import shutil
    up_dir = Path("/tmp") / ("wx-upload-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
    up_dir.mkdir(parents=True, exist_ok=True)
    uniq_images = []
    for i, src in enumerate(images, 1):
        dst = up_dir / ("wx%s-%02d-%s" % (datetime.datetime.now().strftime("%H%M%S"), i, Path(src).name))
        shutil.copy2(src, dst)
        uniq_images.append(str(dst))
    images = uniq_images
    title = normalize_title(args.title)[:MAX_TITLE]
    digest = (args.digest or "")[:MAX_DIGEST]

    if args.schedule:
        raise SystemExit("❌ 贴图模式当前只支持「填好+存草稿」，不支持自动定时发表。\n"
                         "   （公众号每天仅 1 次群发额度，定时请到草稿箱手动操作；需要自动定时时说一声）")
    mode = "dry-run" if args.dry_run else "draft"

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    cfg = {
        "mode": mode,
        "space_name": "wx-pub-%s" % stamp,
        "images": images,
        "title": title,
        "digest": digest,
        "author": args.author[:8],
        "logs_dir": str(LOGS_DIR),
        "stamp": stamp,
    }
    print("=" * 60)
    print("公众号贴图：%d 图 / 标题 %d 字 / 描述 %d 字 / 模式 %s"
          % (len(images), len(title), len(digest), mode))
    print("=" * 60)

    result = run_ego(cfg)

    report = {
        "stamp": stamp, "engine": "ego-browser", "platform": "wechat",
        "space": cfg["space_name"], "result": result.get("result"), "ok": result.get("ok"),
        "meta": {k: cfg[k] for k in ("mode", "title", "digest", "author")},
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
    print("\n✓ 贴图已填好并保存草稿（未点任何发表按钮）。")
    print("  到 mp.weixin.qq.com → 内容管理 → 草稿 检查，由你决定何时发表。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
