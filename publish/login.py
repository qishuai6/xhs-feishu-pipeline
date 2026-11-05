# -*- coding: utf-8 -*-
"""小红书创作平台：扫码登录（含自动刷新二维码）+ 登录态检测 + 登录后发布页 DOM dump。

登录态保存在 xhs-auto/publish/.profile（独立 Chrome profile，不碰用户日常浏览器）。
首次跑一次扫码，之后 publish.py 直接复用。

用法：
    # 【远程扫码】一条命令出干净二维码，二维码失效自动刷新，最长等 15 分钟
    python3 login.py --qr

    # 二维码固定存 logs/qr-login.png；刷新时覆盖同一文件并打印 [HH:MM:SS] 日志
    python3 login.py --qr --timeout 900 --refresh 110

    # 本机有人：弹出 Chrome 窗口直接扫码
    python3 login.py --headed

    # 只看当前登录态（不弹二维码）
    python3 login.py --check

退出码：0 已登录 / 2 未登录（--check）/ 1 出错或超时
"""

import argparse
import base64
import datetime
import sys
import time
from pathlib import Path

from common import (
    LOGIN_URL,
    LOGS_DIR,
    PUBLISH_URL,
    chrome_context,
    get_logger,
    nap,
    shot,
)

log = get_logger("login")

QR_TEXT = "APP扫一扫登录"           # 切到扫码态后必然出现
QR_FILE = LOGS_DIR / "qr-login.png"  # 固定路径，Lead/用户按这个路径取图

# 页面上找二维码图片（登录卡片里那张 196x196 的 base64 图）
QR_IMG_JS = """
() => {
  const imgs = Array.from(document.querySelectorAll('img'));
  const qr = imgs.find(i => (i.className || '').indexOf('css-1lhmg90') >= 0)
          || imgs.find(i => i.naturalWidth >= 150 && i.naturalHeight >= 150
                            && (i.src || '').startsWith('data:image'));
  if (!qr) return null;
  const r = qr.getBoundingClientRect();
  return {cls: String(qr.className), w: Math.round(r.width), h: Math.round(r.height),
          x: Math.round(r.x), y: Math.round(r.y), nw: qr.naturalWidth, nh: qr.naturalHeight};
}
"""

# 发布页可交互元素清单（登录后 dump 用）
ELEMENTS_JS = """
() => {
  const out = [];
  const sel = 'button, [role=button], input, textarea, [contenteditable], a[href], [class*=upload], [class*=cover], [class*=drag]';
  document.querySelectorAll(sel).forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    const attrs = {};
    for (const n of ['type','name','placeholder','accept','multiple','role','aria-label','href','contenteditable','id']) {
      const v = el.getAttribute(n);
      if (v !== null && v !== '') attrs[n] = String(v).slice(0, 120);
    }
    out.push({tag: el.tagName.toLowerCase(),
              text: (el.innerText || el.value || el.getAttribute('title') || '').trim().slice(0, 100),
              cls: (el.className && String(el.className).trim()) || '',
              attrs, rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}});
  });
  return out;
}
"""


def now_ts():
    return datetime.datetime.now().strftime("%H:%M:%S")


def page_has(page, text, timeout=1500):
    try:
        return page.get_by_text(text, exact=False).count() > 0
    except Exception:  # noqa: BLE001
        return False


def body_text(page):
    try:
        return page.inner_text("body")
    except Exception:  # noqa: BLE001
        return ""


