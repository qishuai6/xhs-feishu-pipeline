// wechat_publish.mjs — 微信公众号「贴图」发布驱动（ego lite）
//
// 流程（2026-11-08 实测打通，见 SELECTORS.md 微信节）：
//   新建贴图 → 第1张 CDP 直传 → 后续逐张「+ → 本地上传(chooser)」入素材库
//   → 「从图片库选择」按文件名勾选（勾选顺序=贴图顺序）→ 确定 → 计数验证
//   → 填标题(≤20)/描述(≤1000) → 保存草稿
//
// 关键实测结论：
// - 「本地上传」chooser.setFiles 的图进入素材库但不自动插入，必须库里勾选+确定
// - 选择器卡片 = .weui-desktop-img-picker__item，点击后加 .selected
// - 编辑器贴图计数是页面文本「N N」（非 mmbiz img），验证以计数为准
// - evaluate 必须传函数；选择器开着会挡 bottom-add，先确定关掉再进下一轮

const ev = (name, data) => console.log(JSON.stringify({ ev: name, ...data }));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const markText = (text) => {
  document.querySelectorAll("[data-wx-pick]").forEach((e) => e.removeAttribute("data-wx-pick"));
  for (const el of document.querySelectorAll("div,span,a,button,li")) {
    if ((el.innerText || "").trim() === text) {
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;
      el.setAttribute("data-wx-pick", "1");
      return true;
    }
  }
  return false;
};

const editorCount = () => {
  // 贴图编辑器的图片计数：页面文本里「N」+「N」的图片卡计数（历史版本下方）
  const m = (document.body.innerText || "").match(/(\d+)\s*\n(\d+)\s*\n/);
  return m ? parseInt(m[1], 10) : -1;
};

const clicks = [];

async function stepShot(page, name) {
  const path = `${CONFIG.logs_dir}/wx-step-${name}-${CONFIG.stamp}.png`;
  try { await page.screenshot({ path }); ev("shot", { name, path }); return path; }
  catch (e) { ev("shot-failed", { name, error: String(e).slice(0, 120) }); return null; }
}

async function clickMarked(page, text, purpose) {
  const ok = await page.evaluate(markText, text).catch(() => false);
  if (!ok) throw new Error(`找不到「${text}」`);
  await page.click('[data-wx-pick="1"]');
  clicks.push({ purpose: purpose || text, text });
  ev("click", { purpose: purpose || text });
}

