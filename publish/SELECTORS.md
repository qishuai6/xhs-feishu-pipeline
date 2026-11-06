# 小红书发布端 · 真实选择器实测记录

> 记录日期：**2026-11-05**（Asia/Shanghai）
> 环境：macOS + 系统 Chrome 156.0.8078.80，Playwright(Python 3.9) `launch_persistent_context(channel="chrome")`
> 方法：`explore.py` / `login.py` / `publish.py` 实际打开页面、真实上传、真实填写后 dump DOM + 截图，不是猜的。
> 每条都标注了**是在「登录墙」还是「已登录」状态下看到的**。

一句话结论（给维护者）：
**小红书的发布页比登录页难搞得多**——默认停在视频 tab、tab 有隐藏副本、正文是 Tiptap、话题是 tippy 浮层、底部按钮在 **closed shadow DOM** 里（普通 locator 查不到，必须 CDP 穿透）。

---

## 一、登录墙状态（已实测 ✅）

### 1. 入口与重定向

| 访问 | 结果 |
|---|---|
| `https://creator.xiaohongshu.com` | 302 → `https://creator.xiaohongshu.com/login` |
| `https://creator.xiaohongshu.com/publish/publish` | 302 → `/login?source=&redirectReason=401&lastUrl=%252Fpublish%252Fpublish` |

未登录时任何发布页都会被挡到 `/login`，且带 `lastUrl`，登录成功后会跳回原页面。

### 2. 登录卡片默认态：短信登录（不是扫码！）

| 用途 | 选择器 | 说明 |
|---|---|---|
| 登录按钮 | `text=登 录`（中间有空格） | class `css-n0yaji css-1qm1xja dyn beer-login-btn` |
| 手机号 | `input[placeholder="手机号"]` | |
| 验证码 | `input[placeholder="验证码"]` | |
| **切扫码的图标** | `img.css-wemwzq` | 64x64，登录卡片右上角（1440x900 下 ≈ x1241,y273） |

点 `img.css-wemwzq` 后断言出现 **`APP扫一扫登录`** 即切换成功。
切换后二维码本体：`img.css-1lhmg90`，data:image/png;base64，原图 196x196。
→ `login.py` 直接解码 base64 存成原生位图 `logs/qr-login.png`（比截图干净）。

### 3. 登录态判断逻辑

1. URL 含 `/login` → 未登录
2. 页面有 `APP扫一扫登录` / `短信登录` → 未登录
3. 页面有 `发布笔记` / `上传图文` → 已登录
4. 兜底：cookie 名里有 `web_session`（只看名字，不读值）

---

## 二、已登录 · 发布页（已实测 ✅ 2026-11-05）

### 1. 页面默认停在「上传视频」tab（最大的坑）

`goto /publish/publish` 后默认是视频 tab，**全页面只有一个 file input，还是视频的**：

```html
<input class="upload-input" type="file"
       accept=".mp4,.mov,.flv,.f4v,.mkv,.rm,.rmvb,.m4v,.mpg,.mpeg,.ts">
```

→ 必须先点「上传图文」tab，图片上传框才会渲染出来。

### 2. Tab 元素有 3 份（隐藏副本会骗过 is_visible）

```html
<!-- ① 真实可点：带 data-hp-bound="1" -->
<div class="creator-tab" data-hp-bound="1"><span class="title">上传图文</span></div>

<!-- ② 测量副本：opacity:1e-05; z-index:-1; pointer-events:auto -->
<div class="creator-tab" data-hp-kind="creator-tab-上传图文" aria-hidden="true"
     style="position:absolute; opacity:1e-05; pointer-events:auto; z-index:-1; ...">
  <span class="title">上传图文</span></div>

<!-- ③ 视口外副本：left:-9999px（bounding box 非空，is_visible() 返回 True！） -->
<div class="creator-tab" style="position:absolute; left:-9999px; top:-9999px;">
  <span class="title">上传图文</span></div>
```

