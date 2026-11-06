// ego_publish.mjs — 小红书发布驱动（跑在 ego lite 里，由 ego_publish.py 注入 CONFIG 调起）
//
// 设计与 publish.py（Playwright 版）一一对应：
// - 安全闸门：非提交模式绝不点「发布/立即发布/确认发布…」
// - 每步截图到 logs/；结论通过 EGO_RESULT JSON 行返回给 Python
// - 底部按钮在 closed shadow DOM 里，用 CDP pierce + 真实鼠标坐标点击
// - 注意：page.evaluate 必须传【函数】，传字符串会静默返回 {}（实测）
//
// 由 Python 包装器在文件头部前置 `const CONFIG = {...};` 后经 stdin 传给 ego-browser nodejs。

const PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish";
const SUBMIT_TEXT_RE = /^(发布|立即发布|确认发布|确定发布|提交|立即提交|定时发布)$/;
const MAX_TITLE = 20;

const ev = (name, data) => console.log(JSON.stringify({ ev: name, ...data }));

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// ---------- 页面探测函数（函数形式注入 evaluate；实测结论见 SELECTORS.md） ----------

const pickLeaf = (text) => {
  document.querySelectorAll('[data-xhs-pick]').forEach((e) => e.removeAttribute('data-xhs-pick'));
  const cands = [];
  document.querySelectorAll('div,span,a,button,li').forEach((el) => {
    const t = (el.innerText || '').trim();
    if (t !== text) return;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    if (r.left < -1 || r.top < -1 || r.right > innerWidth + 2 || r.bottom > innerHeight + 2) return;
    let opacity = 1, visible = true, pe = 'auto', node = el;
    for (let i = 0; i < 7 && node && node !== document.documentElement; i++) {
      const cs = getComputedStyle(node);
      opacity *= parseFloat(cs.opacity || '1');
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse') visible = false;
      if (cs.pointerEvents === 'none') pe = 'none';
      node = node.parentElement;
    }
    if (!visible || opacity < 0.05 || pe === 'none') return;
    cands.push({ el, area: r.width * r.height, bound: el.getAttribute('data-hp-bound') === '1' });
  });
  if (!cands.length) return null;
  cands.sort((a, b) => (b.bound ? 1 : 0) - (a.bound ? 1 : 0) || b.area - a.area);
  cands[0].el.setAttribute('data-xhs-pick', '1');
  const r = cands[0].el.getBoundingClientRect();
  return { count: cands.length, bound: cands[0].bound,
           x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
};

const imageMode = () => {
  const inputs = Array.from(document.querySelectorAll('input[type=file]'));
  const isImg = (a) => { a = (a || '').toLowerCase();
    return !a || a.includes('png') || a.includes('jpg') || a.includes('jpeg') || a.includes('webp') || a.includes('image'); };
  const isVid = (a) => { a = (a || '').toLowerCase();
    return a.includes('mp4') || a.includes('mov') || a.includes('flv') || a.includes('mkv'); };
  const img = inputs.find((i) => isImg(i.accept) && !isVid(i.accept));
  const txt = document.body.innerText || '';
  return { has_image_input: !!img, drag_text: /拖拽图片|上传图片|添加图片/.test(txt),
           inputs: inputs.map((i) => ({ accept: i.accept || '', multiple: i.multiple })) };
};

const findImageInput = () => {
  document.querySelectorAll('[data-xhs-input]').forEach((e) => e.removeAttribute('data-xhs-input'));
  const inputs = Array.from(document.querySelectorAll('input[type=file]'));
  const isImg = (a) => { a = (a || '').toLowerCase();
    return !!a && (a.includes('png') || a.includes('jpg') || a.includes('jpeg')
                   || a.includes('webp') || a.includes('image')); };
  const cands = inputs.filter((i) => isImg(i.accept));
  if (!cands.length) return { ready: false, inputs: inputs.map((i) => i.accept || '') };
  const pickSel = cands[0];
  pickSel.setAttribute('data-xhs-input', '1');
  return { ready: true, accept: pickSel.accept || '', multiple: !!pickSel.multiple };
};

const uploadState = () => {
  const txt = document.body.innerText || '';
  const uploading = /上传中|正在上传|上传进度/.test(txt);
  const previews = document.querySelectorAll('img.preview').length;
  let status = '';
  const st = document.querySelector('.img-list .status');
  if (st) status = (st.innerText || '').trim();
  return { uploading, previews, status };
};

const findEditor = () => {
  document.querySelectorAll('[data-xhs-editor]').forEach((e) => e.removeAttribute('data-xhs-editor'));
  const sels = ["[contenteditable='true']", "[contenteditable]", "div[class*='content']"];
  for (const sel of sels) {
    const list = Array.from(document.querySelectorAll(sel)).slice(0, 6);
    for (const el of list) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 80) {
        el.setAttribute('data-xhs-editor', '1');
        return { ready: true, h: Math.round(r.height) };
      }
    }
  }
  return { ready: false };
};

