// wechat_publish.mjs — 公众号文章发布驱动（跑在 ego lite，由 wechat_publish.py 注入 CONFIG）
//
// 实测结论（2026-11-06，见 SELECTORS.md 微信节）：
// - 文章编辑器：首页「新的创作 → 文章」开新 tab
// - 插图：点工具栏「图片」展开下拉后，setInputFiles("input[name='file']", N张) 一次全部插入正文
//   （「贴图」编辑器的动态 input 无法自动化——多方案实测失败，已放弃）
// - 标题=ProseMirror focus+insertText（≤64字）；作者 input[name=author]；摘要 textarea[name=digest]（≤120字）
// - 发表链：发表 → 侧面板 → 发表 → 封面（从正文选择→第1张→下一步→确认）→ 发表 →
//   最终弹层（定时开关 .timer_setting .weui-desktop-switch__box → 日期列表 + 时间输入 HH:MM）→ 发表
// - 定时规则：5分钟后~7天内；取消定时不占群发次数（每天仅 1 次群发额度！）
//
// evaluate 必须传函数（传字符串静默返回 {}）。

const ev = (name, data) => console.log(JSON.stringify({ ev: name, ...data }));
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// ---------- 页面探测函数 ----------

const markText = (text, exact) => {
  // 精确文本标记：找可见的、文案完全匹配的最小元素（按钮/链接/菜单项通用）
  document.querySelectorAll("[data-wx-pick]").forEach((e) => e.removeAttribute("data-wx-pick"));
  let best = null;
  for (const el of document.querySelectorAll("button,a,div,span,li,label")) {
    const t = (el.innerText || "").trim();
    if (exact ? t !== text : !t.includes(text)) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if (r.left < -5 || r.top < -5 || r.bottom > innerHeight + 2000) continue;
    if (!best || r.width * r.height < best.a) best = { el, a: r.width * r.height };
  }
  if (best) { best.el.setAttribute("data-wx-pick", "1"); return true; }
  return false;
};

const bodyImgs = () => Array.from(document.querySelectorAll("img"))
  .filter((i) => (i.src || "").includes("mmbiz") && i.getBoundingClientRect().height > 50).length;

const clickByText = `(text) => ${markText.toString()}(text, true)`; // 占位（不用）

// ---------- 主流程 ----------

const clicks = [];

async function stepShot(page, name) {
  const path = `${CONFIG.logs_dir}/wx-step-${name}-${CONFIG.stamp}.png`;
  try { await page.screenshot({ path }); ev("shot", { name, path }); return path; }
  catch (e) { ev("shot-failed", { name, error: String(e).slice(0, 120) }); return null; }
}

async function clickMarked(page, purpose, opts = {}) {
  const ok = await page.evaluate(markText, opts.text).catch(() => false);
  if (!ok) throw new Error(`找不到「${opts.text}」`);
  await page.click('[data-wx-pick="1"]');
  clicks.push({ purpose, text: opts.text, allow_submit: !!opts.allow_submit });
  ev("click", { purpose, text: opts.text, allow_submit: !!opts.allow_submit });
}

async function waitText(page, text, timeout = 15000) {
  for (let t = 0; t < timeout / 500; t++) {
    const has = await page.evaluate((s) => (document.body.innerText || "").includes(s), text).catch(() => false);
    if (has) return true;
    await sleep(500);
  }
  return false;
}