**结论：不能用 `is_visible()` 过滤**（②③ 都会被判 True）。必须按：
- `getBoundingClientRect()` 在视口内（x>=0, y>=0, right<=innerWidth, bottom<=innerHeight）
- 沿祖先链累计 `opacity >= 0.05`、`visibility/display` 正常、`pointer-events !== none`
- 优先挑带 `data-hp-bound="1"` 的那个

`publish.py` 的 `pick_tab()` / `switch_to_image_tab()` 已按此实现，并断言切完后出现图片上传区（accept 含 png 的 input 或「拖拽图片/上传图片」文案），失败重载重试。

### 3. 上传图文的 file input（已实测）

切 tab 后出现（页面 `accept` 精确值）：

```html
<input class="upload-input" type="file" accept=".jpg,.jpeg,.png,.webp" multiple="">
```

- **支持 multiple**（实测一次 `set_input_files([7张])` 全部按顺序上传成功）
- 上传后缩略图选择器：`img.preview`（80x80），实测 7 张 = 7 个
- 计数文案：`.img-list .status` → `7/18`（**图片上限 18 张**）
- 上传中提示文案：`上传中 / 正在上传 / 上传进度`

### 4. 标题输入框（已实测）

```html
<input type="text" placeholder="填写标题会有更多赞哦" ...>   <!-- 位置 ≈ (362,276)，宽 500 -->
```

- 无 `maxlength` 属性，但平台侧限制 **20 字**（脚本自己截断并警告）
- 需要图片上传完成后才渲染出来

### 5. 正文编辑器（已实测，Tiptap）

```html
<div contenteditable="true" role="textbox"
     class="tiptap ProseMirror ProseMirror-focused" tabindex="0">
  <p>第一段。</p><p>第二段。</p>
</div>
```

- 选择器：`div.tiptap[contenteditable="true"]`（位置 ≈ (362,320)）
- **换行规则（实测）**：整段 `insert_text` 带 `\n` 会变成多个多余空行；
  正确写法 = 段落之间按 `Enter`（生成新 `<p>`），段内换行按 `Shift+Enter`（生成 `<br>`）
- 正文计数器 `N/1000` → **正文上限 1000 字**
- 输入 `#` 会进入话题联想模式（见下）

### 6. 话题联想面板（已实测，tippy 浮层）

输入 `#话题名` 后弹出：

```html
<div role="tooltip" data-placement="bottom-start">
  <div class="tippy-content">
    <div id="creator-editor-topic-container" class="items">
      <div class="item is-selected"><span class="name">#职场经验howto</span><span class="num">2.9亿浏览</span></div>
      ...
```

- 候选选择器：`#creator-editor-topic-container .item .name`
- 点中候选后编辑器里生成真正的话题节点：
  `<a class="tiptap-topic" data-topic='{"name":"...","id":"..."}' contenteditable="false">`
- 校验：`div.tiptap a.tiptap-topic` 数量
- **光标必须先在正文末尾**：`End` 只是行尾（实测会把话题插进正文中间），
  macOS 用 `Meta+ArrowDown` 到文末，再校验「光标后面没有内容」才允许输入
- 精确同名候选才点选；没有同名候选就保留纯文本并警告（不硬凑相似话题）
- ⚠️ 已知平台行为：点选的话题如果恰好是「平台活动话题」，页面会自动提示「已关联活动」（如 #长期主义 关联了活动）。这是平台行为，脚本不控制。

### 7. 底部「暂存离开 / 发布」按钮：closed shadow DOM（已实测）

页面底部有一段：

```html
<xhs-publish-btn is-publish="true" is-save-draft="true"
                 submit-text="发布" save-text="暂存离开"
                 submit-disabled="false" submit-loading="false"
                 save-disabled="false"></xhs-publish-btn>
```

这是一个自定义元素，**内容在 closed shadow root 里**：

