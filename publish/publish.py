# -*- coding: utf-8 -*-
"""小红书图文笔记发布器（Playwright + 系统 Chrome）。

默认只「存草稿」，只有显式加 --publish 才会真的点发布；本脚本作者不会替你点发布。

用法：
    # 最常用：把一个目录里的图按文件名顺序发成草稿
    python3 publish.py \
        --images ../out/demo-magazine \
        --title "普通人最该做的是把消费换成资产" \
        --body body.txt \
        --tags "复利,长期主义,普通人副业"

    # 显式指定图片（逗号分隔，顺序即发布顺序，第 1 张是默认封面）
    python3 publish.py --images "a.png,b.png,c.png" --title "..." --body "..."

    # 真发布（谨慎！）
    python3 publish.py --images ../out/demo-minimal --title "..." --body "..." --publish

设计原则：
- 选择器不写死哈希 class，优先用文本/角色/placeholder 定位，并支持多候选；
- 每一步都打日志，失败自动截图 + dump DOM 到 logs/，报清楚卡在哪一步；
- 操作之间加 0.3~1.2s 人类化随机延迟，不并发、不高频。

退出码：0 成功 / 1 失败（看日志） / 2 未登录（先跑 login.py --qr）
"""

import argparse
import contextlib
import datetime
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

from common import (
    LOGS_DIR,
    PUBLISH_URL,
    PublishError,
    chrome_context,
    dump_debug,
    get_logger,
    human_type,
    nap,
    shot,
)

log = get_logger("publish")

MAX_TITLE = 20          # 小红书标题上限：20 字
MAX_BODY = 1000         # 小红书图文正文常见上限：1000 字（超出只警告，不静默截断）
MAX_IMAGES = 18         # 小红书图文单篇上限：18 张
RUN_STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

# 安全红线：草稿模式下，绝不允许点击「可能是最终发布按钮」的元素。
# 注意：导航入口「发布笔记 / 写笔记」不算提交，但为了绝对安全，
# 草稿模式也不点它们——直接 goto 发布页 URL（已实测可用）。
SUBMIT_TEXT_RE = re.compile(r"^(发布|立即发布|确认发布|确定发布|提交|立即提交)$")


# ---------------------------------------------------------------- 运行记录（--report）