const panelTopics = () => Array.from(document.querySelectorAll('#creator-editor-topic-container .item .name'))
  .map((e) => (e.innerText || '').trim());

const topicCount = () => document.querySelectorAll('div.tiptap a.tiptap-topic').length;

const cursorAtEnd = () => {
  const ed = document.querySelector('div.tiptap[contenteditable="true"]');
  const sel = document.getSelection();
  if (!ed || !sel || !sel.anchorNode) return false;
  if (!ed.contains(sel.anchorNode)) return false;
  const range = document.createRange();
  range.selectNodeContents(ed);
  range.setStart(sel.anchorNode, sel.anchorOffset);
  return range.toString().trim().length === 0;
};

const findNoteCard = (title) => {
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
      return { found: true, matched: el.innerText.trim(), time };
    }
  }
  return { found: false, total: titles.length };
};

const scrollTop = () => window.scrollTo(0, 0);
const inputSelValue = (sel) => { const el = document.querySelector(sel); return el ? el.value : ""; };
const findTitleInput = () => {
  document.querySelectorAll('[data-xhs-title]').forEach((e) => e.removeAttribute('data-xhs-title'));
  const sels = ["input[placeholder*='标题']", "input[placeholder*='填写标题']",
                "input[class*='title']", "input[type='text']"];
  for (const sel of sels) {
    for (const el of Array.from(document.querySelectorAll(sel))) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) {
        el.setAttribute('data-xhs-title', '1');
        return { ready: true, placeholder: el.placeholder || '' };
      }
    }
  }
  return { ready: false };
};
const editorText = () => (document.querySelector('[data-xhs-editor="1"]') || { innerText: "" }).innerText || "";

const clickScheduleSwitch = () => {
  const card = document.querySelector('.post-time-wrapper .custom-switch-card');
  const cb = card && card.querySelector('input[type=checkbox]');
  if (!card || !cb) return { ok: false };
  if (cb.checked) return { ok: true, already: true };
  const sw = card.querySelector('.d-switch');
  if (sw) sw.click();
  return { ok: true, clicked: true };
};

// ---------- 底部提交按钮（closed shadow DOM，经 Accessibility 可访问树穿透） ----------
// 实测（2026-11-06，ego lite）：ego 的 CDP 反检测层会把 shadow DOM 从 DOM.getDocument 里
// 整个滤掉（pierce 无效）；但 Accessibility.getFullAXTree 天然穿透 closed shadow root，
// 拿 backendDOMNodeId → DOM.getBoxModel({backendNodeId}) → 真实鼠标坐标点击。

