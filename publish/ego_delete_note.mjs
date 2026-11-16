// ego_delete_note.mjs — 删除笔记管理页里指定的笔记（用于撤回定时稿重发）
// 安全设计：标题严格匹配 + 卡片状态须含 CONFIG.expect_state（如「定时发布」），
// 删前/删后各截图，删后验证卡片已消失；确认弹窗按钮用 AX 树穿透查找。

const PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish";
const ev = (name, data) => console.log(JSON.stringify({ ev: name, ...data }));
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function stepShot(page, name) {
  const path = `${CONFIG.logs_dir}/del-${name}-${CONFIG.stamp}.png`;
  try { await page.screenshot({ path }); ev("shot", { name, path }); return path; } catch (e) { ev("shot-failed", { name, error: String(e) }); }
}

// 定位目标卡片的删除按钮（只标记不点击，返回实时坐标与卡片状态）
const markDelBtn = (title) => {
  document.querySelectorAll("[data-xhs-del]").forEach((e) => e.removeAttribute("data-xhs-del"));
  const norm = (s) => (s || "").replace(/\s+/g, "");
  const t = norm(title);
  for (const el of Array.from(document.querySelectorAll(".note-card__title"))) {
    if (!norm(el.innerText).includes(t)) continue;
    let card = el;
    for (let i = 0; i < 8 && card && card !== document.body; i++) {
      card = card.parentElement;
      if (card.querySelector(".note-card__time")) break;
    }
    const time = (card.querySelector(".note-card__time")?.innerText || "").trim();
    const cardText = (card.innerText || "").replace(/\s+/g, "");
    const del = card.querySelector(".note-card__action-btn--del");
    if (!del) return { found: true, has_del: false, time, cardText: cardText.slice(0, 120) };
    del.setAttribute("data-xhs-del", "1");
    const r = del.getBoundingClientRect();
    return { found: true, has_del: true, time, cardText: cardText.slice(0, 120),
             x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
  }
  return { found: false };
};

const findNote = (title) => {
  const norm = (s) => (s || "").replace(/\s+/g, "");
  const t = norm(title);
  for (const el of Array.from(document.querySelectorAll(".note-card__title"))) {
    if (norm(el.innerText).includes(t)) return { found: true, matched: el.innerText.trim() };
  }
  return { found: false, total: document.querySelectorAll(".note-card__title").length };
};

async function main() {
  const task = await taskSpace(CONFIG.space_name);
  const page = task.page("p1");
  ev("space", { spaceId: task.spaceId });
  const result = { ok: false, mode: "delete-note" };
  try {
    await page.goto(PUBLISH_URL, { timeout: 40000 });
    try { await page.waitForLoadState("load", { timeout: 15000 }); } catch {}
    await sleep(2500);
    if ((await page.url()).includes("/login")) {
      result.error = "ego lite 无小红书登录态";
      console.log("EGO_RESULT " + JSON.stringify(result)); return;
    }
    await page.click("text=笔记管理", { timeout: 8000 });
    try { await page.waitForLoadState("load", { timeout: 15000 }); } catch {}
    await sleep(3000);

    const target = await page.evaluate(markDelBtn, CONFIG.title).catch((e) => ({ found: false, error: String(e) }));
    ev("target", target);
    if (!target.found || !target.has_del) {
      result.error = target.found ? "找到卡片但没有删除按钮" : "没找到目标卡片";
      await stepShot(page, "no-target");
      result.target = target;
      console.log("EGO_RESULT " + JSON.stringify(result)); return;
    }
    if (CONFIG.expect_state && !target.cardText.includes(CONFIG.expect_state.replace(/\s+/g, ""))) {
      result.error = `卡片状态不含「${CONFIG.expect_state}」（卡片全文：${target.cardText}），防误删已停止`;
      await stepShot(page, "state-mismatch");
      result.target = target;
      console.log("EGO_RESULT " + JSON.stringify(result)); return;
    }
    if (CONFIG.expect_time && !target.time.includes(CONFIG.expect_time)) {
      result.error = `卡片时间不含「${CONFIG.expect_time}」（实际：${target.time}），防误删已停止`;
      await stepShot(page, "state-mismatch");
      result.target = target;
      console.log("EGO_RESULT " + JSON.stringify(result)); return;
    }
    await stepShot(page, "before");

    // 点删除按钮（标记选择器，Playwright 自动滚动到位）
    await page.click("[data-xhs-del='1']", { timeout: 8000 });
    ev("click-del", {});
    await sleep(1800);
    await stepShot(page, "dialog");

    // 确认弹窗：AX 树找按钮（穿透 shadow DOM），文本匹配删除类确认词
    let confirmed = null;
    const ax = await page.cdp("Accessibility.getFullAXTree").catch(() => null);
    const nodes = (ax && ax.nodes) || [];
    const cands = [];
    for (const n of nodes) {
      if (!n.role || !/button/i.test(String(n.role.value || ""))) continue;
      const text = String((n.name && n.name.value) || "").trim();
      if (!/^(删除|确认删除|确认|确定|删除笔记)$/.test(text) || !n.backendDOMNodeId) continue;
      let box; try { box = await page.cdp("DOM.getBoxModel", { backendNodeId: n.backendDOMNodeId }); } catch { continue; }
      const q = box.model.border;
      const xs = [q[0], q[2], q[4], q[6]], ys = [q[1], q[3], q[5], q[7]];
      cands.push({ text, x: xs.reduce((a, b) => a + b, 0) / 4, y: ys.reduce((a, b) => a + b, 0) / 4 });
    }
    ev("dialog-buttons", { cands });
    if (cands.length) {
      // 优先「删除/确认删除/删除笔记」，退而「确认/确定」
      const pick = cands.find((c) => /删除/.test(c.text)) || cands[0];
      await page.mouse.move(pick.x, pick.y); await sleep(200);
      await page.mouse.click(pick.x, pick.y);
      confirmed = pick.text;
      ev("click-confirm", { text: confirmed });
    } else {
      // AX 树没找到，退回文本选择器
      for (const t of ["删除", "确认删除", "删除笔记", "确认", "确定"]) {
        try { await page.click(`text="${t}"`, { timeout: 1500 }); confirmed = t; break; } catch {}
      }
      ev("click-confirm-fallback", { text: confirmed });
    }
    await sleep(2500);
    await stepShot(page, "after");

    const after = await page.evaluate(findNote, CONFIG.title).catch(() => ({ found: null }));
    ev("verify", after);
    result.ok = confirmed !== null && !after.found;
    result.confirmed = confirmed;
    result.after = after;
    if (!result.ok && confirmed === null) result.error = "没找到确认弹窗按钮（看 del-dialog 截图）";
    if (confirmed !== null && after.found) result.error = "点了确认但卡片还在（看 del-after 截图）";
  } catch (e) {
    result.error_step = "unexpected";
    result.error = String(e).slice(0, 400);
    await stepShot(page, "fail");
  }
  console.log("EGO_RESULT " + JSON.stringify(result));
}
main().catch((e) => console.log("EGO_RESULT " + JSON.stringify({ ok: false, error: String(e) })));
