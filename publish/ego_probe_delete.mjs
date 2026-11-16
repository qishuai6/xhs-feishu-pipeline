// ego_probe_delete.mjs — 只读探查：笔记管理「定时发布」tab 里目标卡片的操作按钮结构
// 用法：python3 包装注入 CONFIG（title/logs_dir/stamp/space_name）后经 stdin 传给 ego-browser nodejs
// 只截图和 dump DOM，不做任何点击。

const ev = (name, data) => console.log(JSON.stringify({ ev: name, ...data }));
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function main() {
  const task = await taskSpace(CONFIG.space_name);
  const page = task.page("p1");
  ev("space", { spaceId: task.spaceId });
  const result = { ok: false, mode: "probe-delete" };
  try {
    await page.goto("https://creator.xiaohongshu.com/publish/publish", { timeout: 40000 });
    try { await page.waitForLoadState("load", { timeout: 15000 }); } catch {}
    await sleep(2500);
    await page.click("text=笔记管理", { timeout: 8000 });
    try { await page.waitForLoadState("load", { timeout: 15000 }); } catch {}
    await sleep(3000);
    const url = await page.url();
    ev("navigate", { url });
    if (url.includes("/login")) {
      result.error_step = "login";
      result.error = "ego lite 无小红书登录态";
      console.log("EGO_RESULT " + JSON.stringify(result));
      return;
    }
    // 切「定时发布」tab（找不到就退回「全部」）
    let tab = "";
    for (const t of ["定时发布", "全部"]) {
      try {
        await page.click(`text=${t}`, { timeout: 4000 });
        tab = t; break;
      } catch {}
    }
    await sleep(2500);
    ev("tab", { tab });
    let shot = `${CONFIG.logs_dir}/probe-manage-${CONFIG.stamp}.png`;
    try { await page.screenshot({ path: shot }); ev("shot", { path: shot }); } catch {}

    // dump 目标卡片结构（只读）
    const dump = await page.evaluate((title) => {
      const norm = (s) => (s || "").replace(/\s+/g, "");
      const t = norm(title);
      const titles = Array.from(document.querySelectorAll(".note-card__title"));
      for (const el of titles) {
        if (!norm(el.innerText).includes(t)) continue;
        let card = el;
        for (let i = 0; i < 8 && card && card !== document.body; i++) {
          card = card.parentElement;
          if (card.querySelector(".note-card__time")) break;
        }
        const cr = card.getBoundingClientRect();
        const cls = (e) => {
          const c = (e && e.className);
          if (c && c.baseVal !== undefined) return String(c.baseVal);
          return String(c || "");
        };
        const acts = Array.from(card.querySelectorAll("button, a, [class*='action'], [class*='btn'], [class*='operate'], [class*='more'], [class*='delete'], [class*='icon']"))
          .map((e) => {
            const r = e.getBoundingClientRect();
            return { tag: e.tagName, cls: cls(e).slice(0, 70), txt: (e.innerText || "").trim().slice(0, 20),
                     x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
          })
          .filter((a) => a.w > 0 && a.h > 0);
        return { found: true, matched: el.innerText.trim(),
                 card_cls: cls(card).slice(0, 90),
                 card_text: (card.innerText || "").replace(/\n+/g, " | ").slice(0, 400),
                 card_rect: { x: Math.round(cr.x), y: Math.round(cr.y), w: Math.round(cr.width), h: Math.round(cr.height) },
                 acts };
      }
      return { found: false, total: titles.length,
               first_titles: titles.slice(0, 8).map((e) => e.innerText.trim().slice(0, 24)) };
    }, CONFIG.title).catch((e) => ({ found: false, error: String(e) }));
    ev("dump", dump);
    result.ok = !!dump.found;
    result.dump = dump;
  } catch (e) {
    result.error_step = "unexpected";
    result.error = String(e).slice(0, 400);
    try { await page.screenshot({ path: `${CONFIG.logs_dir}/probe-fail-${CONFIG.stamp}.png` }); } catch {}
  }
  console.log("EGO_RESULT " + JSON.stringify(result));
}
main().catch((e) => console.log("EGO_RESULT " + JSON.stringify({ ok: false, error: String(e) })));
