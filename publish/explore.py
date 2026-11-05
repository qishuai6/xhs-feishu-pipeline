# -*- coding: utf-8 -*-
"""小红书创作平台 DOM 勘察脚本。

用途：小红书改版后重跑，dump 出「当前页面到底长什么样」——URL、按钮文案、
class、input 类型、二维码位置、可见文本。不要靠猜选择器。

用法：
    python3 explore.py                 # 无头，勘察首页（未登录会落到登录墙）
    python3 explore.py --headed        # 有头，人工扫码后继续勘察已登录状态
    python3 explore.py --url https://creator.xiaohongshu.com/publish/publish
    python3 explore.py --headed --keep-open

产物（logs/ 下）：
    explore-<时间戳>.html   页面完整 HTML
    explore-<时间戳>.png    截图
    explore-<时间戳>.json   结构化勘察结果（元素清单、文本、登录墙判断）
"""

import argparse
import json
import sys
import time
import datetime

from common import (
    CREATOR_HOME,
    LOGIN_URL,
    PUBLISH_URL,
    LOGS_DIR,
    get_logger,
    looks_like_login,
    nap,
    shot,
    chrome_context,
)

log = get_logger("explore")

# 在页面里跑的采集函数：只收可见元素，避免把整棵隐藏树搬出来
COLLECT_JS = r"""
() => {
  const pick = (el) => {
    const attrs = {};
    for (const name of ['type','name','id','placeholder','aria-label','role','href','contenteditable','accept','multiple']) {
      const v = el.getAttribute(name);
      if (v !== null && v !== '') attrs[name] = v;
    }
    const r = el.getBoundingClientRect();
    return {
      tag: el.tagName.toLowerCase(),
      text: (el.innerText || el.value || el.getAttribute('title') || '').trim().slice(0, 80),
      class: (el.className && String(el.className).trim().slice(0, 200)) || '',
      attrs,
      rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
    };
  };
  const out = {buttons: [], inputs: [], links: [], editables: [], images: [], canvases: [], big_text: []};
  document.querySelectorAll('button, [role=button]').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) out.buttons.push(pick(el));
  });
  document.querySelectorAll('input, textarea').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) out.inputs.push(pick(el));
  });
  document.querySelectorAll('a[href]').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) out.links.push(pick(el));
  });
  document.querySelectorAll('[contenteditable=true], [contenteditable=""]').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) out.editables.push(pick(el));
  });
  document.querySelectorAll('img').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 40 && r.height > 40) out.images.push(pick(el));
  });
  document.querySelectorAll('canvas').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) out.canvases.push(pick(el));
  });
  // 找页面里字号最大的几段文字，通常是标题/主按钮
  const seen = new Set();
  document.querySelectorAll('h1,h2,h3,div,span,p').forEach(el => {
    const r = el.getBoundingClientRect();
    const t = (el.innerText || '').trim();
    if (!t || t.length > 40 || r.width < 20 || r.height < 10 || seen.has(t)) return;
    const fs = parseFloat(getComputedStyle(el).fontSize) || 0;
    if (fs >= 16) { seen.add(t); out.big_text.push({text: t, fontSize: fs, tag: el.tagName.toLowerCase()}); }
  });
  out.big_text = out.big_text.slice(0, 40);
  return out;
}
"""