```
#shadow-root (closed)
  <div class="publish-page-publish-btn">
    <button type="button" class="ce-btn white">暂存离开</button>   ← 存草稿（按钮文案就叫「暂存离开」，不叫「存草稿」！）
    <button type="button" class="ce-btn bg-red">发布</button>
  </div>
```

实测验证：
- `page.locator('xhs-publish-btn').count()` → 1
- `page.locator('xhs-publish-btn button').count()` → **0**
- `page.get_by_text('暂存离开')` → **0**（closed shadow，普通 locator/get_by_text 都查不到）
- `el.shadowRoot` → **null**（closed）

**唯一可行路径 = CDP 穿透：**
```
context.new_cdp_session(page)
DOM.enable
DOM.getDocument(depth=-1, pierce=True)      # 能拿到 closed shadowRoots
DOM.getBoxModel(nodeId)                      # 拿按钮坐标
page.mouse.click(x, y)                       # 真实鼠标事件点击
```
`publish.py` 的 `get_publish_buttons()` / `_shadow_click()` 已实现，并带安全闸门：
- 只点文案为 `暂存离开` 的按钮（草稿模式）
- 如果目标文案匹配 `发布/立即发布/确认发布/…` 且没加 `--publish`，直接抛异常中止
- dry-run 只定位、不点击
- 实测坐标：暂存离开 ≈ (606,855)，发布 ≈ (750,855)（1440x900，会随窗口变化，不写死）

**点「暂存离开」之后的行为（2026-11-05 已实测，真实账号）**：

- **没有二次确认框**，点下去直接保存；
- 顶部弹出 toast **「保存成功」**；
- 同时自动打开**草稿箱浮层**（`视频笔记(0) / 图文笔记(N) / 长文笔记(0) / 播客笔记(0)` 四个 tab，默认在视频笔记，图文草稿在「图文笔记」tab）；
- 草稿列表每条显示：封面缩略图、标题（未填标题的显示「暂无笔记标题」）、`保存于 YYYY-MM-DD HH:MM:SS`、「编辑」「删除」；
- 浮层顶部提示：`草稿存储于当前使用的浏览器本地，在清除浏览器数据时会被删除；图文笔记最多支持保存100篇草稿，超出后将默认删除最后一篇草稿，请及时发布。`
- 证据：`logs/final-20261105-102646.png`、`logs/run-report-20261105-102553.json`（`result: draft-ok`，`draft` 步骤 5.7s，点击审计里只有一次「暂存离开」`allow_submit=False`）。

**⚠️ 编辑器会自动存草稿（2026-11-05 实测，重要）**：

小红书发布页编辑器**会周期性自动保存草稿**，跟有没有点「暂存离开」无关。实测证据：草稿箱里有 9 条图文草稿，其中 8 条的保存时间精确对应「没有点过任何提交按钮」的 dry-run / 探针会话；只有 10:26:41 那条是真实点「暂存离开」产生的。

| 草稿保存时间 | 对应会话 | 是否点过「暂存离开」 |
|---|---|---|
| 10:12:57 / 10:14:16 / 10:15:32 / 10:16:35 / 10:17:48 | 探针会话（只上传/填写，未点提交） | 否 |
| 10:21:03 / 10:22:40 / 10:23:47 | 3 次 dry-run（`--dry-run`，run-report 点击审计 0 次草稿点击） | 否 |
| 10:26:41 | 真实存草稿 | 是 |

细节：自动保存一般在内容变更后 30~60 秒内落盘（短会话可能来不及存；编辑器右下角有「编辑于 刚刚」这类状态）。
→ **结论：dry-run / 探针跑多了会在草稿箱攒垃圾草稿**。文档里已提醒用户手动清理；脚本**绝不**自动删除草稿（破坏性操作）。
证据文件：`logs/probe-drafts-20261105-102803.json` / `.png`（草稿箱只读 dump）。

### 8. 定时发布（2026-11-05 实测：账号可用 ✅）