async function getPublishButtons(page) {
  const ax = await page.cdp("Accessibility.getFullAXTree");
  const nodes = (ax && ax.nodes) || [];
  const btns = [];
  for (const n of nodes) {
    if (!n.role || !/button/i.test(String(n.role.value || ""))) continue;
    const text = String((n.name && n.name.value) || "").trim();
    if (!text || !n.backendDOMNodeId) continue;
    let box;
    try { box = await page.cdp("DOM.getBoxModel", { backendNodeId: n.backendDOMNodeId }); }
    catch { continue; }
    const q = box.model.border;
    const xs = [q[0], q[2], q[4], q[6]], ys = [q[1], q[3], q[5], q[7]];
    const dis = (n.properties || []).find((p) => p.name === "disabled");
    btns.push({
      text, disabled: !!(dis && dis.value && dis.value.value),
      x: xs.reduce((a, b) => a + b, 0) / 4, y: ys.reduce((a, b) => a + b, 0) / 4,
      w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys),
    });
  }
  // 只保留底部提交区的候选（暂存离开/发布/定时发布），AX 树里可能有大量无关按钮
  return btns.filter((b) => /^(暂存离开|发布|立即发布|定时发布|确认发布|确定发布)$/.test(b.text));
}

// ---------- 主流程 ----------

const clicks = [];

async function safeShadowClick(page, target, purpose, allowSubmit) {
  if (!allowSubmit && SUBMIT_TEXT_RE.test(target.text)) {
    clicks.push({ purpose, text: target.text, allow_submit: false, blocked: true });
    throw new Error(`安全闸门拦截：${CONFIG.mode} 模式试图点击疑似发布按钮「${target.text}」（${purpose}），已停止`);
  }
  await page.evaluate(scrollTop);
  await sleep(300);
  await page.mouse.move(target.x, target.y);
  await sleep(200);
  await page.mouse.click(target.x, target.y, { label: purpose });
  clicks.push({ purpose, text: target.text, allow_submit: !!allowSubmit });
  ev("click", { purpose, text: target.text, allow_submit: !!allowSubmit });
}

async function stepShot(page, name) {
  const path = `${CONFIG.logs_dir}/step-${name}-${CONFIG.stamp}.png`;
  try { await page.screenshot({ path }); ev("shot", { name, path }); return path; }
  catch (e) { ev("shot-failed", { name, error: String(e) }); return null; }
}

async function insertBody(page, body) {
  const paras = body.replace(/\r\n/g, "\n").split(/\n\s*\n/).filter((p) => p.trim());
  for (let pi = 0; pi < paras.length; pi++) {
    const lines = paras[pi].split("\n");
    for (let li = 0; li < lines.length; li++) {
      if (lines[li]) await page.keyboard.insertText(lines[li]);
      if (li < lines.length - 1) { await page.keyboard.press("Shift+Enter"); await sleep(80); }
    }
    if (pi < paras.length - 1) { await page.keyboard.press("Enter"); await sleep(120); }
  }
}