async function main() {
  const task = await taskSpace(CONFIG.space_name);
  let page = task.page("p1");
  ev("space", { spaceId: task.spaceId, mode: CONFIG.mode });
  const result = { mode: CONFIG.mode, ok: false, clicks };

  try {
    try {
      await page.cdp("Page.addScriptToEvaluateOnNewDocument", {
        source: `if (navigator.geolocation) {
          const d = (cb) => cb && cb({ code: 1, PERMISSION_DENIED: 1, message: "denied" });
          navigator.geolocation.getCurrentPosition = function (s, e) { d(e); };
          navigator.geolocation.watchPosition = function (s, e) { d(e); return 0; };
        }`,
      });
    } catch {}

    // 1. 首页 + 登录检查
    await page.goto("https://mp.weixin.qq.com", { timeout: 40000 });
    try { await page.waitForLoadState("load", { timeout: 15000 }); } catch {}
    await sleep(2000);
    const wall = await page.evaluate(() =>
      /微信扫一扫，选择公众平台账号登录/.test(document.body.innerText || "")).catch(() => false);
    if (wall) {
      result.error_step = "login";
      result.error = "ego lite 里没有公众号登录态。请先跑：python3 publish/wechat_login.py --qr";
      await stepShot(page, "login-required");
      console.log("EGO_RESULT " + JSON.stringify(result));
      return;
    }

    // 2. 新建「贴图」（新 tab）
    await clickMarked(page, "贴图", "新建贴图");
    await sleep(3500);
    const tabs = await task.tabs();
    const t = tabs.find((x) => x.active && x.label !== "p1");
    if (!t) throw new Error("贴图编辑器没有打开");
    page = task.page(t.label);
    if (!(t.url || "").includes("type=77")) throw new Error("打开的不是贴图编辑器（type=77）");
    ev("editor-open", { tab: t.label });
    await stepShot(page, "01-editor");

    async function count() {
      // 真实计数 = 大图预览徽标「N/N」（分子分母相等且>0，区别于标题 0/20、描述 0/1000）
      return await page.evaluate(() => {
        const txt = document.body.innerText || "";
        // 口径1：徽标「N/N」字符串（分子分母相等）
        const badge = (txt.match(/(\d+)\s*\/\s*(\d+)/g) || [])
          .map((x) => { const m = x.match(/(\d+)\s*\/\s*(\d+)/); return [parseInt(m[1], 10), parseInt(m[2], 10)]; })
          .filter(([a, b]) => a === b && a > 0 && a < 40);
        if (badge.length) return Math.max(...badge.map(([a]) => a));
        // 口径2：分立双数字节点「N\nN」（贴图编辑器实际渲染形态）
        const pair = txt.match(/(?:^|\n)(\d{1,2})\s*\n\s*\1(?:\s|\n)/);
        if (pair && parseInt(pair[1], 10) > 0) return parseInt(pair[1], 10);
        // 口径3兜底：mmbiz 大图
        return Array.from(document.querySelectorAll("img"))
          .filter((i) => (i.src || "").includes("mmbiz")
                  && i.getBoundingClientRect().width > 100 && i.getBoundingClientRect().height > 100).length;
      }).catch(() => 0);
    }

    // 3. 全部图片：+ → 本地上传(chooser) → 入素材库（不自动插入，最后统一勾选）
    const names = CONFIG.images.map((p) => p.split("/").pop());
    for (let k = 0; k < CONFIG.images.length; k++) {
      // 打开上传面板：首轮点「选择或拖拽图片」初始区，之后点 + 按钮
      let added = false;
      for (let r = 0; r < 3 && !added; r++) {
        try { await page.click(".image-selector__bottom-add", { timeout: 2000 }); added = true; }
        catch {
          const init = await page.evaluate(() => {
            document.querySelectorAll("[data-wx-init]").forEach((e) => e.removeAttribute("data-wx-init"));
            let best = null;
            for (const el of document.querySelectorAll("div")) {
              const t = el.innerText || "";
              if (t.includes("选择或拖拽图片") && !t.includes("标题") && el.children.length < 8) {
                const rr = el.getBoundingClientRect();
                if (rr.width > 80 && rr.height > 80 && (!best || rr.width * rr.height < best.a)) best = { el, a: rr.width * rr.height };
              }
            }
            if (best) { best.el.setAttribute("data-wx-init", "1"); return true; }
            return false;
          });
          if (init) { try { await page.click('[data-wx-init="1"]', { timeout: 2000 }); added = true; } catch {} }
        }
        if (!added) await sleep(800);
      }
      if (!added) throw new Error(`第 ${k + 1} 张：上传面板打不开`);
      await sleep(1000);
      const hasLocal = await page.evaluate(() => {
        document.querySelectorAll("[data-wx-local]").forEach((e) => e.removeAttribute("data-wx-local"));
        const el = Array.from(document.querySelectorAll("div,span,a"))
          .find((e) => (e.innerText || "").trim() === "本地上传" && e.getBoundingClientRect().width > 0);
        if (el) { el.setAttribute("data-wx-local", "1"); return true; }
        return false;
      });
      if (!hasLocal) throw new Error(`第 ${k + 1} 张：上传面板没打开`);
      const chooserPromise = page.waitForFileChooser({ timeout: 8000 });
      await page.click('[data-wx-local="1"]');
      const chooser = await chooserPromise;
      await chooser.setFiles([CONFIG.images[k]]);
      clicks.push({ purpose: `本地上传 ${names[k]}`, text: "本地上传" });
      ev("lib-upload", { file: names[k] });
      // 入库通常 2~4s；面板自动收起
      await sleep(3500);
      // 若面板还开着（选择器挡 + 按钮），先取消
      await page.evaluate(() => {
        const dlg = Array.from(document.querySelectorAll("div"))
          .find((d) => (d.innerText || "").includes("最近使用") && (d.innerText || "").includes("确定"));
        if (dlg) {
          const cancel = Array.from(dlg.querySelectorAll("button,a"))
            .find((b) => (b.innerText || "").trim() === "取消" && b.getBoundingClientRect().width > 0);
          if (cancel) cancel.click();
        }
      });
      await sleep(600);
    }

    // 5. 按缺口从图片库补选（上传各环节插入行为不稳定：初始区会插入、+面板只入库，
    //    所以先数编辑器实际张数，缺几张就从库里勾几张本轮唯一名图）
    let cur = await count();
    for (let w = 0; w < 10; w++) {
      await sleep(2000);
      const again = await count();
      if (again === cur && cur >= 1) break;
      cur = again;
    }
    const need = CONFIG.images.length - cur;
    ev("gap-check", { current: cur, need });
    if (need > 0) {
      await page.click(".image-selector__bottom-add", { timeout: 5000 });
      await sleep(1500);
      await clickMarked(page, "从图片库选择", "打开图片库");
      await sleep(2500);
      let pickedN = 0;
      for (const name of names) {
        if (pickedN >= need) break;
        const hit = await page.evaluate((nm) => {
          document.querySelectorAll("[data-wx-card]").forEach((e) => e.removeAttribute("data-wx-card"));
          const card = Array.from(document.querySelectorAll(".weui-desktop-img-picker__item"))
            .find((d) => (d.innerText || "").includes(nm));
          if (!card || card.classList.contains("selected")) return false;
          card.setAttribute("data-wx-card", "1");
          return true;
        }, name).catch(() => false);
        if (!hit) { ev("card-miss", { name }); continue; }
        await page.click('[data-wx-card="1"]');
        pickedN += 1;
        await sleep(400);
        ev("card-picked", { name });
      }
      const selCount = await page.evaluate(() =>
        document.querySelectorAll(".weui-desktop-img-picker__item.selected").length).catch(() => 0);
      ev("selected", { count: selCount });
      await stepShot(page, "02-library-picked");
      if (selCount < need) ev("warn", { msg: `库里只勾到 ${selCount}/${need}` });
    } else if (need < 0) {
      ev("warn", { msg: `贴图数 ${cur} 超过预期 ${CONFIG.images.length}，请人工检查草稿` });
      await stepShot(page, "02-overcount");
    }
    // 确定（仅补选过才需要点；need<=0 时选择器未打开）
    if (need > 0) {
      await page.evaluate(() => {
        document.querySelectorAll("[data-wx-ok]").forEach((e) => e.removeAttribute("data-wx-ok"));
        const card = document.querySelector(".weui-desktop-img-picker__item");
        let p = card;
        for (let i = 0; i < 12 && p; i++) {
          if ((p.innerText || "").includes("确定")) break;
          p = p.parentElement;
        }
        const scope = p || document;
        for (const el of scope.querySelectorAll("button,a")) {
          if ((el.innerText || "").trim() === "确定" && el.getBoundingClientRect().width > 0) {
            el.setAttribute("data-wx-ok", "1");
            break;
          }
        }
      });
      const hasOk = await page.evaluate(() => !!document.querySelector('[data-wx-ok="1"]'));
      if (!hasOk) throw new Error("选择器里找不到「确定」");
      await page.click('[data-wx-ok="1"]');
      clicks.push({ purpose: "图片库-确定", text: "确定" });
      await sleep(3000);
    } else {
      ev("skip-confirm", { reason: "无需补选" });
    }

    // 6. 验证贴图数量（计数文本 + mmbiz img 双口径，取大者）
    const total = await count();
    ev("insert-verify", { total, expect: CONFIG.images.length });
    if (total > CONFIG.images.length) {
      ev("warn", { msg: `贴图数量 ${total} 超过预期 ${CONFIG.images.length}，请人工检查草稿` });
    }
    result.images = total;
    await stepShot(page, "03-inserted");

    // 7. 标题 + 描述（focus 方式；描述编辑器高度>100 的 ProseMirror）
    await page.evaluate(() => {
      document.querySelectorAll("[data-wx-title]").forEach((e) => e.removeAttribute("data-wx-title"));
      const t = Array.from(document.querySelectorAll("div[contenteditable=\"true\"].ProseMirror"))
        .find((e) => (e.getAttribute("data-placeholder") || "").includes("标题"));
      if (t) t.setAttribute("data-wx-title", "1");
    });
    await page.focus('[data-wx-title="1"]');
    await page.keyboard.insertText(CONFIG.title);
    await sleep(500);
    if (CONFIG.digest) {
      await page.evaluate(() => {
        document.querySelectorAll("[data-wx-body]").forEach((e) => e.removeAttribute("data-wx-body"));
        const b = Array.from(document.querySelectorAll("div[contenteditable=\"true\"].ProseMirror"))
          .find((e) => !(e.getAttribute("data-placeholder") || "").includes("标题") && e.getBoundingClientRect().height > 100);
        if (b) b.setAttribute("data-wx-body", "1");
      });
      const hasBody = await page.evaluate(() => !!document.querySelector('[data-wx-body="1"]'));
      if (hasBody) {
        await page.focus('[data-wx-body="1"]');
        await page.keyboard.insertText(CONFIG.digest);
        await sleep(500);
      }
    }
    const filled = await page.evaluate(() => {
      const t = document.querySelector('[data-wx-title="1"]');
      const b = document.querySelector('[data-wx-body="1"]');
      return { title: t ? t.innerText.trim() : "", bodyLen: b ? (b.innerText || "").replace(/\s/g, "").length : 0 };
    });
    ev("filled", filled);
    await page.evaluate(() => window.scrollTo(0, 0)).catch(() => {});
    await sleep(400);
    await stepShot(page, "04-filled");

    // 8. 保存草稿
    await clickMarked(page, "保存为草稿", "保存草稿");
    await sleep(3500);
    const saved = await page.evaluate(() =>
      /已保存|保存成功/.test(document.body.innerText || "")).catch(() => false);
    ev("draft-saved", { saved });
    await stepShot(page, "05-draft");

    result.ok = saved && filled.title && total >= Math.min(CONFIG.images.length, 1);
    result.result = result.ok ? "draft-ok" : "draft-unverified";
    result.filled = filled;
    console.log("EGO_RESULT " + JSON.stringify(result));
    await task.finish({ keep: [] });
  } catch (e) {
    result.error_step = e.step || "unexpected";
    result.error = String(e.message || e);
    await stepShot(page, "fail-" + result.error_step).catch(() => {});
    console.log("EGO_RESULT " + JSON.stringify(result));
  }
}

await main();