**开关 DOM（实测，在「更多设置」区域）**：

```html
<div class="post-time-wrapper">
  <div class="post-time-switch-container">
    <div class="custom-switch-wrapper">
      <div class="custom-switch-card">
        <span class="has-tips">定时发布</span>
        <div class="custom-switch-switch">
          <div class="d-switch d-clickable d-switch-default">
            <input type="checkbox">
          </div>
        </div>
      </div>
    </div>
  </div>
  <div class="date-picker-container">   <!-- 开关勾选后才渲染 -->
    <div class="d-datepicker">
      <div class="d-datepicker-input-filter show">
        <input class="d-text" value="2026-11-05 11:37">   <!-- 默认值 = 当前时间 + 1 小时 -->
      </div>
      <div class="d-datepicker-suffix-indicator">…日历图标…</div>
    </div>
  </div>
</div>
```

**实测结论**：

1. **这个账号可以使用定时发布**：开关打开后没有出现「暂不可用 / 需要认证 / 账号不支持」等提示，日期选择器正常渲染。
2. 开关定位：`.post-time-wrapper .custom-switch-card`（has_text=定时发布）→ 点里面的 `.d-switch`。
   - 那个 checkbox 在视口外，**直接点 checkbox 会报 "Element is outside of the viewport"**；先 `scroll_into_view_if_needed()`，再点 `.d-switch`。
   - 勾选后 `input[type=checkbox].is_checked()` == true，可断言。
3. 时间设置：`.date-picker-container input.d-text` → `fill("YYYY-MM-DD HH:MM")` → `Enter` → **回读 input.value 必须完全等于目标值**。
4. **过去时间会被平台静默回退**（重要）：实测填 `2026-11-05 09:00`（当时 10:38），回读变成默认值 `2026-11-05 11:38`，没有任何报错提示。
   → 所以必须「先自己校验未来时间 + 回读严格比对」，`publish.py` 已实现，不一致直接报错退出，不会自己猜时间。
5. 日历面板二次校验：点 `.d-datepicker-suffix-indicator` 打开面板，面板显示 `2026年` `11月` + 日期格 + `19 时` `31 分`，可确认设置生效。
6. **开关打开后，底部红色按钮文案会从「发布」变成「定时发布」**（closed shadow DOM 里同样如此）：
   - 未开启时：`['暂存离开', '发布']`
   - 开启后：`['暂存离开', '定时发布']`
   → 定时模式必须点文案为「定时发布」的那个按钮，`publish.py` 的 `click_publish(submit_text=...)` 已支持。
7. 发布后验证入口：`笔记管理`（实际 URL `https://creator.xiaohongshu.com/new/note-manager`），tab 有
   `全部 N` / `已发布` / `审核中` / `未通过` / `定时发布`；卡片选择器 `.note-card__title` + `.note-card__time`。
   实测：刚提交完记录先出现在 **「审核中」tab**，卡片状态文字为「定时发布 2026-11-05 19:31」；
   审核通过后才会进入「定时发布」tab。`publish.py` 会依次找 `定时发布 → 审核中 → 全部` 并截图。

**状态（2026-11-05 全流程已实测 ✅）**：

- 开关可用性 + 时间设置 + 回读/面板校验 = ✅ 已实测；
- **真点【定时发布】= 没有二次确认弹窗，点下去直接提交**；
- 提交后去「笔记管理」：记录先出现在 **「审核中」tab**（不是「定时发布」tab），状态文字为 **「定时发布 2026-11-05 19:31」**；
- 证据：`logs/step-scheduled-verify-20261105-104941.png`、`logs/run-report-20261105-104837.json`。

### 9. 其他实测到的元素（暂不操作）

