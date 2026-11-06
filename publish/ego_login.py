#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书登录（ego lite 版）—— 登录态存在 ego lite 浏览器里，人机共用。

用法：
    python3 ego_login.py --check    # 检查 ego lite 里是否已登录小红书
    python3 ego_login.py --qr       # 打开登录页，你去 ego lite 窗口扫码，回来按回车后自动复查

说明：登录态跟 ego lite 的浏览器配置走（不像 Playwright 版需要单独的 .profile）。
在 ego lite 里登录过小红书网页/创作平台后，发布脚本（ego_publish.py）直接可用。
"""

import argparse
import subprocess
import sys
from pathlib import Path

DRIVER_CHECK = r"""
const task = await taskSpace("xhs-login-check-%s");
const page = task.page("p1");
await page.goto("https://creator.xiaohongshu.com/publish/publish", { timeout: 40000 });
try { await page.waitForLoadState("load", { timeout: 15000 }); } catch {}
await new Promise(r => setTimeout(r, 2000));
const url = await page.url();
// 已登录判据：没被重定向到 /login，且页面出现发布页特征文字（头像/菜单）。
// 注意：evaluate 必须传函数（传字符串会静默返回 {}，实测）。
const hasCreatorUI = await page.evaluate(() =>
  /上传图文|上传视频|笔记管理|数据看板/.test(document.body.innerText || '')).catch(() => false);
await page.screenshot({ path: "%s/login-check-%s.png" });
const ok = !url.includes("/login") && hasCreatorUI;
console.log("EGO_LOGIN " + JSON.stringify({ ok, url }));
if (ok) await task.finish({ keep: [] });
"""

DRIVER_OPEN_LOGIN = r"""
const task = await taskSpace("xhs-login-open-%s");
const page = task.page("p1");
await page.goto("https://creator.xiaohongshu.com/publish/publish", { timeout: 40000 });
try { await page.waitForLoadState("load", { timeout: 15000 }); } catch {}
await new Promise(r => setTimeout(r, 1500));
await page.screenshot({ path: "%s/login-open-%s.png" });
console.log("EGO_OPENED");
await task.handOff();
"""


def run_script(src):
    proc = subprocess.run(["ego-browser", "nodejs"], input=src, text=True,
                          capture_output=True, timeout=120)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--qr", action="store_true")
    args = ap.parse_args()
    if not args.check and not args.qr:
        ap.print_help()
        return 0

    import datetime
    logs = Path(__file__).resolve().parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    if args.qr:
        code, out = run_script(DRIVER_OPEN_LOGIN % (stamp, logs, stamp))
        print("已在 ego lite 打开小红书登录页（截图：%s/login-open-%s.png）" % (logs, stamp))
        input("→ 请在 ego lite 窗口里用小红书 App 扫码登录，完成后回到这里按回车…")

    code, out = run_script(DRIVER_CHECK % (stamp, logs, stamp))
    ok = False
    for line in out.splitlines():
        if line.startswith("EGO_LOGIN "):
            import json
            d = json.loads(line[len("EGO_LOGIN "):])
            ok = d.get("ok")
            print("当前 URL：%s" % d.get("url"))
            break
    else:
        print("❌ 检查脚本异常：\n" + out[:800])
        return 1

    print("✅ ego lite 已登录小红书" if ok else "❌ 未登录（或登录态已过期）")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
