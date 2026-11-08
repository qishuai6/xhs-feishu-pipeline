#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信公众号登录（ego lite 版）—— 登录态存在 ego lite 浏览器里，人机共用。

用法：
    python3 wechat_login.py --check    # 检查 ego lite 里是否已登录公众号
    python3 wechat_login.py --qr       # 打开登录页，你去 ego lite 窗口微信扫码（选公众号身份）
"""

import argparse
import datetime
import subprocess
import sys
from pathlib import Path

LOGS = Path(__file__).resolve().parent / "logs"
LOGS.mkdir(parents=True, exist_ok=True)

DRIVER = r"""
const task = await taskSpace("wx-login-check-%s");
const page = task.page("p1");
await page.goto("https://mp.weixin.qq.com", { timeout: 40000 });
try { await page.waitForLoadState("load", { timeout: 15000 }); } catch {}
await new Promise(r => setTimeout(r, 2500));
const url = await page.url();
// 未登录特征：登录页的扫码文案（已登录会落在带 token 的首页）
const wall = await page.evaluate(() =>
  /微信扫一扫，选择公众平台账号登录/.test(document.body.innerText || "")).catch(() => false);
const hasHome = await page.evaluate(() =>
  /新的创作|发表记录|内容管理/.test(document.body.innerText || "")).catch(() => false);
await page.screenshot({ path: "%s/wx-login-check-%s.png" });
const ok = !wall && hasHome;
console.log("WX_LOGIN " + JSON.stringify({ ok, url }));
if (ok) await task.finish({ keep: [] });
"""


def run(src):
    return subprocess.run(["ego-browser", "nodejs"], input=src, text=True,
                          capture_output=True, timeout=180)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--qr", action="store_true")
    args = ap.parse_args()
    if not args.check and not args.qr:
        ap.print_help()
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.qr:
        # ego 已打开 mp 首页（登录页即首页），提示用户扫码即可
        print("请在 ego lite 里打开 mp.weixin.qq.com，用微信扫码并选择公众号身份登录。")
        input("→ 完成后回到这里按回车，自动复查…")

    p = run(DRIVER % (stamp, LOGS, stamp))
    out = (p.stdout or "") + (p.stderr or "")
    ok = None
    for line in out.splitlines():
        if line.startswith("WX_LOGIN "):
            import json
            d = json.loads(line[9:])
            ok = d.get("ok")
            print("URL：%s" % d.get("url", "")[:80])
            break
    if ok is None:
        print("❌ 检查异常：\n" + out[:600])
        return 1
    print("✅ ego lite 已登录公众号" if ok else "❌ 未登录（截图见 logs/wx-login-check-*.png）")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