async function main() {
  const task = await taskSpace(CONFIG.space_name);
  const page = task.page("p1");
  ev("space", { spaceId: task.spaceId, mode: CONFIG.mode });
  const result = { mode: CONFIG.mode, ok: false, clicks };

  try {
    // 0. 屏蔽定位权限弹窗：接管 navigator.geolocation，让页面拿到「已拒绝」而不是触发浏览器提示
    try {
      await page.cdp("Page.addScriptToEvaluateOnNewDocument", {
        source: `if (navigator.geolocation) {
          const denied = (cb) => cb && cb({ code: 1, PERMISSION_DENIED: 1, message: "User denied Geolocation" });
          navigator.geolocation.getCurrentPosition = function (s, e) { denied(e); };
          navigator.geolocation.watchPosition = function (s, e) { denied(e); return 0; };
        }`,
      });
      ev("geo-blocked", {});
    } catch (e) { ev("geo-block-failed", { error: String(e).slice(0, 120) }); }

    // 1. 打开发布页 + 登录检查
    await page.goto(PUBLISH_URL, { timeout: 40000 });
    try { await page.waitForLoadState("load", { timeout: 15000 }); } catch {}
    await sleep(2000);
    const url = await page.url();
    ev("navigate", { url });
    if (url.includes("/login")) {
      result.error_step = "login";
      result.error = "ego lite 里没有小红书登录态。请先跑：python3 publish/ego_login.py --qr";
      await stepShot(page, "login-required");
      console.log("EGO_RESULT " + JSON.stringify(result));
      return;
    }

    // 2. 切「上传图文」tab（重载重试一次）
    let imageReady = false;
    for (let attempt = 1; attempt <= 2 && !imageReady; attempt++) {
      let st = await page.evaluate(imageMode).catch(() => ({}));
      if (!st.has_image_input && !st.drag_text) {
        const pick = await page.evaluate(pickLeaf, "上传图文").catch(() => null);
        if (pick) { await page.click('[data-xhs-pick="1"]'); await sleep(1200); }
        else if (attempt === 2) {
          await page.reload(); await sleep(2500);
          const p2 = await page.evaluate(pickLeaf, "上传图文").catch(() => null);
          if (p2) { await page.click('[data-xhs-pick="1"]'); await sleep(1200); }
        }
        for (let i = 0; i < 16; i++) {
          st = await page.evaluate(imageMode).catch(() => ({}));
          if (st.has_image_input || st.drag_text) break;
          await sleep(400);
        }
      }
      imageReady = !!(st.has_image_input || st.drag_text);
    }
    if (!imageReady) throw Object.assign(new Error("切不到「上传图文」tab"), { step: "upload-tab" });
    await stepShot(page, "03-image-tab");

    // 3. 上传图片
    const inp = await page.evaluate(findImageInput).catch(() => ({ ready: false }));
    if (!inp.ready) throw Object.assign(new Error("找不到图片上传 input"), { step: "upload-input" });
    if (inp.multiple) {
      await page.setInputFiles('[data-xhs-input="1"]', CONFIG.images);
    } else {
      for (const img of CONFIG.images) {
        await page.setInputFiles('[data-xhs-input="1"]', [img]);
        await sleep(1500);
      }
    }
    ev("upload-set", { count: CONFIG.images.length, multiple: !!inp.multiple });
    let lastPreviews = -1;
    for (let i = 0; i < 180; i++) {
      const st = await page.evaluate(uploadState).catch(() => ({ uploading: false, previews: 0 }));
      if (st.previews !== lastPreviews) { ev("upload-progress", { previews: st.previews, status: st.status }); lastPreviews = st.previews; }
      if (!st.uploading && st.previews >= CONFIG.images.length) break;
      await sleep(1000);
    }
    await stepShot(page, "04-uploaded");
    for (const t of ["我知道了", "知道了", "关闭"]) {
      try { await page.click(`text="${t}"`, { timeout: 1500 }); await sleep(600); break; } catch {}
    }

    // 4. 标题（页面有多个 input，用 JS 标记法锁定唯一目标，避免选择器歧义）
    let tInfo = { ready: false };
    for (let i = 0; i < 25 && !tInfo.ready; i++) {
      tInfo = await page.evaluate(findTitleInput).catch(() => ({ ready: false }));
      if (!tInfo.ready) await sleep(400);
    }
    if (!tInfo.ready) throw Object.assign(new Error("找不到标题输入框"), { step: "title" });
    ev("title-input", { placeholder: tInfo.placeholder });
    await page.fill('[data-xhs-title="1"]', CONFIG.title.slice(0, MAX_TITLE));
    await sleep(600);
    const titleVal = await page.evaluate(inputSelValue, '[data-xhs-title="1"]').catch(() => "");
    if (!titleVal || !titleVal.trim()) {
      throw Object.assign(new Error("标题写入后是空的"), { step: "title-verify" });
    }
    ev("title", { value: titleVal, len: titleVal.length });

    // 5. 正文
    const ed = await page.evaluate(findEditor).catch(() => ({ ready: false }));
    if (!ed.ready) throw Object.assign(new Error("找不到正文编辑器"), { step: "body" });
    await page.click('[data-xhs-editor="1"]');
    await sleep(400);
    await insertBody(page, CONFIG.body);
    await sleep(800);
    const normLen = (s) => (s || "").replace(/\s/g, "").length;
    let got = normLen(await page.evaluate(editorText).catch(() => ""));
    const expect = normLen(CONFIG.body);
    if (expect > 0 && got < Math.max(2, Math.floor(expect * 0.8))) {
      await page.click('[data-xhs-editor="1"]');
      await page.keyboard.press("Meta+A"); await page.keyboard.press("Backspace");
      await sleep(400);
      await insertBody(page, CONFIG.body); await sleep(800);
      got = normLen(await page.evaluate(editorText).catch(() => ""));
      if (got < Math.max(2, Math.floor(expect * 0.8))) {
        throw Object.assign(new Error(`正文写入不完整：目标 ${expect} 字，实际 ${got} 字`), { step: "body-verify" });
      }
    }
    ev("body", { expect, got });
    await stepShot(page, "05-body");

    // 6. 话题
    const picked = [], missed = [];
    if (CONFIG.tags.length) {
      await page.click('[data-xhs-editor="1"]');
      await page.keyboard.press("Meta+ArrowDown");
      await sleep(500);
      let atEnd = await page.evaluate(cursorAtEnd).catch(() => false);
      if (!atEnd) {
        await page.keyboard.press("Control+End"); await sleep(400);
        atEnd = await page.evaluate(cursorAtEnd).catch(() => false);
      }
      if (!atEnd) throw Object.assign(new Error("光标没能移到正文末尾，拒绝插入话题"), { step: "tags-cursor" });

      for (const tag of CONFIG.tags) {
        try {
          await page.keyboard.press("Enter"); await sleep(500);
          await page.keyboard.insertText("#" + tag);
          let names = [];
          for (let i = 0; i < 14; i++) {
            names = await page.evaluate(panelTopics).catch(() => []);
            if (names.length) break;
            await sleep(300);
          }
          if (!names.length) {
            missed.push(tag); ev("tag-miss", { tag, reason: "no-panel" });
            await page.keyboard.insertText(" "); continue;
          }
          const exact = names.findIndex((nm) => nm === "#" + tag);
          if (exact < 0) {
            missed.push(tag); ev("tag-miss", { tag, reason: "no-exact", top3: names.slice(0, 3) });
            await page.keyboard.press("Escape"); await page.keyboard.insertText(" "); continue;
          }
          await page.click(`#creator-editor-topic-container .item >> nth=${exact}`);
          await sleep(1000);
          const cnt = await page.evaluate(topicCount).catch(() => 0);
          if (cnt >= picked.length + 1) { picked.push(tag); ev("tag-picked", { tag }); }
          else { missed.push(tag); ev("tag-miss", { tag, reason: "no-node" }); }
        } catch (e) {
          missed.push(tag); ev("tag-miss", { tag, reason: String(e) });
        }
      }
      await stepShot(page, "06-tags");
    }
    result.tags = { picked, missed };

    // 7. 定时发布
    let scheduleInfo = null;
    if (CONFIG.schedule) {
      await page.waitForSelector(".post-time-wrapper", { state: "visible", timeout: 10000 });
      const switched = await page.evaluate(clickScheduleSwitch).catch(() => ({ ok: false }));
      if (!switched.ok) throw Object.assign(new Error("找不到「定时发布」开关"), { step: "schedule" });
      await sleep(1500);
      await page.waitForSelector(".date-picker-container input.d-text", { state: "visible", timeout: 10000 });
      const readback0 = await page.evaluate(inputSelValue, ".date-picker-container input.d-text").catch(() => "");
      await page.click(".date-picker-container input.d-text");
      await sleep(600);
      await page.fill(".date-picker-container input.d-text", CONFIG.schedule);
      await sleep(600);
      await page.keyboard.press("Enter"); await sleep(1000);
      await page.keyboard.press("Escape"); await sleep(500);
      const readback = await page.evaluate(inputSelValue, ".date-picker-container input.d-text").catch(() => "");
      scheduleInfo = { target: CONFIG.schedule, readback, default: readback0 };
      ev("schedule", scheduleInfo);
      if (readback.trim() !== CONFIG.schedule) {
        throw Object.assign(
          new Error(`平台没有接受定时时间：期望 ${CONFIG.schedule}，回读 ${readback}`), { step: "schedule-verify" });
      }
      await stepShot(page, "07-schedule");
    }
    result.schedule = scheduleInfo;

    // 8. 底部按钮（closed shadow DOM）
    await page.evaluate(scrollTop);
    await sleep(400);
    const btns = await getPublishButtons(page);
    if (!btns.length) throw Object.assign(new Error("读不到 <xhs-publish-btn> 内部按钮"), { step: "buttons" });
    const saveBtn = btns.find((b) => b.text === "暂存离开");
    const submitText = CONFIG.schedule ? "定时发布" : "发布";
    const submitBtn = btns.find((b) => b.text === submitText);
    ev("buttons", { found: btns.map((b) => b.text), submit: submitBtn ? { x: submitBtn.x, y: submitBtn.y } : null });
    result.buttons = { found: btns.map((b) => b.text), has_save: !!saveBtn, has_submit: !!submitBtn };

    if (CONFIG.mode === "dry-run") {
      await stepShot(page, "dry-run-final");
      result.ok = true;
      result.result = "dry-run-ok";
      result.audit = { submit_clicks: 0 };
    } else if (CONFIG.mode === "draft") {
      if (!saveBtn) throw Object.assign(new Error("找不到「暂存离开」按钮"), { step: "draft" });
      await safeShadowClick(page, saveBtn, "暂存离开（存草稿）", false);
      await sleep(2000);
      for (const t of ["暂存并离开", "确定", "确认", "保存草稿", "暂存"]) {
        try { await page.click(`text="${t}"`, { timeout: 1500 }); await sleep(1500); break; } catch {}
      }
      await stepShot(page, "07-draft-done");
      result.ok = true; result.result = "draft-ok";
    } else if (CONFIG.mode === "schedule") {
      if (!submitBtn) throw Object.assign(new Error(`找不到「${submitText}」按钮`), { step: "publish-scheduled" });
      await safeShadowClick(page, submitBtn, "定时发布", true);
      await sleep(2500);
      for (const t of ["确认定时发布", "确认发布", "确定发布", "确定", "确认"]) {
        try { await page.click(`text="${t}"`, { timeout: 1500 }); await sleep(2000); break; } catch {}
      }
      await stepShot(page, "08-published");

      // 9. 笔记管理回读验证
      let verify = { found: false };
      try {
        await page.click("text=笔记管理", { timeout: 8000 });
        try { await page.waitForLoadState("load", { timeout: 15000 }); } catch {}
        await sleep(3000);
        for (const tab of ["定时发布", "审核中", "全部"]) {
          try {
            await page.click(`text=${tab}`, { timeout: 3000 });
            await sleep(2000);
            const res = await page.evaluate(findNoteCard, CONFIG.title).catch(() => ({ found: false }));
            ev("verify-tab", { tab, res });
            if (res.found) {
              verify = { found: true, tab, matched: res.matched, time: res.time };
              const hhmm = CONFIG.schedule.split(" ").pop();
              verify.time_matches = hhmm ? res.time.includes(hhmm) : null;
              break;
            }
          } catch {}
        }
        verify.screenshot = await stepShot(page, "scheduled-verify");
      } catch (e) { verify.error = String(e); }
      result.verify = verify;
      result.ok = verify.found;
      result.result = verify.found ? "scheduled" : "scheduled-unverified";
    } else if (CONFIG.mode === "publish") {
      if (!submitBtn) throw Object.assign(new Error("找不到「发布」按钮"), { step: "publish" });
      await safeShadowClick(page, submitBtn, "立即发布", true);
      await sleep(2500);
      for (const t of ["确认发布", "确定发布", "确定", "确认"]) {
        try { await page.click(`text="${t}"`, { timeout: 1500 }); await sleep(2000); break; } catch {}
      }
      await stepShot(page, "08-published");
      result.ok = true; result.result = "published";
    }

    console.log("EGO_RESULT " + JSON.stringify(result));
    await task.finish({ keep: [] });
  } catch (e) {
    result.error_step = e.step || "unexpected";
    result.error = String(e.message || e);
    await stepShot(page, "fail-" + (e.step || "unexpected")).catch(() => {});
    console.log("EGO_RESULT " + JSON.stringify(result));
  }
}

await main();