def dump_page(page, tag):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    info = {
        "tag": tag,
        "url": page.url,
        "title": page.title(),
        "is_login_wall": looks_like_login(page),
        "body_text": "",
        "elements": {},
        "frames": [],
    }
    try:
        info["body_text"] = page.inner_text("body")[:4000]
    except Exception as exc:  # noqa: BLE001
        info["body_text"] = "<读取 body 失败: %s>" % exc

    for frame in page.frames:
        try:
            data = frame.evaluate(COLLECT_JS)
            data["frame_url"] = frame.url
            data["_name"] = frame.name
            if frame == page.main_frame:
                info["elements"] = data
            else:
                info["frames"].append(data)
        except Exception as exc:  # noqa: BLE001
            info["frames"].append({"frame_url": frame.url, "error": str(exc)})

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    html_path = LOGS_DIR / ("explore-%s-%s.html" % (tag, stamp))
    json_path = LOGS_DIR / ("explore-%s-%s.json" % (tag, stamp))
    png_path = LOGS_DIR / ("explore-%s-%s.png" % (tag, stamp))
    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.warning("保存 HTML 失败: %s", exc)
    json_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        page.screenshot(path=str(png_path), full_page=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("截图失败: %s", exc)
    return info, {"html": str(html_path), "json": str(json_path), "png": str(png_path)}


def report(info, artifacts):
    print("\n" + "=" * 72)
    print("URL      :", info["url"])
    print("标题     :", info["title"])
    print("登录墙   :", "是（未登录，或停在扫码页）" if info["is_login_wall"] else "否")
    print("HTML     :", artifacts["html"])
    print("JSON     :", artifacts["json"])
    print("截图     :", artifacts["png"])
    print("=" * 72)

    el = info["elements"]
    print("\n-- 可见按钮 (%d) --" % len(el.get("buttons", [])))
    for b in el.get("buttons", []):
        print("  [%s]" % b["tag"], repr(b["text"])[:60], "| class=", b["class"][:80])
    print("\n-- 可见输入框 (%d) --" % len(el.get("inputs", [])))
    for i in el.get("inputs", []):
        print("  [%s]" % i["tag"], "type=%r" % i["attrs"].get("type"),
              "placeholder=%r" % i["attrs"].get("placeholder"),
              "accept=%r" % i["attrs"].get("accept"),
              "multiple=%r" % i["attrs"].get("multiple"),
              "| class=", i["class"][:80])
    print("\n-- contenteditable (%d) --" % len(el.get("editables", [])))
    for e in el.get("editables", []):
        print("  ", repr(e["text"])[:50], "| class=", e["class"][:100])
    print("\n-- 二维码 canvas / 图片 (%d/%d) --" % (len(el.get("canvases", [])), len(el.get("images", []))))
    for c in el.get("canvases", []):
        print("  canvas", c["rect"], "class=", c["class"][:80])
    for im in el.get("images", [])[:12]:
        print("  img", im["rect"], "class=", im["class"][:80])
    print("\n-- 大字文本（可能是标题/主按钮） --")
    for t in el.get("big_text", [])[:25]:
        print("  %.0fpx" % t["fontSize"], repr(t["text"])[:60])
    if info["frames"]:
        print("\n-- iframe 内元素 (%d 个 frame) --" % len(info["frames"]))
        for f in info["frames"]:
            print("  frame:", f.get("frame_url"))
    print("\n-- body 文本前 600 字 --")
    print(info["body_text"][:600])
    print()


def main():
    ap = argparse.ArgumentParser(description="小红书创作平台 DOM 勘察")
    ap.add_argument("--url", default=CREATOR_HOME, help="要勘察的 URL")
    ap.add_argument("--headed", action="store_true", help="有头模式（需要人工扫码/登录时用）")
    ap.add_argument("--keep-open", action="store_true", help="有头模式下采完保持打开，Ctrl+C 退出")
    ap.add_argument("--wait", type=float, default=5.0, help="页面加载后额外等待秒数（默认 5）")
    args = ap.parse_args()

    headless = not args.headed
    log.info("启动系统 Chrome（headless=%s）→ %s", headless, args.url)
    with chrome_context(headless=headless) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=30000)
        # 等页面稳定：网络空闲或超时都不致命
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001
            log.info("networkidle 超时，继续（小红书有长连接，正常）")
        time.sleep(args.wait)
        info, artifacts = dump_page(page, "initial")
        report(info, artifacts)

        if args.headed:
            print("\n>>> 如果当前在登录墙：请在弹出的 Chrome 窗口里扫码登录，登录完成后回来看第二份勘察。")
            print(">>> 登录成功后，本脚本会自动再采一次已登录状态（最多等 180 秒）。")
            try:
                for _ in range(180):
                    if not looks_like_login(page):
                        break
                    time.sleep(1)
                time.sleep(3.0)
                info2, artifacts2 = dump_page(page, "after-login")
                report(info2, artifacts2)
                print("\n>>> 再把发布页采一次：")
                try:
                    page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(5)
                    info3, artifacts3 = dump_page(page, "publish-page")
                    report(info3, artifacts3)
                except Exception as exc:  # noqa: BLE001
                    log.warning("发布页勘察失败: %s", exc)
            except KeyboardInterrupt:
                log.info("用户中断")
            if args.keep_open:
                print("\n>>> --keep-open：按 Ctrl+C 关闭浏览器")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    pass

    log.info("勘察完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