| 元素 | 说明 |
|---|---|
| 功能引导弹窗 | 上传后弹「图片可以编辑啦，快来试试吧」+ `我知道了` 按钮，遮挡编辑区，脚本会自动关掉 |
| `图片编辑 7/18` | 图片区标题 + 计数 |
| `展开所有图片` | footer 按钮 |
| `获取封面建议` / `封面预览` | 封面相关入口（**未实测设置动作**，见第四节） |
| `智能标题` | 推荐标题按钮 |
| `添加内容类型声明` / `原创声明` / `加入合集` | 内容设置，脚本不碰 |
| 手机预览（笔记预览/封面预览） | 右侧实时预览，不影响填写 |

### 10. 实测证据文件（logs/）

| 文件 | 内容 |
|---|---|
| `logs/explore-initial-20261105-094104.*` | 登录墙（短信登录态）DOM/截图 |
| `logs/probe-login-toggled.png` | 切到扫码态的截图 |
| `logs/dump-fail-upload-set-20261105-100927.{html,txt,png}` | 第一次真实失败：默认视频 tab + 视频 input 的现场 |
| `logs/probe-editor-20261105-101300.json` | 图文编辑态完整元素清单（按钮/输入框/contenteditable） |
| `logs/probe2-*.html` | 标题/正文/话题操作后的 DOM |
| `logs/dry-run-final-20261105-102351.png` | **最终 dry-run 截图**（7 图 + 标题 + 正文 + 7 话题，停在最后一步） |
| `logs/run-report-20261105-102257.json` | dry-run 运行报告（步骤/耗时/选择器/点击审计） |
| `logs/final-20261105-102646.png` | **真实存草稿**：toast「保存成功」+ 草稿箱浮层（图文笔记 9 条） |
| `logs/run-report-20261105-102553.json` | 真实存草稿运行报告（result: draft-ok，0 次发布点击） |
| `logs/probe-drafts-20261105-102803.{png,json}` | 草稿箱只读 dump（9 条图文草稿的标题+保存时间） |
| `logs/dry-run-final-20261105-104453.png` | 定时发布 dry-run：内容填好 + 定时 19:31 + 停在「定时发布」按钮前 |
| `logs/run-report-20261105-104453.json` | 定时发布 dry-run 报告（mode=schedule-dry，0 次 allow_submit） |
| `logs/probe-sched2-*.html` / `probe-sched3-*.html` | 定时开关、日期选择器、日历面板的真实 DOM |

---

## 三、发布流程实测结论（2026-11-05）

| 步骤 | 结论 | 状态 |
|---|---|---|
| 打开发布页 | `goto /publish/publish`，已登录直接落在发布页 | ✅ 实测 |
| 切图文 tab | 默认视频 tab，必须切；用视口+opacity+data-hp-bound 过滤隐藏副本 | ✅ 实测 |
| 上传图片 | `input[accept='.jpg,.jpeg,.png,.webp'][multiple]`，一次多选按顺序上传，`img.preview` 校验数量 | ✅ 实测（7/7） |
| 标题 | `input[placeholder='填写标题会有更多赞哦']`，20 字上限（平台侧） | ✅ 实测 |
| 正文 | `div.tiptap[contenteditable=true]`，段落 Enter / 段内 Shift+Enter，1000 字上限 | ✅ 实测（610 字） |
| 话题 | 输入 `#tag` → tippy 面板 `#creator-editor-topic-container` → 点同名候选 → `a.tiptap-topic` | ✅ 实测（7/7 全中） |
| 封面 | 未实现（只看到「封面预览/获取封面建议」入口） | ❌ 未实现，显式提示 |
| 存草稿 | 按钮叫「暂存离开」，在 closed shadow DOM，需 CDP + 真实鼠标点击；点后**无二次确认**，直接「保存成功」+ 打开草稿箱浮层 | ✅ 实测（真实账号） |
| 存草稿后的行为 | 顶部 toast「保存成功」+ 草稿箱浮层自动打开（图文笔记 tab 可见新草稿） | ✅ 实测（2026-11-05 10:26） |
| 定时发布开关 | `.post-time-wrapper .custom-switch-card >> .d-switch`；勾选后 checkbox=true | ✅ 实测（账号可用，无限制提示） |
| 定时时间设置 | `.date-picker-container input.d-text` fill `YYYY-MM-DD HH:MM` + Enter，回读严格比对；过去时间会被静默回退 | ✅ 实测（19:31 回读一致；09:00 回退默认值） |
| 定时发布按钮 | 开关打开后底部红按钮文案从「发布」变「定时发布」，仍在 closed shadow DOM | ✅ 实测（dry-run 定位到，未点） |
| 定时发布后验证 | 笔记管理 `/new/note-manager` → tab「审核中」→ `.note-card__title` 匹配 + 状态文字含「定时发布 HH:MM」+ 截图 | ✅ 实测（2026-11-05，真发布成功） |