def wait_login_card(page, timeout=20):
    """等登录卡片渲染完（二维码切换图标 or 短信登录文案出现）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if page.locator("img.css-wemwzq").count() or page_has(page, "短信登录") \
                or page_has(page, QR_TEXT):
            return True
        time.sleep(0.4)
    return False


def switch_to_qr(page):
    """切到扫码态。成功后断言出现「APP扫一扫登录」，失败重试一次。"""
    for attempt in (1, 2):
        if page_has(page, QR_TEXT):
            log.info("已经是扫码态")
            return True
        log.info("第 %d 次切换到扫码登录…", attempt)
        try:
            # 用选择器，不靠坐标：登录卡片右上角那个二维码图标
            toggle = page.locator("img.css-wemwzq").first
            if not toggle.count():
                toggle = page.locator("img[src^='data:image']").first
            toggle.click(timeout=8000)
        except Exception as exc:  # noqa: BLE001
            log.warning("点击二维码图标失败：%s", exc)
            try:
                page.reload(wait_until="domcontentloaded")
                nap(2.0, 3.0)
            except Exception:  # noqa: BLE001
                return False
            continue
        # 断言切换成功
        for _ in range(12):
            if page_has(page, QR_TEXT):
                log.info("已切到扫码登录（断言通过）")
                return True
            time.sleep(0.4)
        log.warning("点击后没看到「%s」，重试", QR_TEXT)
    return False


def png_size(path):
    """纯标准库读 PNG 宽高（16 字节处 IHDR），避免依赖 Pillow。"""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return (int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big"))
    except Exception:  # noqa: BLE001
        return None


def wait_qr_ready(page, timeout=15):
    """等二维码图片真正渲染出来（有尺寸、原图够大、可见）。"""
    deadline = time.time() + timeout
    info = None
    while time.time() < deadline:
        try:
            info = page.evaluate(QR_IMG_JS)
        except Exception:  # noqa: BLE001
            info = None
        if info and info.get("w", 0) >= 150 and info.get("h", 0) >= 150 \
                and info.get("nw", 0) >= 150:
            return info
        time.sleep(0.4)
    return info


def extract_qr_bitmap(page, out):
    """直接把 <img> 的 data:image/png;base64 解码成文件，拿到原生像素尺寸的二维码。"""
    src = page.evaluate("""
    () => {
      const imgs = Array.from(document.querySelectorAll('img'));
      const qr = imgs.find(i => (i.className || '').indexOf('css-1lhmg90') >= 0
                     && (i.src || '').startsWith('data:image'));
      const alt = qr || imgs.find(i => i.naturalWidth >= 150 && i.naturalHeight >= 150
                     && (i.src || '').startsWith('data:image'));
      return alt ? alt.src : null;
    }
    """)
    if not src or "base64," not in src:
        return None
    raw = base64.b64decode(src.split("base64,", 1)[1])
    Path(out).write_bytes(raw)
    size = png_size(out)
    if size and size[0] >= 150 and size[1] >= 150:
        return size
    return None


def save_clean_qr(page, path=None):
    """只截那张二维码（不是整页），返回路径；尺寸异常视为失败。"""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    out = str(path or QR_FILE)
    info = wait_qr_ready(page)
    if not info:
        log.warning("等不到二维码渲染（可能页面还在加载）")
        # 最后再试一次整页截图，方便排查，但不当成二维码交付
        return None
    log.info("二维码元素就绪：页面 %sx%s / 原图 %sx%s", info.get("w"), info.get("h"),
             info.get("nw"), info.get("nh"))

    # 首选：直接解码 img 的 base64，得到原生尺寸的干净二维码
    try:
        size = extract_qr_bitmap(page, out)
        if size:
            log.info("二维码已保存（原生位图）：%s（%dx%d）", out, size[0], size[1])
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning("二维码位图提取失败：%s，改用元素截图", exc)

    # 次选：对二维码 img 元素截图（Playwright 会等元素可见+稳定）
    try:
        el = page.locator("img.css-1lhmg90").first
        if not el.count():
            el = page.locator("img[src^='data:image']").last
        el.screenshot(path=out, timeout=15000)
        size = png_size(out)
        if size and size[0] >= 150:
            log.info("二维码已保存（元素截图）：%s（%dx%d）", out, size[0], size[1])
            return out
        log.warning("元素截图尺寸异常 %s，改用区域裁剪", size)
    except Exception as exc:  # noqa: BLE001
        log.warning("元素截图失败：%s，改用区域裁剪", exc)

    # 兜底：按二维码 bounding box 精确裁剪（仍然不截整页）
    try:
        el = page.locator("img.css-1lhmg90").first
        box = el.bounding_box() if el.count() else None
        if box and box["width"] >= 150:
            pad = 8
            page.screenshot(path=out, clip={
                "x": max(0, box["x"] - pad), "y": max(0, box["y"] - pad),
                "width": box["width"] + pad * 2, "height": box["height"] + pad * 2,
            })
            size = png_size(out)
            log.info("二维码已按区域裁剪保存：%s（%sx%s）", out,
                     size[0] if size else "?", size[1] if size else "?")
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning("区域裁剪也失败：%s", exc)
    return None


def refresh_qr(page, qr_path=None):
    """重新加载登录页 → 切扫码态 → 重新存二维码到同一路径（默认 qr-login.png）。"""
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:  # noqa: BLE001
        log.warning("刷新时重新打开登录页失败：%s", exc)
        return None
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:  # noqa: BLE001
        pass
    nap(1.2, 2.0)
    if not wait_login_card(page):
        log.warning("刷新后登录卡片没渲染出来")
        return None
    if not switch_to_qr(page):
        log.warning("刷新后切扫码态失败")
        return None
    nap(0.8, 1.4)
    return save_clean_qr(page, qr_path)


def detect_state(page, ctx=None):
    """'logged_in' / 'login_wall' / 'unknown'"""
    url = page.url or ""
    if "/login" in url:
        return "login_wall"
    if page_has(page, QR_TEXT) or page_has(page, "短信登录"):
        return "login_wall"
    if page_has(page, "发布笔记") or page_has(page, "发布图文") or page_has(page, "上传图文"):
        return "logged_in"
    if ctx is not None:
        try:
            if "web_session" in {c.get("name") for c in ctx.cookies()}:
                return "logged_in"
        except Exception:  # noqa: BLE001
            pass
    return "unknown"


def wait_for_login(page, timeout=900, interval=1.5, refresh_secs=110, qr_path=None):
    """每 interval 秒检查是否离开 /login；到期或提示失效就刷新二维码（同一路径覆盖）。

    扫码后手机还没确认时，页面会出现「扫描成功/请在手机上确认」，这时候不刷新，
    以免把用户正在确认的那次登录踢掉。
    """
    log.info("等待扫码：每 %.1fs 轮询，最多 %ds，二维码每 %ds 自动刷新",
             interval, timeout, refresh_secs)
    start = time.time()
    last_refresh = start
    count = 1  # 首轮二维码算 #1
    while time.time() - start < timeout:
        if "/login" not in (page.url or ""):
            nap(1.5, 2.5)  # 等跳转后的页面稳定
            return True
        body = body_text(page)
        scanned = any(k in body for k in ("扫描成功", "扫码成功", "请在手机上确认",
                                         "在手机上确认", "已扫码"))
        expired = any(k in body for k in ("失效", "过期", "请刷新"))
        due = (time.time() - last_refresh) >= refresh_secs
        if (expired or due) and not scanned:
            count += 1
            saved = refresh_qr(page, qr_path)
            last_refresh = time.time()
            print("[%s] 二维码已刷新 #%d → %s" % (now_ts(), count, saved or "刷新失败"),
                  flush=True)
            log.info("[%s] 二维码已刷新 #%d", now_ts(), count)
            if not saved:
                log.warning("刷新后二维码保存失败（下一轮会再试）")
        time.sleep(interval)
    return False


# ---------------------------------------------------------------- 登录后 DOM dump

def dump_publish_dom(page):
    """登录后：打开发布页，把真实 DOM 完整 dump 到 logs/。"""
    paths = {}
    log.info("登录后打开发布页 dump DOM…")
    try:
        page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:  # noqa: BLE001
        log.warning("打开发布页失败：%s", exc)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    nap(3.0, 4.5)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    txt_path = LOGS_DIR / "publish-dom.txt"
    html_path = LOGS_DIR / "publish-dom.html"
    png_path = LOGS_DIR / "publish-page.png"

    try:
        page.screenshot(path=str(png_path), full_page=True)
        paths["png"] = str(png_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("发布页截图失败：%s", exc)
    try:
        html_path.write_text(page.content(), encoding="utf-8")
        paths["html"] = str(html_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("发布页 HTML 保存失败：%s", exc)

    lines = []
    lines.append("=== 小红书发布页 DOM dump ===")
    lines.append("时间: %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("URL : %s" % page.url)
    try:
        lines.append("标题: %s" % page.title())
    except Exception:  # noqa: BLE001
        pass
    lines.append("")
    lines.append("--- body innerText（前 6000 字）---")
    txt = body_text(page)
    lines.append(txt[:6000])
    lines.append("")
    lines.append("--- 含关键词的可见元素 ---")
    try:
        hits = page.evaluate("""
        () => {
          const kws = ['标题','正文','话题','封面','拖动','排序','存草稿','发布','上传','图片','文字','视频'];
          const out = [];
          document.querySelectorAll('body *').forEach(el => {
            if (el.children.length > 0) return;
            const t = (el.innerText || '').trim();
            if (!t || t.length > 60) return;
            if (!kws.some(k => t.includes(k))) return;
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return;
            out.push({tag: el.tagName.toLowerCase(), text: t,
                      cls: (el.className && String(el.className).trim()) || ''});
          });
          return out.slice(0, 80);
        }
        """)
        for h in hits:
            lines.append("  [%s] %r | class=%s" % (h["tag"], h["text"], h["cls"][:140]))
    except Exception as exc:  # noqa: BLE001
        lines.append("  关键词扫描失败: %s" % exc)
    lines.append("")
    lines.append("--- 可交互元素清单（button/input/textarea/contenteditable/a）---")
    try:
        els = page.evaluate(ELEMENTS_JS)
        for e in els:
            lines.append("  [%s] text=%r class=%s attrs=%s rect=%s" % (
                e["tag"], e["text"], e["cls"][:160], e["attrs"], e["rect"]))
    except Exception as exc:  # noqa: BLE001
        lines.append("  元素扫描失败: %s" % exc)
    lines.append("")
    lines.append("--- iframe ---")
    for fr in page.frames:
        lines.append("  frame: %s" % fr.url)
    lines.append("")
    lines.append("--- 文件 ---")
    for k, v in paths.items():
        lines.append("  %s: %s" % (k, v))
    try:
        txt_path.write_text("\n".join(lines), encoding="utf-8")
        paths["txt"] = str(txt_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("DOM 文本 dump 保存失败：%s", exc)
    log.info("发布页 DOM dump 完成：%s", txt_path)
    return paths


def main():
    ap = argparse.ArgumentParser(description="小红书创作平台扫码登录 / 登录态检测")
    ap.add_argument("--qr", action="store_true",
                    help="一条命令出干净二维码：headless 打开→切扫码→存 logs/qr-login.png→轮询→自动刷新")
    ap.add_argument("--check", action="store_true", help="只检测登录态，不弹二维码")
    ap.add_argument("--headed", action="store_true", help="有头模式弹窗（本机人工扫码用）")
    ap.add_argument("--timeout", type=int, default=900, help="等待扫码秒数，默认 900（15 分钟）")
    ap.add_argument("--refresh", type=int, default=110, help="二维码自动刷新间隔秒数，默认 110")
    ap.add_argument("--qr-shot", nargs="?", const="", default=None,
                    help="二维码图片路径（默认固定 logs/qr-login.png）")
    ap.add_argument("--no-dump", action="store_true", help="登录成功后不 dump 发布页 DOM")
    ap.add_argument("--keep-open", action="store_true", help="登录后保持窗口打开（调试用）")
    args = ap.parse_args()

    # --qr 默认无头（远程扫码，不弹窗）；本机人工扫码用 --headed
    headless = True
    if args.qr:
        headless = not args.headed
    elif args.headed:
        headless = False

    with chrome_context(headless=headless) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # 打开发布页：未登录 302 到 /login?lastUrl=/publish/publish，登录后自动回发布页
        page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=30000)
        nap(1.5, 2.5)
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:  # noqa: BLE001
            pass

        state = detect_state(page, ctx)
        log.info("当前 URL: %s", page.url)
        log.info("登录态: %s", state)

        if args.check:
            if state == "logged_in":
                print("\n✅ 已登录，.profile 里的登录态可用。")
                return 0
            print("\n❌ 未登录（或登录态已过期）。先运行：python3 login.py --qr")
            return 2

        if state == "logged_in":
            print("\n✅ 已经是登录状态，不需要重新扫码。")
            if not args.no_dump:
                paths = dump_publish_dom(page)
                print("   发布页 DOM dump：%s" % paths.get("txt"))
            return 0

        if not wait_login_card(page):
            p = shot(page, "login-card-timeout")
            log.error("登录卡片没渲染出来。截图：%s", p)
            return 1

        if not switch_to_qr(page):
            p = shot(page, "switch-qr-failed")
            log.error("切扫码态失败。截图：%s", p)
            print("\n❌ 没能切到扫码登录。可能小红书改版了，请跑 explore.py --headed 重新勘察。")
            return 1

        qr_path = save_clean_qr(page, args.qr_shot or None)
        print("\n" + "=" * 66, flush=True)
        print("[%s] 二维码就绪（#1）：%s" % (now_ts(), qr_path), flush=True)
        print("请用【小红书 App】扫这个二维码登录（文件：%s）" % (qr_path or QR_FILE), flush=True)
        print("  每 %ds 自动刷新一次，刷新会覆盖同一文件并打印 [时间] 日志。" % args.refresh,
              flush=True)
        print("  最多等 %d 秒；登录成功会打印 ✓ 并自动 dump 发布页 DOM。" % args.timeout, flush=True)
        print("=" * 66 + "\n", flush=True)

        ok = wait_for_login(page, timeout=args.timeout, refresh_secs=args.refresh,
                            qr_path=args.qr_shot or None)
        if not ok:
            p = shot(page, "login-timeout")
            log.error("登录超时。截图：%s", p)
            print("[%s] ❌ 超时未扫码（%ds），二维码流程需要重启：python3 login.py --qr"
                  % (now_ts(), args.timeout), flush=True)
            print("  截图留证：%s" % p, flush=True)
            return 1

        log.info("扫码成功。当前 URL: %s", page.url)
        print("✓ 登录成功，登录态已保存到 .profile", flush=True)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        nap(2.0, 3.0)
        shot(page, "after-login")

        if not args.no_dump:
            paths = dump_publish_dom(page)
            print("✓ 发布页 DOM 已 dump：%s" % paths.get("txt"), flush=True)

        if args.keep_open:
            print(">>> --keep-open：Ctrl+C 退出", flush=True)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
