# -*- coding: utf-8 -*-
"""小红书发布链路公共工具。

只做四件事：
1. 用系统 Chrome（channel="chrome"）启动 persistent context，登录态存 .profile；
2. 人类化随机延迟；
3. 统一日志（同时写 logs/run-*.log）；
4. 失败截图留证。

注意：playwright 自带的 chromium 二进制在本机版本不匹配，必须走 channel="chrome"。
"""

import contextlib
import datetime
import logging
import random
import time
from pathlib import Path

PUBLISH_DIR = Path(__file__).resolve().parent
PROFILE_DIR = PUBLISH_DIR / ".profile"
LOGS_DIR = PUBLISH_DIR / "logs"

CREATOR_HOME = "https://creator.xiaohongshu.com"
LOGIN_URL = "https://creator.xiaohongshu.com/login"
PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish"

# 风控友好：单次操作间隔 0.3~1.2s 随机
def nap(lo=0.3, hi=1.2):
    time.sleep(random.uniform(lo, hi))


def get_logger(name="xhs", logfile=True):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:  # 避免重复添加
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    if logfile:
        fh = logging.FileHandler(
            LOGS_DIR / ("run-%s.log" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S")),
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


class PublishError(RuntimeError):
    """带步骤名和截图路径的业务异常，方便 README 里的排查对照。"""

    def __init__(self, step, message, screenshot=None):
        self.step = step
        self.message = message
        self.screenshot = screenshot
        super().__init__("[%s] %s" % (step, message))


def shot(page, tag, full_page=True):
    """截图留证到 logs/，返回路径。失败不抛异常（截图本身不该掩盖原错误）。"""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = LOGS_DIR / ("%s-%s.png" % (tag, stamp))
        page.screenshot(path=str(path), full_page=full_page)
        return str(path)
    except Exception as exc:  # noqa: BLE001
        return None


def clear_stale_profile_lock():
    """清理 Chrome 上次被强杀留下的 Singleton 锁（进程已死才算 stale）。

    实测：后台进程被 SIGTERM 杀掉后，Chrome 会残留 SingletonLock，
    再启动就会报 ProcessSingleton 失败。这里只在持锁进程确实不存在时才删。
    """
    import os

    lock = PROFILE_DIR / "SingletonLock"
    if not lock.is_symlink() and not lock.exists():
        return False
    try:
        target = os.readlink(str(lock)) if lock.is_symlink() else ""
    except OSError:
        target = ""
    pid = None
    if target:
        try:
            pid = int(str(target).split("-")[-1])
        except ValueError:
            pid = None
    alive = False
    if pid:
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            alive = False
    if alive:
        return False
    removed = False
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        p = PROFILE_DIR / name
        try:
            if p.is_symlink() or p.exists():
                p.unlink()
                removed = True
        except OSError:
            pass
    return removed


@contextlib.contextmanager
def chrome_context(headless=True, slow_mo=0, viewport=(1440, 900)):
    """启动系统 Chrome 的 persistent context。

    首次运行会创建 .profile 目录；之后复用，登录态自动保留。
    如果报 ProcessSingleton / profile 被锁，先清理“持锁进程已死”的残留锁，再重试一次。
    """
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    clear_stale_profile_lock()
    with sync_playwright() as p:
        launch_kwargs = dict(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=headless,
            slow_mo=slow_mo,
            viewport={"width": viewport[0], "height": viewport[1]},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = None
        last_exc = None
        for attempt in (1, 2):
            try:
                ctx = p.chromium.launch_persistent_context(**launch_kwargs)
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                msg = str(exc)
                if "ProcessSingleton" in msg and attempt == 1:
                    if clear_stale_profile_lock():
                        continue
                raise PublishError(
                    "launch",
                    "启动系统 Chrome 失败：%s\n"
                    "排查：1) 确认 /Applications/Google Chrome.app 存在；"
                    "2) 关闭占用 %s 的 Chrome 进程（或结束上次没跑完的脚本）；"
                    "3) 仍不行就把 .profile 删掉重新扫码登录。" % (exc, PROFILE_DIR),
                ) from exc
        if ctx is None:  # pragma: no cover
            raise PublishError("launch", "启动系统 Chrome 失败：%s" % last_exc)
        try:
            yield ctx
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass


def page_goto(page, url, timeout=30000):
    page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    nap()


def looks_like_login(page):
    """粗判是否停在登录墙：URL / 二维码 / 登录按钮文案。"""
    url = page.url or ""
    if "/login" in url:
        return True
    try:
        if page.locator("text=扫码登录").count() > 0:
            return True
        if page.locator("text=登录").count() > 0 and page.locator("text=发布笔记").count() == 0:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def first_visible(page, selectors, timeout=5000):
    """按顺序尝试选择器，返回第一个可见 locator；都失败返回 None。"""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=timeout):
                return loc
        except Exception:  # noqa: BLE001
            continue
    return None


def human_type(locator, text, delay=(60, 160)):
    """逐字输入，带随机节奏，尽量贴近真人。"""
    locator.click()
    nap(0.15, 0.4)
    for ch in text:
        locator.type(ch)
        time.sleep(random.uniform(*delay) / 1000.0)


def dump_debug(page, tag, note=""):
    """失败留证：HTML + 可见文本 + 整页截图，全部进 logs/。返回路径字典。"""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = {}
    html_path = LOGS_DIR / ("dump-%s-%s.html" % (tag, stamp))
    txt_path = LOGS_DIR / ("dump-%s-%s.txt" % (tag, stamp))
    png_path = LOGS_DIR / ("dump-%s-%s.png" % (tag, stamp))
    try:
        html_path.write_text(page.content(), encoding="utf-8")
        out["html"] = str(html_path)
    except Exception:  # noqa: BLE001
        pass
    try:
        body = ""
        try:
            body = page.inner_text("body")
        except Exception:  # noqa: BLE001
            pass
        txt_path.write_text(
            "URL: %s\nNOTE: %s\n\n--- body innerText ---\n%s" % (page.url, note, body),
            encoding="utf-8",
        )
        out["txt"] = str(txt_path)
    except Exception:  # noqa: BLE001
        pass
    try:
        page.screenshot(path=str(png_path), full_page=True)
        out["png"] = str(png_path)
    except Exception:  # noqa: BLE001
        pass
    return out