class Recorder:
    """把一次运行的全过程记录成 JSON：步骤/耗时/选择器命中/点击/重试/失败证据。

    用户怀疑账号异常时，这是唯一能回溯"脚本到底干了什么"的证据。
    """

    def __init__(self):
        self.stamp = RUN_STAMP
        self.started_at = datetime.datetime.now().isoformat(timespec="seconds")
        self.meta = {}
        self.steps = []
        self.selectors = []
        self.clicks = []
        self.retries = []
        self.screenshots = []
        self.failures = []
        self.current_step = "init"

    @contextlib.contextmanager
    def step(self, name):
        prev = self.current_step
        self.current_step = name
        t0 = time.time()
        rec = {"name": name, "ok": True,
               "started_at": datetime.datetime.now().isoformat(timespec="seconds")}
        try:
            yield
        except Exception as exc:  # noqa: BLE001
            rec["ok"] = False
            rec["error"] = str(exc)[:500]
            self.failures.append({"step": name, "error": str(exc)[:500]})
            raise
        finally:
            rec["duration_s"] = round(time.time() - t0, 2)
            self.steps.append(rec)
            self.current_step = prev

    def selector(self, desc, kind, value, matched):
        self.selectors.append({
            "step": self.current_step, "desc": desc, "kind": kind,
            "value": str(value), "matched": bool(matched),
        })

    def click(self, purpose, text, allow_submit):
        self.clicks.append({
            "step": self.current_step, "purpose": purpose,
            "text": (text or "")[:80], "allow_submit": bool(allow_submit),
            "at": datetime.datetime.now().isoformat(timespec="seconds"),
        })

    def retry(self, detail):
        self.retries.append({"step": self.current_step, "detail": str(detail)[:300]})

    def screenshot(self, path):
        if path:
            self.screenshots.append(str(path))

    def to_dict(self, result="unknown", extra=None):
        return {
            "stamp": self.stamp,
            "started_at": self.started_at,
            "finished_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "result": result,
            "meta": self.meta,
            "steps": self.steps,
            "selectors": self.selectors,
            "clicks": self.clicks,
            "retries": self.retries,
            "screenshots": self.screenshots,
            "failures": self.failures,
            "extra": extra or {},
        }

    def save(self, result="unknown", extra=None):
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        path = LOGS_DIR / ("run-report-%s.json" % self.stamp)
        try:
            path.write_text(
                json.dumps(self.to_dict(result, extra), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("运行报告保存失败：%s", exc)
            return None
        return str(path)


REC = Recorder()


# ---------------------------------------------------------------- 点击安全闸门

def _locator_text(loc):
    for getter in ("inner_text", "input_value"):
        try:
            val = getattr(loc, getter)()
            if val:
                return str(val).strip()
        except Exception:  # noqa: BLE001
            continue
    for attr in ("aria-label", "title"):
        try:
            val = loc.get_attribute(attr)
            if val:
                return str(val).strip()
        except Exception:  # noqa: BLE001
            continue
    return ""


def safe_click(loc, purpose, allow_submit=False):
    """所有点击都走这里。草稿模式点任何「疑似发布按钮」直接抛异常，绝不静默放过。"""
    text = _locator_text(loc)
    if not allow_submit and SUBMIT_TEXT_RE.match(text):
        REC.click(purpose, text, allow_submit)
        raise PublishError(
            "safety",
            "安全闸门拦截：非 --publish 模式试图点击疑似发布按钮「%s」（purpose=%s）。"
            "已停止，没有点下去。" % (text, purpose),
        )
    loc.click()
    REC.click(purpose, text, allow_submit)
    log.info("  ▶ 点击「%s」（%s）", purpose, "允许提交" if allow_submit else "非提交")


# ---------------------------------------------------------------- 小工具

def step_shot(page, name):
    """每一步都留一张截图，方便回溯。"""
    path = shot(page, "step-%s" % name)
    REC.screenshot(path)
    return path


def fail(page, step, message):
    """失败：截图 + dump DOM + 抛业务异常（带证据路径）。"""
    dump = dump_debug(page, "fail-%s" % step, note=message)
    REC.screenshot(dump.get("png"))
    REC.failures.append({"step": step, "error": message[:500],
                         "evidence": dump.get("txt") or dump.get("html")})
    raise PublishError(
        step,
        "%s\n  截图: %s\n  DOM : %s\n  文本: %s" % (
            message, dump.get("png"), dump.get("html"), dump.get("txt")),
    )


def _visible(loc, timeout_ms=800):
    try:
        return loc.count() > 0 and loc.first.is_visible(timeout=timeout_ms)
    except Exception:  # noqa: BLE001
        return False


def find_any(page, candidates, desc, timeout=10, required=True):
    """按候选顺序找第一个可见元素。

    candidates 形如 [("text", "存草稿"), ("role", ("button", "存草稿")), ("css", "input[placeholder*='标题']")]
    命中会打印并记录用的是哪个候选——SELECTORS.md 里的结论就是这么反推的。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for kind, val in candidates:
            try:
                if kind == "text":
                    loc = page.get_by_text(val, exact=True)
                elif kind == "text_loose":
                    loc = page.get_by_text(val, exact=False)
                elif kind == "role":
                    role, name = val
                    loc = page.get_by_role(role, name=name)
                elif kind == "role_loose":
                    role, name = val
                    loc = page.get_by_role(role, name=re.compile(name))
                elif kind == "css":
                    loc = page.locator(val)
                elif kind == "placeholder":
                    loc = page.get_by_placeholder(val)
                else:
                    continue
                if _visible(loc):
                    log.info("  ✓ 定位「%s」命中：%s %r", desc, kind, val)
                    REC.selector(desc, kind, val, True)
                    return loc.first
            except Exception:  # noqa: BLE001
                continue
        time.sleep(0.4)
    REC.selector(desc, "ALL", candidates, False)
    if required:
        raise LookupError("找不到「%s」，候选=%s" % (desc, candidates))
    return None


def wait_gone(page, text, timeout=30):
    """等某段文字消失（比如「上传中」）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if page.get_by_text(text, exact=False).count() == 0:
                return True
        except Exception:  # noqa: BLE001
            return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------- 输入整理

def resolve_images(raw, script_dir):
    """把 --images 解析成有序图片列表。

    支持三种写法：
      1) 目录           -> 目录下 *.png/*.jpg/*.jpeg/*.webp，按文件名自然排序
      2) 逗号分隔       -> "a.png,b.png"
      3) 通配符         -> "../out/demo-*/*.png"
    """
    raw = (raw or "").strip()
    if not raw:
        raise SystemExit("❌ 必须给 --images")
    paths = []

    def expand(p):
        p = os.path.expanduser(p.strip())
        if not os.path.isabs(p):
            p = os.path.join(script_dir, p)
        return os.path.normpath(p)

    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        p = expand(part)
        if os.path.isdir(p):
            for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.PNG", "*.JPG", "*.JPEG"):
                paths.extend(glob.glob(os.path.join(p, ext)))
        else:
            hits = glob.glob(p)
            paths.extend(hits if hits else [p])

    return validate_images(paths)


def validate_images(paths):
    """去重 + 按文件名自然排序 + 校验存在性和数量。"""
    def natural_key(s):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]

    paths = sorted(set(paths), key=natural_key)
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        raise SystemExit("❌ 图片不存在：\n  " + "\n  ".join(missing))
    if not paths:
        raise SystemExit("❌ 没解析出任何图片")
    if len(paths) > MAX_IMAGES:
        raise SystemExit("❌ 小红书图文最多 %d 张，你给了 %d 张" % (MAX_IMAGES, len(paths)))
    return paths


def read_body(body_arg, script_dir):
    if body_arg and os.path.isfile(os.path.expanduser(body_arg)):
        return Path(os.path.expanduser(body_arg)).read_text(encoding="utf-8")
    if body_arg and os.path.isfile(os.path.join(script_dir, body_arg)):
        return Path(os.path.join(script_dir, body_arg)).read_text(encoding="utf-8")
    return body_arg or ""


def normalize_title(title):
    """标题压成单行：换行/连续空白 → 一个空格，去首尾空白。"""
    return re.sub(r"\s+", " ", (title or "").replace("\u3000", " ")).strip()


def normalize_tags(tags):
    """话题支持 "a,b" 字符串或 ["a","b"] 列表。"""
    if not tags:
        return []
    if isinstance(tags, str):
        items = tags.split(",")
    else:
        items = list(tags)
    return [str(t).strip().lstrip("#") for t in items if str(t).strip()]


def load_meta(path, script_dir):
    """可选：读取 render 产出的 meta.json，省得手动拼参数。

    meta 里认识的字段：title / body / tags / images / lead。
    images 是相对 meta 文件所在目录的路径。（render 的 meta.json 就是这么写的）
    """
    if not path:
        return {}
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        p = os.path.join(script_dir, p)
    p = os.path.normpath(p)
    if os.path.isdir(p):
        p = os.path.join(p, "meta.json")
    if not os.path.isfile(p):
        raise SystemExit("❌ --meta 文件不存在：%s" % p)
    import json

    data = json.loads(Path(p).read_text(encoding="utf-8"))
    base = os.path.dirname(p)
    resolved = []
    for item in data.get("images") or []:
        cand = os.path.normpath(os.path.join(base, str(item)))
        if not os.path.isfile(cand):
            cand = os.path.normpath(os.path.join(script_dir, str(item)))
        resolved.append(cand)
    data["_images_resolved"] = resolved
    log.info("读入 meta.json：%s（style=%s, %d 张图）", p, data.get("style"),
             len(resolved))
    return data


# ---------------------------------------------------------------- 主体流程

def assert_logged_in(page, ctx):
    """未登录就明确报错退出，不要带着登录墙往下点。"""
    url = page.url or ""
    try:
        if page.get_by_text("APP扫一扫登录", exact=False).count() > 0 \
                or page.get_by_text("短信登录", exact=False).count() > 0:
            raise PublishError("login", "当前是登录页，登录态不可用")
    except PublishError:
        raise
    except Exception:  # noqa: BLE001
        pass
    if "/login" in url:
        raise PublishError("login", "被重定向到登录页（%s）。先跑：python3 login.py --qr" % url)


def goto_publish_page(page):
    """进入「发布笔记 → 上传图文」页面。"""
    log.info("打开创作平台…")
    page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=40000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    nap(2.0, 3.0)
    step_shot(page, "01-publish-url")
    log.info("当前 URL: %s", page.url)

    # 登录墙：直接快速失败，不要继续在登录页上找按钮（实测未登录会 302 到 /login）
    if "/login" in (page.url or ""):
        raise PublishError(
            "login",
            "被重定向到登录页（%s）。先跑：python3 login.py --qr" % page.url,
        )

    # 路线 1（半自动存草稿）的安全设计：**不点击任何「发布笔记」入口**，
    # 直接 goto 发布页 URL。登录成功后这里会落在 /publish/publish，不需要导航点击。
    # 如果没落在发布页，交给后面的上传步骤报错（有截图/DOM 证据），不做模糊导航。
    log.info("当前页面是否发布页：%s", "是" if "/publish/publish" in (page.url or "") else "否")
    step_shot(page, "02-publish-landed")


# ---------------------------------------------------------------- 上传图文 tab（实测坑：tab 有隐藏副本）

# 实测（2026-11-05）：发布页默认停在「上传视频」tab，全页只有视频 input。
# 「上传图文」在 DOM 里有 3 份：
#   1) 真实可点：<div class="creator-tab" data-hp-bound="1"><span class="title">上传图文</span></div>
#   2) 测量副本：opacity:1e-05; z-index:-1; pointer-events:auto（is_visible() 会判 True！）
#   3) 视口外副本：position:absolute; left:-9999px; top:-9999px（bounding box 非空，
#      Playwright is_visible() 同样判 True，所以不能用 is_visible 过滤）
# 结论：必须自己按「在视口内 + 祖先 opacity 足够 + pointer-events 不是 none」来挑。
PICK_LEAF_JS = r"""
(text) => {
  document.querySelectorAll('[data-xhs-pick]').forEach(e => e.removeAttribute('data-xhs-pick'));
  const cands = [];
  document.querySelectorAll('div,span,a,button,li').forEach(el => {
    const t = (el.innerText || '').trim();
    if (t !== text) return;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    // 排除视口外副本（left:-9999px 这种 bounding box 非空但根本不在屏幕上）
    if (r.left < -1 || r.top < -1 || r.right > innerWidth + 2 || r.bottom > innerHeight + 2) return;
    // 沿祖先链累计 opacity + 检查 visibility/display/pointer-events
    let opacity = 1, visible = true, pe = 'auto', node = el;
    for (let i = 0; i < 7 && node && node !== document.documentElement; i++) {
      const cs = getComputedStyle(node);
      opacity *= parseFloat(cs.opacity || '1');
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse') visible = false;
      if (cs.pointerEvents === 'none') pe = 'none';
      node = node.parentElement;
    }
    if (!visible || opacity < 0.05 || pe === 'none') return;
    cands.push({el, area: r.width * r.height, bound: el.getAttribute('data-hp-bound') === '1'});
  });
  if (!cands.length) return null;
  cands.sort((a, b) => (b.bound ? 1 : 0) - (a.bound ? 1 : 0) || b.area - a.area);
  cands[0].el.setAttribute('data-xhs-pick', '1');
  const r = cands[0].el.getBoundingClientRect();
  return {count: cands.length, bound: cands[0].bound,
          x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)};
}
"""

IMAGE_MODE_JS = r"""
() => {
  const inputs = Array.from(document.querySelectorAll('input[type=file]'));
  const isImg = (a) => { a = (a || '').toLowerCase();
    return !a || a.includes('png') || a.includes('jpg') || a.includes('jpeg') || a.includes('webp') || a.includes('image'); };
  const isVid = (a) => { a = (a || '').toLowerCase();
    return a.includes('mp4') || a.includes('mov') || a.includes('flv') || a.includes('mkv'); };
  const img = inputs.find(i => isImg(i.accept) && !isVid(i.accept));
  const txt = document.body.innerText || '';
  return {has_image_input: !!img, drag_text: /拖拽图片|上传图片|添加图片/.test(txt),
          inputs: inputs.map(i => ({accept: i.accept || '', multiple: i.multiple}))};
}
"""


def pick_tab(page, text):
    """挑真正可见可点的 tab 元素（排除隐藏测量副本和视口外副本）。"""
    info = page.evaluate(PICK_LEAF_JS, text)
    if not info:
        return None, None
    loc = page.locator('[data-xhs-pick="1"]').first
    return loc, info


def switch_to_image_tab(page):
    """切到「上传图文」：选真实 tab → 点击 → 断言图片上传区出现。失败重载重试一次。"""
    for attempt in (1, 2):
        # 已经切过去就不用再点（重试时也复用这个判断）
        try:
            state = page.evaluate(IMAGE_MODE_JS)
        except Exception:  # noqa: BLE001
            state = {}
        if state.get("has_image_input") or state.get("drag_text"):
            log.info("已经是「上传图文」模式（图片 input=%s，上传区文案=%s）",
                     state.get("has_image_input"), state.get("drag_text"))
            return True

        log.info("第 %d 次切「上传图文」tab…", attempt)
        loc, info = pick_tab(page, "上传图文")
        if loc is None:
            log.warning("没找到可见的「上传图文」tab")
        else:
            log.info("  选中 tab：%s（候选 %d 个，bound=%s，位置 %s,%s %sx%s）",
                     "data-hp-bound" if info.get("bound") else "fallback",
                     info.get("count"), info.get("bound"), info.get("x"), info.get("y"),
                     info.get("w"), info.get("h"))
            try:
                safe_click(loc, "切到上传图文 tab")
            except PublishError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("点 tab 失败：%s", exc)
                REC.retry("点上传图文 tab 失败：%s" % exc)

        # 断言：图片上传区出现（accept 含图片的 input，或「拖拽图片/上传图片」文案）
        ok = False
        for _ in range(16):
            try:
                state = page.evaluate(IMAGE_MODE_JS)
            except Exception:  # noqa: BLE001
                state = {}
            if state.get("has_image_input") or state.get("drag_text"):
                ok = True
                break
            time.sleep(0.4)
        if ok:
            log.info("✓ 已切到「上传图文」并确认图片上传区出现")
            REC.selector("上传图文 tab", "js-pick", "上传图文", True)
            step_shot(page, "03-image-tab")
            return True
        log.warning("切 tab 后没看到图片上传区，重试一次")

    # 两次都失败：刷新页面再试最后一轮
    log.warning("两次切换失败，重载页面后最后再试一次")
    try:
        page.reload(wait_until="domcontentloaded")
        nap(2.0, 3.0)
        loc, info = pick_tab(page, "上传图文")
        if loc is not None:
            safe_click(loc, "切到上传图文 tab（重载后）")
            for _ in range(16):
                state = page.evaluate(IMAGE_MODE_JS)
                if state.get("has_image_input") or state.get("drag_text"):
                    log.info("✓ 重载后切 tab 成功")
                    step_shot(page, "03-image-tab")
                    return True
                time.sleep(0.4)
    except Exception as exc:  # noqa: BLE001
        log.warning("重载重试失败：%s", exc)
    return False


def find_image_input(page, timeout=25):
    """等 accept 含图片后缀的 file input 出现，返回 (locator, info)。

    实测：上传视频的 input accept=".mp4,.mov,..."，绝不能拿第一个 input[type=file]。
    """
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            info = page.evaluate(r"""
            () => {
              document.querySelectorAll('[data-xhs-input]').forEach(e => e.removeAttribute('data-xhs-input'));
              const inputs = Array.from(document.querySelectorAll('input[type=file]'));
              const isImg = (a) => { a = (a || '').toLowerCase();
                return !!a && (a.includes('png') || a.includes('jpg') || a.includes('jpeg')
                               || a.includes('webp') || a.includes('image')); };
              const cands = inputs.filter(i => isImg(i.accept));
              if (!cands.length) return {ready: false, inputs: inputs.map(i => i.accept || '')};
              const pick = cands[0];
              pick.setAttribute('data-xhs-input', '1');
              return {ready: true, accept: pick.accept || '', multiple: !!pick.multiple,
                      cls: String(pick.className || ''),
                      inputs: inputs.map(i => i.accept || '')};
            }
            """)
        except Exception:  # noqa: BLE001
            info = None
        if info and info.get("ready"):
            loc = page.locator('[data-xhs-input="1"]').first
            log.info("  ✓ 找到图片上传 input：accept=%r multiple=%s", info.get("accept"),
                     info.get("multiple"))
            return loc, info
        last = info
        time.sleep(0.5)
    raise LookupError("等不到 accept 含图片后缀的 input[type=file]；页面现有 input accept=%s"
                      % (last.get("inputs") if last else "?"))


def upload_images(page, images):
    """切「上传图文」tab → 找到图片 input → 按顺序上传（自动处理 multiple）。"""
    log.info("上传 %d 张图片…", len(images))

    if not switch_to_image_tab(page):
        fail(page, "upload-tab", "切不到「上传图文」tab（发布页可能默认停在「上传视频」）。"
                                 "已尝试选择器+重载重试。")
    nap(0.8, 1.5)

    try:
        inp, info = find_image_input(page)
    except LookupError as exc:
        fail(page, "upload-input", str(exc))
        raise  # pragma: no cover

    REC.selector("图片上传 input", "css", "input[type=file][accept*=图片后缀]", True)
    multiple = info.get("multiple")
    staged = images[:MAX_IMAGES]

    if multiple:
        log.info("  图片 input 支持多选（multiple=true），一次按顺序上传 %d 张", len(staged))
        try:
            inp.set_input_files(staged, timeout=30000)
            REC.click("上传图片（set_input_files，非点击）", "%d 张" % len(staged), False)
        except Exception as exc:  # noqa: BLE001
            fail(page, "upload-set", "set_input_files 失败：%s" % exc)
    else:
        # 实测要求：非 multiple 时逐张上传，并观察是「追加」还是「替换」
        log.warning("  图片 input 不支持多选（multiple=false），改为逐张上传")
        for idx, img_path in enumerate(staged, 1):
            try:
                cur, cur_info = find_image_input(page, timeout=10)
                cur.set_input_files([img_path], timeout=30000)
                REC.click("上传第 %d 张（单文件模式）" % idx, os.path.basename(img_path), False)
            except Exception as exc:  # noqa: BLE001
                fail(page, "upload-set", "第 %d 张上传失败：%s" % (idx, exc))
            nap(1.0, 2.0)
            if idx == 2:
                n = _count_preview_imgs(page)
                log.info("  上传第 2 张后预览图数量 = %d（1=替换，2+=追加）", n)
                if n <= 1:
                    raise PublishError(
                        "upload-multiple",
                        "图片 input 不支持多选，且第 2 张是「替换」而不是「追加」，"
                        "无法可靠多图上传。请检查小红书上传控件是否改版。")

    nap(1.5, 2.5)

    # 等图片全部传上去：实测「图片编辑 N/18」标题里的计数 + img.preview 缩略图数量
    log.info("等待图片上传完成…")
    deadline = time.time() + 180
    last = -1
    while time.time() < deadline:
        try:
            done = page.evaluate(r"""
            () => {
              const txt = document.body.innerText || '';
              const uploading = /上传中|正在上传|上传进度/.test(txt);
              const previews = document.querySelectorAll('img.preview').length;
              let status = '';
              const st = document.querySelector('.img-list .status');
              if (st) status = (st.innerText || '').trim();
              return {uploading, previews, status};
            }
            """)
        except Exception:  # noqa: BLE001
            done = {"uploading": False, "previews": 0, "status": ""}
        if done["previews"] != last:
            log.info("  缩略图 %d 张（目标 %d），页面计数=%r",
                     done["previews"], len(images), done.get("status"))
            last = done["previews"]
        if not done["uploading"] and done["previews"] >= len(images):
            log.info("✓ 图片上传完成（缩略图 %d 张，计数 %r）",
                     done["previews"], done.get("status"))
            break
        time.sleep(1.0)
    else:
        log.warning("⚠️ 180 秒内没确认全部上传完成，继续尝试填内容（截图留证）")
    step_shot(page, "04-uploaded")
    nap(1.0, 2.0)
    dismiss_overlays(page)


def dismiss_overlays(page):
    """关掉挡路的引导弹窗（实测上传后会弹「图片可以编辑啦」引导，遮挡编辑区）。"""
    for txt in ("我知道了", "知道了", "关闭"):
        try:
            btn = page.get_by_text(txt, exact=True)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click(timeout=2000)
                log.info("  已关闭引导弹窗：%s", txt)
                nap(0.5, 1.0)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _count_preview_imgs(page):
    try:
        return page.evaluate("""() => document.querySelectorAll('img.preview').length""")
    except Exception:  # noqa: BLE001
        return 0


def fill_title(page, title):
    log.info("填标题（%d 字，上限 %d）：%s", len(title), MAX_TITLE, title)
    if len(title) > MAX_TITLE:
        log.warning("标题超过 %d 字，会被小红书截断/拒绝，建议自己改短", MAX_TITLE)
    loc = find_any(page, [
        ("css", "input[placeholder*='标题']"),
        ("css", "input[placeholder*='填写标题']"),
        ("placeholder", "填写标题会有更多赞哦"),
        ("css", "input[class*='title']"),
        ("css", "input[type='text']"),
    ], "标题输入框", timeout=20)
    try:
        loc.fill("")
        human_type(loc, title[:MAX_TITLE])
    except Exception as exc:  # noqa: BLE001
        fail(page, "title", "标题填写失败：%s" % exc)
    nap(0.5, 1.0)
    try:
        val = loc.input_value()
        log.info("  标题实际值：%r（%d 字）", val, len(val))
        if not val.strip():
            fail(page, "title-verify", "标题写入后输入框是空的（可能被 maxlength/校验吞了）")
    except PublishError:
        raise
    except Exception:  # noqa: BLE001
        pass
    step_shot(page, "04-title")


def find_editor(page):
    """正文编辑器：优先 contenteditable，其次带 placeholder 的 div。"""
    for kind, val in (("css", "[contenteditable='true']"), ("css", "[contenteditable]"),
                      ("css", "div[class*='content']")):
        try:
            loc = page.locator(val)
            for i in range(min(loc.count(), 6)):
                cand = loc.nth(i)
                if cand.is_visible():
                    box = cand.bounding_box()
                    # 正文编辑区一般是页面上较大的一块
                    if box and box["height"] > 80:
                        log.info("  ✓ 正文编辑器命中：%s（第 %d 个，%.0fx%.0f）",
                                 val, i, box["width"], box["height"])
                        return cand
        except Exception:  # noqa: BLE001
            continue
    return None


def _norm_len(s):
    return len(re.sub(r"\s", "", s or ""))


def fill_body(page, body):
    """填正文，并做「写进去没有」的校验：编辑器可能是 Slate/ProseMirror，
    一次 insert_text 不一定进状态，所以校验不过就清空重来一次。"""
    log.info("填正文（%d 字，去空白 %d 字）…", len(body), _norm_len(body))
    if len(body) > MAX_BODY:
        log.warning("正文超过小红书常见上限 %d 字，可能被拦截或截断，建议拆图/精简", MAX_BODY)
    editor = find_editor(page)
    if editor is None:
        fail(page, "body", "找不到正文编辑器（contenteditable）")

    expect = _norm_len(body)
    got = 0
    for attempt in (1, 2):
        try:
            editor.click()
            nap(0.3, 0.7)
            if attempt == 2:
                # 清空上一次可能写了一半的内容
                page.keyboard.press("Meta+A")
                page.keyboard.press("Backspace")
                nap(0.3, 0.6)
                editor.click()
            # 实测：Tiptap/ProseMirror 里整段 insert_text 会把 \n 弄成多个多余空行，
            # 正确做法是「段落用 Enter 新开 <p>，段内单换行用 Shift+Enter」。
            _insert_body_text(page, body)
            nap(0.6, 1.2)
        except Exception as exc:  # noqa: BLE001
            fail(page, "body-fill", "正文填写失败：%s" % exc)

        try:
            got = _norm_len(editor.inner_text())
        except Exception:  # noqa: BLE001
            got = 0
        if expect == 0 or got >= max(2, int(expect * 0.8)):
            log.info("  正文写入校验通过：目标 %d 字 / 实际 %d 字", expect, got)
            break
        log.warning("  正文写入校验不通过：目标 %d 字 / 实际 %d 字，清空重试一次", expect, got)
        if attempt == 2:
            fail(page, "body-verify",
                 "正文写进去了但编辑器内容为空/不完整（目标 %d 字，实际 %d 字）" % (expect, got))
    nap(0.5, 1.0)
    step_shot(page, "05-body")


def _insert_body_text(page, body):
    """按段落写正文：段落之间 Enter（生成 <p>），段内换行 Shift+Enter（生成 <br>）。"""
    body = (body or "").replace("\r\n", "\n")
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    for pi, para in enumerate(paras):
        lines = [ln for ln in para.split("\n")]
        for li, line in enumerate(lines):
            if line:
                page.keyboard.insert_text(line)
            if li < len(lines) - 1:
                page.keyboard.press("Shift+Enter")
                time.sleep(0.08)
        if pi < len(paras) - 1:
            page.keyboard.press("Enter")
            time.sleep(0.12)


def _topic_count(page):
    try:
        return page.evaluate("() => document.querySelectorAll('div.tiptap a.tiptap-topic').length")
    except Exception:  # noqa: BLE001
        return 0


def _panel_topic_names(page):
    """读话题联想面板里的候选名（实测：tippy 浮层 #creator-editor-topic-container .item .name）。"""
    try:
        return page.evaluate(r"""
        () => Array.from(document.querySelectorAll('#creator-editor-topic-container .item .name'))
                 .map(e => (e.innerText || '').trim())
        """)
    except Exception:  # noqa: BLE001
        return []


def _cursor_at_editor_end(page):
    """光标后面还有没有内容？没有 = 在正文末尾（话题要插在这里）。"""
    try:
        return page.evaluate(r"""
        () => {
          const ed = document.querySelector('div.tiptap[contenteditable="true"]');
          const sel = document.getSelection();
          if (!ed || !sel || !sel.anchorNode) return false;
          if (!ed.contains(sel.anchorNode)) return false;
          const range = document.createRange();
          range.selectNodeContents(ed);
          range.setStart(sel.anchorNode, sel.anchorOffset);
          return range.toString().trim().length === 0;
        }
        """)
    except Exception:  # noqa: BLE001
        return False


def add_tags(page, tags):
    """话题：正文末尾逐个输入 #话题，等联想面板，点选同名话题变成真正的 topic 节点。

    实测（2026-11-05）：输入 # 会弹 tippy 浮层 #creator-editor-topic-container，
    .item .name 是候选名；点中后编辑器里生成 <a class="tiptap-topic" data-topic=...>。
    没有精确同名候选时保留纯文本并警告（不硬凑相似话题）。
    """
    if not tags:
        return
    tags = [t.strip().lstrip("#") for t in tags if str(t).strip()]
    log.info("添加 %d 个话题：%s", len(tags), ", ".join(tags))
    editor = find_editor(page)
    if editor is None:
        fail(page, "tags", "找不到正文编辑器，无法添加话题")
    try:
        editor.click()
        # 光标必须到「整篇正文末尾」：End 只是行尾，实测会把话题插到正文中间，
        # macOS 用 Cmd+↓ 到文末，其他平台用 Ctrl+End 兜底。
        for key in ("Meta+ArrowDown", "Control+End"):
            try:
                page.keyboard.press(key)
                nap(0.2, 0.4)
                break
            except Exception:  # noqa: BLE001
                continue
        nap(0.3, 0.6)
    except Exception as exc:  # noqa: BLE001
        fail(page, "tags", "正文编辑器聚焦失败：%s" % exc)

    before_text = ""
    try:
        before_text = editor.inner_text()
    except Exception:  # noqa: BLE001
        pass

    # 强校验：光标真的在正文末尾（后面没有内容），否则话题会被插到正文中间
    at_end = _cursor_at_editor_end(page)
    if not at_end:
        for key in ("Meta+ArrowDown", "Control+End"):
            try:
                page.keyboard.press(key)
                nap(0.3, 0.5)
                if _cursor_at_editor_end(page):
                    at_end = True
                    break
            except Exception:  # noqa: BLE001
                continue
    if not at_end:
        fail(page, "tags-cursor", "光标没能移到正文末尾，拒绝插入话题（避免把话题插进正文中间）")

    picked_tags, missed_tags = [], []
    for idx, tag in enumerate(tags):
        try:
            page.keyboard.press("Enter")  # 话题单独一段，避免和正文粘在一起
            nap(0.4, 0.8)
            page.keyboard.insert_text("#" + tag)
            # 等联想面板
            names = []
            for _ in range(14):
                names = _panel_topic_names(page)
                if names:
                    break
                time.sleep(0.3)
            if not names:
                log.warning("  ⚠ 话题 #%s 没弹出联想面板，保留纯文本", tag)
                missed_tags.append(tag)
                page.keyboard.insert_text(" ")
                continue
            exact = [i for i, nm in enumerate(names) if nm == "#" + tag]
            if not exact:
                log.warning("  ⚠ 话题 #%s 没有「完全同名」候选（面板前 3 个：%s），保留纯文本",
                            tag, names[:3])
                missed_tags.append(tag)
                page.keyboard.press("Escape")
                page.keyboard.insert_text(" ")
                continue
            item = page.locator("#creator-editor-topic-container .item").nth(exact[0])
            safe_click(item, "选择话题 #%s" % tag)
            nap(0.8, 1.4)
            if _topic_count(page) >= len(picked_tags) + 1:
                picked_tags.append(tag)
                log.info("  ✓ 话题 #%s 已变成真正的话题节点（tiptap-topic）", tag)
            else:
                log.warning("  ⚠ 话题 #%s 点了候选但没检测到 topic 节点，保留纯文本", tag)
                missed_tags.append(tag)
        except PublishError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("话题 #%s 添加异常：%s", tag, exc)
            missed_tags.append(tag)

    log.info("话题结果：成功 %d/%d（%s），未命中：%s",
             len(picked_tags), len(tags), "、".join(picked_tags) or "无",
             "、".join(missed_tags) or "无")
    # 位置校验：话题必须在正文内容之后
    try:
        final_text = editor.inner_text()
        if picked_tags:
            pos = final_text.find("#" + picked_tags[0])
            body_len_norm = _norm_len(before_text)
            before_len = _norm_len(final_text[:pos]) if pos >= 0 else -1
            if pos < 0 or before_len < max(2, int(body_len_norm * 0.9)):
                log.warning("⚠ 话题位置校验不通过：正文 %d 字，但第一个话题前只有 %d 字（可能插进正文中间）",
                            body_len_norm, before_len)
                REC.retry("话题插入位置可能异常：第一个话题前只有 %d 字（正文 %d 字）"
                          % (before_len, body_len_norm))
            else:
                log.info("  ✓ 话题位置校验通过：话题在正文之后（正文 %d 字）", body_len_norm)
    except Exception as exc:  # noqa: BLE001
        log.warning("话题位置校验异常：%s", exc)
    REC.selector("话题联想面板", "css", "#creator-editor-topic-container .item .name", True)
    step_shot(page, "06-tags")


def set_cover(page, index):
    """封面设置：**未实现（not implemented）**。

    原因：这次只实测到「封面预览」「获取封面建议」两个入口，没实测到
    「把第 N 张设为封面」的真实可点路径；按项目规矩不猜、不盲点。
    传了 --cover-index 就明确告知，不静默跳过。
    """
    if not index:
        return
    print("\n⚠️ 封面设置 **未实现（not implemented）**：", flush=True)
    print("   --cover-index %d 不会被执行。" % index, flush=True)
    print("   原因：只实测到「封面预览/获取封面建议」，没实测到「设为封面」的真实可点路径，不猜着点。",
          flush=True)
    print("   替代做法：脚本默认按 --images 顺序上传，第 1 张就是小红书默认封面；", flush=True)
    print("   如需指定别的封面，请在草稿箱/发布页里手动设置。", flush=True)
    log.warning("封面设置未实现（not implemented），已忽略 --cover-index %d", index)


# ---------------------------------------------------------------- 底部提交按钮（closed shadow DOM）

# 实测（2026-11-05）：底部「暂存离开 / 发布」在自定义元素 <xhs-publish-btn> 的
# **closed shadow root** 里：
#   <xhs-publish-btn submit-text="发布" save-text="暂存离开" ...>
#     #shadow-root (closed)
#       <div class="publish-page-publish-btn">
#         <button class="ce-btn white">暂存离开</button>
#         <button class="ce-btn bg-red">发布</button>
# 普通 locator / get_by_text 都查不到（closed shadow 无法渗透），
# 只能用 CDP DOM.getDocument(pierce=True) 穿透，拿 box model 后真实鼠标点击。

def _cdp_walk(node, out, depth=0, max_depth=14):
    if depth > max_depth:
        return
    out.append(node)
    for sr in node.get("shadowRoots") or []:
        _cdp_walk(sr, out, depth + 1, max_depth)
    for ch in node.get("children") or []:
        _cdp_walk(ch, out, depth, max_depth)


def _node_text(node):
    parts = []
    if node.get("nodeType") == 3:  # Text node
        parts.append(node.get("nodeValue") or "")
    for ch in node.get("children") or []:
        parts.append(_node_text(ch))
    return "".join(parts).strip()


def get_publish_buttons(cdp):
    """CDP 穿透 closed shadow DOM，返回底部按钮列表 [{text, x, y, w, h, node_id}]。"""
    cdp.send("DOM.enable")
    doc = cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
    nodes = []
    _cdp_walk(doc["root"], nodes)
    host = None
    for n in nodes:
        if (n.get("nodeName") or "").lower() == "xhs-publish-btn":
            host = n
            break
    if host is None:
        return []
    sub = []
    _cdp_walk(host, sub)
    btns = []
    for n in sub:
        if (n.get("nodeName") or "").lower() != "button":
            continue
        text = _node_text(n)
        attrs = n.get("attributes") or []
        disabled = "disabled" in attrs
        try:
            box = cdp.send("DOM.getBoxModel", {"nodeId": n["nodeId"]})
            q = box["model"]["border"]
            xs = [q[0], q[2], q[4], q[6]]
            ys = [q[1], q[3], q[5], q[7]]
            cx, cy = sum(xs) / 4.0, sum(ys) / 4.0
            w, h = max(xs) - min(xs), max(ys) - min(ys)
        except Exception:  # noqa: BLE001
            continue
        btns.append({"text": text, "x": cx, "y": cy, "w": w, "h": h,
                     "node_id": n.get("nodeId"), "disabled": disabled})
    return btns


def _shadow_click(page, cdp, target, purpose, allow_submit=False):
    """用真实鼠标点击 closed shadow DOM 里的按钮（坐标来自 CDP box model）。"""
    text = target["text"]
    if not allow_submit and SUBMIT_TEXT_RE.match(text):
        REC.click(purpose, text, allow_submit)
        raise PublishError(
            "safety",
            "安全闸门拦截：非 --publish 模式试图点击疑似发布按钮「%s」" % text,
        )
    # 坐标是文档坐标，先滚到顶部再点，保证等于视口坐标
    page.evaluate("() => window.scrollTo(0, 0)")
    nap(0.2, 0.4)
    nx, ny = target["x"], target["y"]
    page.mouse.move(nx, ny, steps=8)
    nap(0.15, 0.3)
    page.mouse.click(nx, ny)
    log.info("  ▶ 点击底部按钮「%s」（%s，坐标 %.0f,%.0f）",
             text, "允许提交" if allow_submit else "非提交", nx, ny)
    REC.click(purpose, text, allow_submit)


def click_draft(page, cdp, dry_run=False):
    """点「暂存离开」（小红的存草稿）。dry_run 时只定位不点击。"""
    log.info("%s「暂存离开」（存草稿）…", "定位（dry-run，不点击）" if dry_run else "点击")
    page.evaluate("() => window.scrollTo(0, 0)")
    nap(0.2, 0.4)
    btns = get_publish_buttons(cdp)
    if not btns:
        raise LookupError("读不到 <xhs-publish-btn> 内部按钮（closed shadow DOM 结构可能变了）")
    texts = [b["text"] for b in btns]
    info = {
        "button_text": "暂存离开",
        "clicked": False,
        "found_buttons": texts,
        "locator": "_CDP_ closed shadow DOM: xhs-publish-btn >> button.ce-btn.white(暂存离开)",
        "save_button": next((b for b in btns if b["text"] == "暂存离开"), None),
        "publish_button": next((b for b in btns if b["text"] in ("发布", "定时发布")), None),
    }
    if info["save_button"] is None:
        raise LookupError("xhs-publish-btn 里找不到「暂存离开」按钮，现有按钮：%s" % texts)
    if dry_run:
        log.info("  dry-run：定位到「暂存离开」按钮（坐标 %.0f,%.0f），未点击",
                 info["save_button"]["x"], info["save_button"]["y"])
        return info

    _shadow_click(page, cdp, info["save_button"], "暂存离开（存草稿）")
    info["clicked"] = True
    nap(1.2, 2.2)
    # 可能会弹二次确认（未实测过具体文案，按常见文案兜底；这些都不算「发布」）
    for txt in ("暂存并离开", "确定", "确认", "保存草稿", "暂存"):
        try:
            btn = page.get_by_text(txt, exact=True)
            if btn.count() > 0 and btn.first.is_visible():
                safe_click(btn.first, "存草稿确认弹窗：%s" % txt)
                nap(1.2, 2.2)
                log.info("  处理存草稿确认弹窗：%s", txt)
                break
        except PublishError:
            raise
        except Exception:  # noqa: BLE001
            continue
    nap(1.5, 3.0)
    step_shot(page, "07-draft-done")
    return info


def click_publish(page, cdp, dry_run=False, submit_text="发布"):
    """点底部提交按钮。submit_text 默认「发布」；定时发布模式下按钮文案是「定时发布」。"""
    log.info("%s「发布」…（这是真发布，会发到线上账号）",
             "定位（dry-run，不点击）" if dry_run else "点击")
    page.evaluate("() => window.scrollTo(0, 0)")
    nap(0.2, 0.4)
    btns = get_publish_buttons(cdp)
    if not btns:
        raise LookupError("读不到 <xhs-publish-btn> 内部按钮（closed shadow DOM 结构可能变了）")
    texts = [b["text"] for b in btns]
    target = next((b for b in btns if b["text"] == submit_text), None)
    if target is None:
        raise LookupError("xhs-publish-btn 里找不到「%s」按钮，现有按钮：%s" % (submit_text, texts))
    info = {"button_text": submit_text, "clicked": False, "found_buttons": texts,
            "locator": "_CDP_ closed shadow DOM: xhs-publish-btn >> button.ce-btn.bg-red(%s)" % submit_text}
    if dry_run:
        log.info("  dry-run：定位到「%s」按钮（坐标 %.0f,%.0f），未点击",
                 submit_text, target["x"], target["y"])
        return info

    _shadow_click(page, cdp, target, submit_text, allow_submit=True)
    info["clicked"] = True
    nap(1.5, 2.5)
    for txt in ("确认定时发布", "确认发布", "确定发布", "确定", "确认"):
        try:
            btn = page.get_by_text(txt, exact=True)
            if btn.count() > 0 and btn.first.is_visible():
                safe_click(btn.first, "发布确认弹窗：%s" % txt, allow_submit=True)
                nap(1.5, 2.5)
                break
        except PublishError:
            raise
        except Exception:  # noqa: BLE001
            continue
    step_shot(page, "08-published")
    return info


# ---------------------------------------------------------------- 定时发布（2026-11-05 实测）

# 实测 DOM：
#   <div class="post-time-wrapper">
#     <div class="custom-switch-card"><span class="has-tips">定时发布</span>
#       <div class="d-switch d-clickable d-switch-default"><input type="checkbox"></div></div>
#     <div class="date-picker-container">        ← 开关打开后才渲染
#       <div class="d-datepicker ..."><input class="d-text" value="2026-11-05 11:37"></div>
#     </div>
#   </div>
# 默认值 = 当前时间 + 1 小时；直接 fill "YYYY-MM-DD HH:MM" 后回车即可。
# 实测：填过去时间（09:00，当时 10:38）会被组件静默回退到默认值 → 所以必须回读校验。
SCHEDULE_SWITCH_TEXT = "定时发布"
SCHEDULE_PAST_HINTS = ("定时发布暂不可用", "暂不支持定时", "无法使用定时", "需要完成认证",
                       "请先完成实名", "账号不支持定时")


def parse_schedule(raw):
    """解析 --schedule：支持 "YYYY-MM-DD HH:MM" / "HH:MM"（=今天）。"""
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
    if m:
        now = datetime.datetime.now()
        return now.replace(hour=int(m.group(1)), minute=int(m.group(2)),
                           second=int(m.group(3) or 0), microsecond=0)
    raise SystemExit(
        "❌ --schedule 时间格式不对：%r\n"
        "   示例：--schedule \"2026-11-05 19:31\"（也支持只写 \"19:31\" 表示今天）" % raw)


def validate_schedule_target(dt):
    """时间校验：必须是将来的时间，且不能太远（平台上限未知，先按 14 天报警）。"""
    now = datetime.datetime.now()
    if dt <= now:
        raise SystemExit(
            "❌ 定时发布时间已经过了：%s（现在是 %s）。\n"
            "   请给一个未来的时间，脚本不会替你猜时间。"
            % (dt.strftime("%Y-%m-%d %H:%M"), now.strftime("%Y-%m-%d %H:%M")))
    if dt < now + datetime.timedelta(minutes=2):
        raise SystemExit(
            "❌ 定时发布时间太近：%s（现在 %s）。至少留 2 分钟，否则平台可能直接当立即发布。"
            % (dt.strftime("%Y-%m-%d %H:%M"), now.strftime("%Y-%m-%d %H:%M")))
    if dt > now + datetime.timedelta(days=14):
        log.warning("⚠ 定时时间 %s 距今超过 14 天，小红书可能不支持（先按平台回读结果为准）",
                    dt.strftime("%Y-%m-%d %H:%M"))
    return dt


def _page_body_text(page):
    """整页可见文本（容错）。"""
    try:
        return page.inner_text("body")
    except Exception:  # noqa: BLE001
        return ""


def _visible_texts(page, limit=150):
    try:
        return page.evaluate(r"""
        (limit) => {
          const out = [];
          document.querySelectorAll('body *').forEach(el => {
            if (el.children.length > 0) return;
            const t = (el.innerText || '').trim();
            if (!t || t.length > 40) return;
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0 || r.left < 0 || r.top < 0) return;
            out.push(t);
          });
          return out.slice(0, limit);
        }
        """, limit)
    except Exception:  # noqa: BLE001
        return []


def enable_schedule(page, target_dt):
    """开启定时发布并设置时间；回读校验（平台会静默回退过去时间）。"""
    target_str = target_dt.strftime("%Y-%m-%d %H:%M")
    log.info("开启「定时发布」并设置时间：%s", target_str)

    wrapper = page.locator(".post-time-wrapper").first
    if not wrapper.count():
        fail(page, "schedule", "找不到 .post-time-wrapper（定时发布区域），平台可能改版")
    wrapper.scroll_into_view_if_needed()
    nap(0.5, 1.0)

    card = page.locator(".custom-switch-card", has_text=SCHEDULE_SWITCH_TEXT).first
    if not card.count():
        fail(page, "schedule", "找不到「定时发布」开关卡片，平台可能改版")
    cb = card.locator("input[type=checkbox]").first
    if cb.count() == 0:
        fail(page, "schedule", "「定时发布」卡片里没有 checkbox，结构可能变了")

    if not cb.is_checked():
        safe_click(card.locator(".d-switch").first, "打开定时发布开关")
        nap(1.0, 1.8)
        for _ in range(12):
            if cb.is_checked():
                break
            time.sleep(0.3)
    if not cb.is_checked():
        fail(page, "schedule", "定时发布开关没有变成勾选状态")
    log.info("  ✓ 定时发布开关已打开")

    # 账号/内容不可用提示
    body = _page_body_text(page)
    for kw in SCHEDULE_PAST_HINTS:
        if kw in body:
            fail(page, "schedule", "平台提示「%s」——这个账号/内容不能用定时发布，停止，不硬写" % kw)

    # 等日期选择器渲染
    dp = page.locator(".date-picker-container").first
    for _ in range(20):
        if dp.count() and dp.first.is_visible():
            break
        time.sleep(0.4)
    if not dp.count():
        fail(page, "schedule", "定时发布开关打开了，但日期选择器没渲染出来")
    dp.scroll_into_view_if_needed()
    nap(0.4, 0.8)

    inp = dp.locator("input.d-text").first
    default_val = ""
    try:
        default_val = (inp.input_value() or "").strip()
    except Exception:  # noqa: BLE001
        pass
    log.info("  选择器默认值：%r", default_val)

    try:
        inp.click()
        nap(0.4, 0.8)
        inp.fill(target_str)
        nap(0.4, 0.8)
        inp.press("Enter")
        nap(0.8, 1.4)
        page.keyboard.press("Escape")
        nap(0.3, 0.6)
    except Exception as exc:  # noqa: BLE001
        fail(page, "schedule", "填写定时发布时间失败：%s" % exc)

    readback = ""
    try:
        readback = (inp.input_value() or "").strip()
    except Exception:  # noqa: BLE001
        pass
    info = {"target": target_str, "readback": readback, "default": default_val,
            "switch_checked": bool(cb.is_checked())}
    log.info("  回读值：%r", readback)
    if readback != target_str:
        fail(page, "schedule-verify",
             "平台没有接受这个时间：期望 %r，选择器回读 %r。\n"
             "  常见原因：时间已过 / 超出平台可选范围 / 格式不对。脚本不会自己改时间。"
             % (target_str, readback))

    # 二次确认：打开日历面板，确认面板里确实是这个日期和 时/分
    try:
        dp.locator(".d-datepicker-suffix-indicator").first.click()
        nap(0.8, 1.5)
        panel = " ".join(_visible_texts(page, 200))
        info["panel"] = {
            "has_date": (("%d年" % target_dt.year) in panel
                         and ("%d月" % target_dt.month) in panel
                         and ("%d" % target_dt.day) in panel),
            "has_hour": ("%d" % target_dt.hour) in panel,
            "has_minute": ("%d" % target_dt.minute) in panel,
            "raw_has_date_exact": target_str[:10] in panel,
        }
        page.keyboard.press("Escape")
        nap(0.3, 0.6)
        log.info("  日历面板校验：%s", info["panel"])
    except Exception as exc:  # noqa: BLE001
        log.warning("日历面板二次校验跳过：%s", exc)
        info["panel"] = None

    REC.selector("定时发布开关", "css", ".post-time-wrapper .custom-switch-card >> .d-switch", True)
    REC.selector("定时发布时间输入", "css", ".date-picker-container input.d-text", True)
    log.info("✓ 定时发布已开启并校验通过：%s", readback)
    return info


def _find_note_card(page, title):
    """在「笔记管理」列表里找标题匹配的卡片，返回 {found,title,time}。"""
    try:
        return page.evaluate(r"""
        (title) => {
          const norm = (s) => (s || '').replace(/\s+/g, '');
          const t = norm(title);
          const titles = Array.from(document.querySelectorAll('.note-card__title'));
          for (const el of titles) {
            if (norm(el.innerText).includes(t)) {
              let card = el.parentElement, time = '';
              for (let i = 0; i < 6 && card && card !== document.body; i++) {
                const te = card.querySelector('.note-card__time');
                if (te) { time = (te.innerText || '').trim(); break; }
                card = card.parentElement;
              }
              return {found: true, matched: el.innerText.trim(), time};
            }
          }
          return {found: false, total: titles.length};
        }
        """, title)
    except Exception as exc:  # noqa: BLE001
        return {"found": False, "error": str(exc)}


def verify_scheduled(page, title, target_str):
    """发布后去「笔记管理」确认定时记录存在，截图留证。"""
    log.info("去「笔记管理」验证定时发布记录…")
    out = {"found": False, "tab": None, "screenshot": None}
    try:
        page.get_by_text("笔记管理", exact=True).first.click(timeout=8000)
    except Exception as exc:  # noqa: BLE001
        log.warning("导航到「笔记管理」失败：%s", exc)
        out["error"] = str(exc)
        return out
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    nap(2.5, 3.5)

    for tab in ("定时发布", "审核中", "全部"):
        try:
            t = page.get_by_text(tab, exact=False)
            clicked = False
            for i in range(min(t.count(), 4)):
                el = t.nth(i)
                if el.is_visible():
                    el.click(timeout=3000)
                    clicked = True
                    break
            if not clicked:
                continue
            nap(1.5, 2.5)
            res = _find_note_card(page, title)
            log.info("  在「%s」tab 查找：%s", tab, res)
            if res.get("found"):
                out.update({"found": True, "tab": tab,
                            "matched": res.get("matched"), "time": res.get("time")})
                break
        except Exception as exc:  # noqa: BLE001
            log.warning("  查「%s」tab 失败：%s", tab, exc)

    out["screenshot"] = step_shot(page, "scheduled-verify")
    if out["found"]:
        log.info("✓ 笔记管理确认到定时发布记录：「%s」，列表时间 %r（tab=%s）",
                 out.get("matched"), out.get("time"), out.get("tab"))
        if target_str and out.get("time"):
            hhmm = target_str.split(" ")[-1]
            out["time_matches"] = hhmm in out["time"]
            if not out["time_matches"]:
                log.warning("⚠ 列表时间 %r 里没看到目标时间 %r（可能显示的是创建时间，需人工确认）",
                            out["time"], hhmm)
            else:
                log.info("  ✓ 列表时间包含目标时间 %s", hhmm)
    else:
        log.warning("⚠ 笔记管理里没找到标题匹配的定时记录（可能还在审核/列表未刷新）")
    return out


def report_state(page, images, title, body, tags):
    """收尾核对：把页面上实际填进去的东西打出来。"""
    try:
        txt = page.inner_text("body")
    except Exception:  # noqa: BLE001
        txt = ""
    log.info("—— 提交前核对 ——")
    log.info("图片 %d 张：%s", len(images), ", ".join(os.path.basename(p) for p in images))
    log.info("标题：%s", title)
    log.info("正文 %d 字", len(body))
    log.info("话题 %d 个：%s", len(tags), ", ".join(tags))
    log.info("页面 URL：%s", page.url)
    for kw in ("草稿", "发布成功", "保存成功", "已保存"):
        if kw in txt:
            log.info("页面出现关键字：%s", kw)
    log.info("—— 核对结束 ——")


def main():
    ap = argparse.ArgumentParser(description="小红书图文笔记发布（默认存草稿）")
    ap.add_argument("--images", help="图片：目录 / 逗号分隔路径 / 通配符（也可由 --meta 提供）")
    ap.add_argument("--title", help="标题（≤20 字）（也可由 --meta 提供）")
    ap.add_argument("--body", default="", help="正文文本，或一个 .txt 文件路径")
    ap.add_argument("--tags", default="", help="话题，逗号分隔，不带 #")
    ap.add_argument("--meta", default=None,
                    help="可选：render 产出的 meta.json，自动读 title/images/tags（CLI 参数优先）")
    ap.add_argument("--draft", action="store_true", help="存草稿（默认行为，可不写）")
    ap.add_argument("--publish", action="store_true", help="立即发布（危险，需显式指定）")
    ap.add_argument("--schedule", default=None,
                    help="定时发布：\"YYYY-MM-DD HH:MM\"（也支持 \"HH:MM\"=今天）。"
                         "填完内容→打开定时发布→设好时间→点发布，由平台到点自动发")
    ap.add_argument("--headed", action="store_true", help="有头模式，能看到浏览器操作")
    ap.add_argument("--cover-index", type=int, default=0, help="把第 N 张图设为封面（1 起）")
    ap.add_argument("--dump", action="store_true", help="只在发布页 dump DOM，不填内容")
    ap.add_argument("--dry-run", action="store_true",
                    help="空跑：一路填好内容（含定时发布设置），停在最后一步，不点任何提交类按钮")
    ap.add_argument("--report", action="store_true",
                    help="结束时把本次运行报告 JSON 打印到终端（报告本身每次都会写进 logs/）")
    args = ap.parse_args()

    script_dir = str(Path(__file__).resolve().parent)
    meta = load_meta(args.meta, script_dir)

    schedule_dt = parse_schedule(args.schedule) if args.schedule else None
    if schedule_dt is not None:
        schedule_dt = validate_schedule_target(schedule_dt)
        if args.publish:
            raise SystemExit("❌ --schedule 和 --publish 不能同时用（--schedule 本身就是「定时发布」）")
        if args.draft:
            log.warning("--schedule 模式下 --draft 不生效")

    if args.dump:
        # 只 dump 页面结构时，不需要图片和标题
        images, title, body, tags = [], "", "", []
    else:
        if args.images:
            images = resolve_images(args.images, script_dir)
        elif meta.get("_images_resolved"):
            images = validate_images(meta["_images_resolved"])
        else:
            raise SystemExit("❌ 必须给 --images（或 --meta 里带 images）")

        title = normalize_title(args.title or meta.get("title") or "")
        if not title:
            raise SystemExit("❌ 必须给 --title（或 --meta 里带 title）")
        body = read_body(args.body, script_dir) or (meta.get("body") or "")
        tags = normalize_tags(args.tags) or normalize_tags(meta.get("tags"))
        if not body:
            log.warning("正文为空（meta.json 里没有 body，也没给 --body）——小红书可能不允许空正文，建议补上")

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    if schedule_dt is not None:
        mode = "schedule-dry" if args.dry_run else "schedule"
    elif args.publish:
        mode = "publish"
    elif args.dry_run:
        mode = "dry-run"
    else:
        mode = "draft"
    REC.meta = {
        "mode": mode,
        "images": [os.path.basename(p) for p in images],
        "image_count": len(images),
        "title": title,
        "title_len": len(title),
        "body_len": len(body),
        "tags": tags,
        "meta_json": args.meta,
        "dry_run": bool(args.dry_run),
        "publish": bool(args.publish),
        "schedule": schedule_dt.strftime("%Y-%m-%d %H:%M") if schedule_dt else None,
        "headed": bool(args.headed),
    }
    log.info("=" * 60)
    log.info("小红书发布：%d 张图 / 标题 %d 字 / 正文 %d 字 / 话题 %d 个",
             len(images), len(title), len(body), len(tags))
    log.info("模式：%s", {"publish": "【立即发布】",
                          "dry-run": "【空跑：只填内容，不点任何提交按钮】",
                          "draft": "【存草稿】（默认，安全）",
                          "schedule": "【定时发布】%s" % (schedule_dt.strftime("%Y-%m-%d %H:%M")
                                                          if schedule_dt else ""),
                          "schedule-dry": "【定时发布·空跑：只设置不点发布】"}[mode])
    for p in images:
        log.info("  - %s", p)
    log.info("=" * 60)

    def finish(code, result, extra=None):
        report = REC.save(result, extra)
        if report:
            print("\n📄 运行报告（可回溯证据）：%s" % report, flush=True)
            if args.report:
                print(json.dumps(REC.to_dict(result, extra), ensure_ascii=False, indent=2),
                      flush=True)
        return code

    with chrome_context(headless=not args.headed) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        cdp = ctx.new_cdp_session(page)  # 穿透 closed shadow DOM 用
        try:
            with REC.step("navigate"):
                goto_publish_page(page)
                assert_logged_in(page, ctx)

            if args.dump:
                dump = dump_debug(page, "publish-page-manual", note="--dump")
                print("\nDOM dump：%s" % dump.get("txt"))
                return finish(0, "dump-ok", {"dump": dump})

            with REC.step("upload"):
                upload_images(page, images)
            with REC.step("title"):
                fill_title(page, title)
            with REC.step("body"):
                fill_body(page, body)
            with REC.step("tags"):
                add_tags(page, tags)
            with REC.step("cover"):
                set_cover(page, args.cover_index)
            with REC.step("report-state"):
                report_state(page, images, title, body, tags)

            schedule_info = None
            if schedule_dt is not None:
                with REC.step("schedule"):
                    schedule_info = enable_schedule(page, schedule_dt)

            if args.publish:
                with REC.step("publish"):
                    click_publish(page, cdp)
                log.info("✅ 已点击发布（立即发布）。")
                final = shot(page, "final")
                return finish(0, "published", {"final_screenshot": final})

            if mode == "schedule" and not args.dry_run:
                with REC.step("publish-scheduled"):
                    click_publish(page, cdp, submit_text="定时发布")
                target = schedule_info.get("readback") if schedule_info else "?"
                log.info("✅ 已点击「发布」，定时时间：%s", target)
                print("\n✓ 已提交定时发布：%s（小红书会到点自动发出）" % target, flush=True)
                nap(2.0, 3.0)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:  # noqa: BLE001
                    pass
                final = shot(page, "final-scheduled")
                REC.screenshot(final)
                verify = verify_scheduled(page, title, target)
                if not verify.get("found"):
                    print("\n⚠ 已点【发布】，但「笔记管理」里没验证到定时记录（可能还在审核/列表未刷新）。\n"
                          "   请手动去 creator.xiaohongshu.com → 笔记管理 → 定时发布 确认。\n"
                          "   在确认之前**不要重复发布**，否则会发两条。", flush=True)
                    return finish(1, "scheduled-unverified",
                                  {"schedule": schedule_info, "verify": verify,
                                   "final_screenshot": final})
                print("\n✓ 笔记管理已确认到定时发布记录（tab=%s，列表时间=%r）"
                      % (verify.get("tab"), verify.get("time")), flush=True)
                print("   截图：%s" % verify.get("screenshot"), flush=True)
                return finish(0, "scheduled",
                              {"schedule": schedule_info, "verify": verify,
                               "final_screenshot": final})

            if args.dry_run:
                # 空跑：已填好全部内容，只定位「暂存离开」按钮，绝不点击
                with REC.step("dry-run-stop"):
                    draft_btn = click_draft(page, cdp, dry_run=True)
                    publish_info = {
                        "found": draft_btn.get("publish_button") is not None,
                        "button_text": "发布",
                        "button": draft_btn.get("publish_button"),
                    }
                dry_shot = shot(page, "dry-run-final")
                REC.screenshot(dry_shot)
                print("\n" + "=" * 66)
                print("🧪 DRY-RUN：内容已全部填好，**没有点击任何提交类按钮**")
                print("=" * 66)
                print("已填内容清单：")
                print("  图片：%d 张" % len(images))
                for i, p in enumerate(images, 1):
                    print("    %2d. %s" % (i, os.path.basename(p)))
                print("  标题：%s（%d 字）" % (title, len(title)))
                print("  正文：%d 字" % len(body))
                print("  话题：%s（实际变成话题节点的数量见下方安全日志）"
                      % ("、".join(tags) if tags else "（无）"))
                if schedule_info:
                    print("  定时发布：已开启，时间回读 %r（平台默认值 %r）"
                          % (schedule_info.get("readback"), schedule_info.get("default")))
                    print("  ⏳ dry-run：没有点【发布】，所以不会真的定时发出去")
                print("\n停在最后一步，本来要点的按钮：")
                save_b = draft_btn.get("save_button") or {}
                print("  「暂存离开」（=存草稿）——文案 %r，坐标 %.0f,%.0f"
                      % (draft_btn.get("button_text"), save_b.get("x", -1), save_b.get("y", -1)))
                print("  定位方式：%s" % draft_btn.get("locator"))
                print("  该元素里现有按钮：%s" % draft_btn.get("found_buttons"))
                pub_b = publish_info.get("button") or {}
                if publish_info["found"]:
                    print("  真发布时的按钮（只定位、没点）：「%s」坐标 %.0f,%.0f"
                          % (pub_b.get("text") or publish_info.get("button_text"),
                             pub_b.get("x", -1), pub_b.get("y", -1)))
                else:
                    print("  真发布时的按钮：没定位到（结构可能变了，届时看 logs 里 dump）")
                print("\n当前页面截图：%s" % dry_shot)
                print("本次点击审计：")
                for c in REC.clicks:
                    print("  - %s「%s」allow_submit=%s" % (c["step"], c["text"], c["allow_submit"]))
                assert not any(c["allow_submit"] for c in REC.clicks), \
                    "dry-run 竟然出现了 allow_submit 的点击"
                print("\n✅ 安全断言通过：全程没有 allow_submit 的点击。")
                return finish(0, "dry-run-ok",
                              {"draft_button": draft_btn, "publish_button": publish_info,
                               "final_screenshot": dry_shot})

            with REC.step("draft"):
                click_draft(page, cdp)
            # 硬断言：草稿模式不允许出现任何 allow_submit 的点击
            bad = [c for c in REC.clicks if c["allow_submit"]]
            if bad:
                raise PublishError("safety", "草稿模式出现了发布类点击：%s" % bad)
            log.info("安全断言通过：全程未点击任何「发布」类按钮（0 次 allow_submit）")
            print("\n✓ 已存入草稿箱。请到 creator.xiaohongshu.com → 笔记管理 → 草稿箱 "
                  "打开检查，确认无误后由你本人点击【发布】。本脚本未点击任何发布按钮。",
                  flush=True)

            final = shot(page, "final")
            log.info("最终截图：%s", final)
            return finish(0, "draft-ok", {"final_screenshot": final,
                                          "submit_clicks": 0})

        except PublishError as exc:
            log.error("❌ 失败：%s", exc)
            print("\n❌ 发布流程失败（步骤 %s）：%s" % (exc.step, exc.message))
            print("   证据都在 logs/ 里，把 dump-*.txt 发给维护者即可定位。")
            return finish(1, "error", {"error_step": exc.step, "error": exc.message})
        except LookupError as exc:
            p = shot(page, "lookup-failed")
            REC.screenshot(p)
            log.error("❌ 定位失败：%s", exc)
            print("\n❌ 页面元素没找到：%s" % exc)
            print("   截图：%s" % p)
            print("   修复办法：运行 explore.py --url https://creator.xiaohongshu.com/publish/publish，")
            print("   把新的 DOM 结论补进 SELECTORS.md / publish.py 的候选选择器。")
            return finish(1, "lookup-error", {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            p = shot(page, "unexpected")
            dump = dump_debug(page, "unexpected", note=str(exc))
            REC.screenshot(p)
            log.exception("❌ 未预期错误：%s", exc)
            print("\n❌ 未预期错误：%s" % exc)
            print("   截图：%s" % p)
            print("   dump：%s" % dump.get("txt"))
            return finish(1, "unexpected-error", {"error": str(exc)})


if __name__ == "__main__":
    sys.exit(main())