---

## 四、封面 / 拖动排序（未实现 ❌）

**结论：封面设置功能 not implemented，`--cover-index` 明确提示并忽略，不静默跳过。**

理由：登录后确实看到了「封面预览」「获取封面建议」入口，但**没有实测到「把第 N 张设为封面」的真实可点路径**，按项目规矩不猜、不盲点。

已知（预期，未验证）：
1. 图片顺序 = `--images` 列表顺序（目录输入按文件名自然排序），实测 7 张缩略图顺序正确。
2. 默认封面 = 第 1 张（小红书惯例，未实测确认）。
3. 拖动排序/设为封面：等后续实测到入口再实现；重点看 `logs/publish-dom.txt` 里含「封面」「拖动」「排序」的元素。

---

## 五、踩过的坑（2026-11-05 实测，按踩坑顺序）

1. **Playwright 自带 chromium 版本不匹配** → 必须 `channel="chrome"` 走系统 Chrome。
2. **后台进程被 SIGTERM 杀掉会残留 SingletonLock** → `common.chrome_context()` 会自动检测持锁 pid，已死才清锁重试。
3. **登录页默认是短信登录** → 点 `img.css-wemwzq` 切扫码，断言 `APP扫一扫登录`。
4. **发布页默认停在「上传视频」tab** → 第一个 `input[type=file]` 是视频的（accept 全是视频后缀），
   直接 `set_input_files(多张)` 会报 `Non-multiple file input can only accept single file`。
5. **「上传图文」tab 有 3 份**（真实/opacity 1e-05 测量副本/left:-9999px 视口外副本），`is_visible()` 会误判。
6. **正文是 Tiptap/ProseMirror**：整段 insert_text 的 `\n` 会变成多个空行，必须段落 Enter 分开。
7. **`End` 只是行尾**：话题会插进正文中间，必须 `Meta+ArrowDown` 到文末并校验。
8. **话题联想面板在 tippy 浮层**：`get_by_text().first` 会先命中手机预览里的同名文字（点错），
   必须限定 `#creator-editor-topic-container .item` 再点。
9. **底部按钮在 closed shadow DOM**：`get_by_text('暂存离开')` 返回 0，`shadowRoot` 是 null；
   必须 CDP `DOM.getDocument(pierce=True)` + `DOM.getBoxModel` + `mouse.click`。
10. **草稿按钮不叫「存草稿」**：叫 **「暂存离开」**（`save-text="暂存离开"`）。
11. **上传后有功能引导弹窗**（「图片可以编辑啦」+「我知道了」）会挡住编辑区，先点掉。
12. **话题可能是平台活动话题**：点选后页面自动「已关联活动」，属平台行为。
13. **`wait_for_load_state("networkidle")` 经常超时**（长连接），脚本已容错。

---

## 六、改版后怎么更新这份文档

```bash
# 登录页
python3 explore.py --headed

# 已登录后 dump 发布页（需要登录态）
python3 login.py            # 已登录时会自动 dump
python3 publish.py --dump   # 或只 dump，不填内容

# 真实链路空跑（最推荐，只填不点提交）
python3 publish.py --meta "../notes/002-50岁还在跳槽/out" --body body.txt --dry-run --headed
```