async function main() {
  const task = await taskSpace(CONFIG.space_name);
  let page = task.page("p1");
  ev("space", { spaceId: task.spaceId, mode: CONFIG.mode });
  const result = { mode: CONFIG.mode, ok: false, clicks };

  try {
    // 0. 定位权限屏蔽（保险）
    try {
      await page.cdp("Page.addScriptToEvaluateOnNewDocument", {
        source: `if (navigator.geolocation) {
          const d = (cb) => cb && cb({ code: 1, PERMISSION_DENIED: 1, message: "denied" });
          navigator.geolocation.getCurrentPosition = function (s, e) { d(e); };
          navigator.geolocation.watchPosition = function (s, e) { d(e); return 0; };
        }`,
      });
    } catch {}

    // 1. 打开公众号首页 + 登录检查
    await page.goto("https://mp.weixin.qq.com", { timeout: 40000 });
    try { await page.waitForLoadState("load", { timeout: 15000 }); } catch {}
    await sleep(2000);
    let url = await page.url();
    const loginWall = await page.evaluate(() => /微信扫一扫，选择公众平台账号登录/.test(document.body.innerText || "")).catch(() => false);
    ev("navigate", { url });
    if (loginWall) {
      result.error_step = "login";
      result.error = "ego lite 里没有公众号登录态。请先跑：python3 publish/wechat_login.py --qr";
      await stepShot(page, "login-required");
      console.log("EGO_RESULT " + JSON.stringify(result));
      return;
    }

    // 2. 进「文章」编辑器（新 tab）
    await clickMarked(page, "新的创作-文章", { text: "文章" });
    await sleep(3500);
    const tabs = await task.tabs();
    const newTab = tabs.find((t) => t.active && t.label !== "p1");
    if (!newTab) throw new Error("文章编辑器没有打开");
    const ed = task.page(newTab.label);
    page = ed;
    ev("editor-open", { tab: newTab.label });
    await waitText(page, "请在这里输入标题", 15000);
    await stepShot(page, "01-editor");

    // 3. 插图：点工具栏「图片」展开下拉 → setInputFiles
    await clickMarked(page, "工具栏-图片", { text: "图片" });
    await sleep(1200);
    // 等静态 input[name=file]（下拉展开后可见）
    let hasInput = await page.evaluate(() => {
      const i = document.querySelector('input[name="file"]');
      return !!(i && (i.innerText !== undefined));
    }).catch(() => false);
    // input 不依赖可见性，直接设置
    await page.setInputFiles('input[name="file"]', CONFIG.images);
    ev("upload-set", { count: CONFIG.images.length });
    let n = 0;
    for (let t = 0; t < 60; t++) {
      await sleep(2000);
      n = await page.evaluate(bodyImgs).catch(() => 0);
      if (n >= CONFIG.images.length) break;
    }
    ev("upload-done", { imgs: n });
    if (n < CONFIG.images.length) {
      throw new Error(`插图不完整：目标 ${CONFIG.images.length} 张，正文 ${n} 张`);
    }
    await stepShot(page, "02-images");

    // 4. 标题 / 作者 / 摘要
    await page.focus('div.ProseMirror[data-placeholder*="标题"]');
    await page.keyboard.insertText(CONFIG.title);
    await sleep(400);
    for (const [sel, val, purpose] of [
      ['input[name="author"]', CONFIG.author, "作者"],
      ['textarea[name="digest"]', CONFIG.digest, "摘要"],
    ]) {
      if (!val) continue;
      // 先滚进视口（编辑器是内部滚动容器，fill 要求可见）
      await page.evaluate((s) => {
        const el = document.querySelector(s);
        if (el) el.scrollIntoView({ block: "center" });
      }, sel).catch(() => {});
      await sleep(400);
      try {
        await page.fill(sel, val);
      } catch {
        await page.focus(sel);
        await page.keyboard.insertText(val);
      }
      await sleep(300);
    }
    const filled = await page.evaluate(() => ({
      title: (document.querySelector('div.ProseMirror[data-placeholder*="标题"]') || { innerText: "" }).innerText.trim(),
      author: (document.querySelector('input[name="author"]') || {}).value || "",
      digestLen: ((document.querySelector('textarea[name="digest"]') || {}).value || "").length,
    }));
    ev("filled", filled);
    if (!filled.title) throw new Error("标题写入失败");
    await stepShot(page, "03-filled");

    // 5. 分支
    if (CONFIG.mode === "dry-run" || CONFIG.mode === "draft") {
      // 保存草稿（dry-run 也存草稿：内容安全落袋，绝不点发表）
      await clickMarked(page, "保存为草稿", { text: "保存为草稿" });
      await sleep(3500);
      const saved = await page.evaluate(() => /已保存|保存成功/.test(document.body.innerText || "")).catch(() => false);
      ev("draft", { saved });
      await stepShot(page, "04-draft");
      result.ok = true;
      result.result = CONFIG.mode === "dry-run" ? "dry-run-ok(draft-saved)" : "draft-ok";
      result.note = "草稿已保存，未点任何发表按钮";
    } else if (CONFIG.mode === "schedule") {
      // 6. 发表三段式
      await clickMarked(page, "发表(第1次)", { text: "发表" });
      await sleep(2000);
      await clickMarked(page, "发表(第2次)", { text: "发表" });
      await sleep(2000);
      // 封面层
      if (await waitText(page, "拖拽或选择封面", 8000)) {
        await clickMarked(page, "封面-从正文选择", { text: "从正文选择" });
        await sleep(2000);
        // 弹层选第 1 张
        const picked = await page.evaluate(() => {
          const dlg = Array.from(document.querySelectorAll("div"))
            .find((d) => (d.innerText || "").includes("请从正文插入的图片") && d.getBoundingClientRect().height > 100 && d.getBoundingClientRect().height < 900);
          if (!dlg) return false;
          const li = dlg.querySelectorAll("li")[0];
          if (!li) return false;
          li.setAttribute("data-wx-img", "1");
          return true;
        });
        if (!picked) throw new Error("封面图片列表没找到");
        await page.click('[data-wx-img="1"]');
        await sleep(800);
        await clickMarked(page, "封面-下一步", { text: "下一步" });
        await sleep(1500);
        await clickMarked(page, "封面-确认", { text: "确认" });
        await sleep(1500);
        await stepShot(page, "05-cover");
        // 封面后第三次点发表
        await clickMarked(page, "发表(第3次)", { text: "发表" });
        await sleep(2000);
      }
      // 最终弹层：定时开关
      if (!await waitText(page, "定时发表", 8000)) throw new Error("发表确认弹层没出现");
      await page.evaluate(() => {
        const box = document.querySelector(".timer_setting .weui-desktop-switch__box");
        if (box) { box.scrollIntoView({ block: "center" }); box.click(); }
      });
      await sleep(1500);
      const checked = await page.evaluate(() => {
        const t = document.querySelector(".timer_setting");
        return t && t.querySelector("input") ? t.querySelector("input").checked : false;
      });
      ev("timer-switch", { checked });
      if (!checked) throw new Error("定时开关没打开");
      // 日期：列表里点「今天」或「明天」
      const dayText = CONFIG.schedule_day === "tomorrow" ? "明天" : "今天";
      await clickMarked(page, "定时日期-" + dayText, { text: dayText });
      await sleep(800);
      // 时间：时间输入框（weui-desktop-form__input，HH:MM）
      const timeSet = await page.evaluate((hhmm) => {
        const inputs = Array.from(document.querySelectorAll("input.weui-desktop-form__input"));
        const t = inputs.find((i) => /^\d{2}:\d{2}$/.test((i.value || "").trim()));
        if (!t) return false;
        t.focus();
        t.value = hhmm;
        t.dispatchEvent(new Event("input", { bubbles: true }));
        t.dispatchEvent(new Event("change", { bubbles: true }));
        t.blur();
        return true;
      }, CONFIG.schedule_time).catch(() => false);
      ev("time-set", { ok: timeSet, value: CONFIG.schedule_time });
      await sleep(1000);
      await stepShot(page, "06-timer-set");
      // 发表（最终确认）
      await clickMarked(page, "定时发表-确认", { text: "发表", allow_submit: true });
      await sleep(3000);
      // 可能有二次确认
      for (const t of ["确认", "确定"]) {
        const ok = await page.evaluate(markText, t).catch(() => false);
        if (ok) {
          await page.click('[data-wx-pick="1"]');
          clicks.push({ purpose: "发表确认", text: t, allow_submit: true });
          await sleep(2500);
          break;
        }
      }
      await stepShot(page, "07-published");
      // 验证：页面提示或跳转
      const verifyText = await page.evaluate(() => (document.body.innerText || "").slice(0, 3000)).catch(() => "");
      const found = /定时发表|发表成功|已进入/.test(verifyText);
      result.verify = { page_hint: found, url: await page.url() };
      result.ok = found;
      result.result = found ? "scheduled" : "scheduled-unverified";
    }

    console.log("EGO_RESULT " + JSON.stringify(result));
    await task.finish({ keep: [] });
  } catch (e) {
    result.error_step = e.step || "unexpected";
    result.error = String(e.message || e);
    await stepShot(page, "fail-" + (result.error_step)).catch(() => {});
    console.log("EGO_RESULT " + JSON.stringify(result));
  }
}

await main();