看 `logs/explore-*.json` / `logs/publish-dom.txt` / `logs/run-report-*.json`，
按「元素清单」更新本文档和 `publish.py` 的对应函数（`pick_tab` / `find_image_input` / `fill_title` / `fill_body` / `add_tags` / `get_publish_buttons`）。

---

## ego lite 实测补充（2026-11-06，发布链路切换到 ego-browser）

- **`page.evaluate` 必须传函数**：传字符串会静默返回 `{}`，所有探测逻辑用真函数注入。
- **选择器要求唯一匹配**：ego 不允许 `.first()` 式歧义。页面有多个同类 input 时，
  用「JS 标记法」：evaluate 里按候选顺序找第一个可见元素打 `data-xhs-*` 标记，
  再用标记选择器操作（见 ego_publish.mjs 的 findTitleInput / findImageInput）。
- **closed shadow DOM 拿不到**：ego 的 CDP 反检测层会把 shadow DOM（连同 xhs-* 自定义
  元素 host）从 `DOM.getDocument(pierce:true)` 里整个滤掉，Playwright 的穿透法失效。
  **可行替代**：`Accessibility.getFullAXTree` 天然穿透 closed shadow root → 按角色
  button + 名称（暂存离开/发布/定时发布）拿 `backendDOMNodeId` →
  `DOM.getBoxModel({backendNodeId})` → 真实鼠标坐标点击。
- **定位权限弹窗**：小红书页面加载即请求 geolocation，会触发 ego 浏览器级权限提示并
  中断自动化。解法：`Page.addScriptToEvaluateOnNewDocument` 注入脚本接管
  `navigator.geolocation`（直接回调 PERMISSION_DENIED），弹窗不再出现。
- **登录态**：跟 ego lite 浏览器配置走（人机共用）。`ego_login.py --check` 判据：
  未被重定向到 /login 且页面含「上传图文|笔记管理|数据看板」特征文字。
  不要用「短信登录」字样做未登录判据（已登录页面也会残留，实测误报）。

---

## 微信公众号实测（2026-11-06，文章编辑器路线，ego lite）

- **登录**：mp.weixin.qq.com 微信扫码（选公众号身份），登录态跟 ego lite 走。
  未登录特征：页面含「微信扫一扫，选择公众平台账号登录」。
- **「贴图」模式放弃**：上传控件是动态克隆 input，setInputFiles/CDP setFileInputFiles/
  waitForFileChooser 全部实测失败（首张成功、后续静默无效）。改走「文章」编辑器。
- **文章编辑器**：首页「新的创作 → 文章」开新 tab（appmsg_edit_v2）。
  插图：点工具栏「图片」展开下拉 → `setInputFiles("input[name='file']", N张)`
  **一次全部按序插入正文**（静态 input + multiple，最可靠的一环）。
- **字段**：标题 `div.ProseMirror[data-placeholder*="标题"]`（focus+insertText，≤64字）；
  作者 `input[name="author"]`（≤8字）；摘要 `textarea[name="digest"]`（≤120字）。
  注意：author/digest 在编辑器内部滚动容器里，fill 前要 scrollIntoView。
- **发表三段式**：点「发表」→右侧设置面板展开→再点「发表」→封面层（「从正文选择」→
  弹层选第 1 张 li →「下一步」→「确认」）→再点「发表」→最终弹层：
  群发通知（默认开）/分组通知/**定时开关 `.timer_setting .weui-desktop-switch__box`**
  →日期列表（今天/明天/…）+时间输入框（`input.weui-desktop-form__input`，HH:MM，
  直接设 value + 派发 input/change 事件）→「发表」→（可能二次确认）。
- **定时规则**：5 分钟后 ~ 7 天内；取消定时恢复草稿且**不占群发次数**；
  公众号每天仅 1 次群发额度。
