# xhs-feishu-pipeline

**桌面端贴稿 → 发布前风险检测 → 自动改稿 → 小红书定时发布 + 微信公众号贴图草稿**，一条全自动流水线。

在 DSH 桌面版的会话里把稿子贴进来（或给一个文件路径），agent 会：逐句风险检测（机器审核层 + 内容实质层）→ 按检测结果最小修改 → 长文自动排版成深色卡片图 → 填进两个平台（小红书定时发布、公众号贴图存草稿）→ 回发检测摘要、改动清单、封面图和发布凭证。

```
DSH 桌面会话（你贴稿 / 给文件）
   ↓  agent 读取工作区 AGENTS.md 的流水线指令
dbs-content-risk-check 风险检测
   ↓  只做最小修改，保留观点、口语风格与传播钩子
inbox/稿子.md（单文件：frontmatter + 长文）
   ↓  make.py --md 自动编号建目录
render：长文 → 3:4 深色卡片图（一篇一色：蓝→绿→红→黑轮换）
   ↓                              ↓
小红书：ego lite 填表          公众号：ego lite「贴图」
  标题/正文/话题/定时            6 图 + 标题 + 描述 → 保存草稿
  默认当天 19:31 到点自动发      （每天 1 次群发额度，发表动作留给你）
   ↓                              ↓
会话里回执：检测摘要 + 改动清单 + 封面图 + 「定时发布/草稿」回读凭证
```

## 支持什么

| 能力 | 说明 |
|---|---|
| 单文件发稿 | 一个 Markdown 文件写 frontmatter（标题/正文/话题/时间/平台），`--md` 一条命令跑完全流程 |
| 小红书图文定时发布 | card 深色卡片风格，整篇单色、颜色按笔记序号轮换（001蓝→002绿→003红→004黑）；封面大字号、信息流缩略图可读 |
| 微信公众号贴图 | 「贴图」模式：卡片图 + 标题(≤20字) + 描述(≤1000字，自动分段)，**填好保存草稿**，不自动群发 |
| 双平台一次跑 | `--platform both`：小红书定时 + 公众号草稿，一份回执看两边结果 |
| 双平台字数预检 | 按平台真实口径（UTF-16 + 组合 emoji）预检，超限直接拦下，不静默截断 |
| 发布凭证 | 每次运行留截图 + JSON 运行报告（`publish/logs/`）+ 汇总回执（`out/receipt.json`） |
| 平台拒绝检测 | 发布后抓平台错误提示（字数超限/内容不合规等），失败原因一目了然 |
| 风险检测 | 集成 [dbs-content-risk-check](https://github.com/dontbesilent2025/dbskill)（CC BY-NC 4.0，需单独安装） |

## 环境要求

| 依赖 | 用途 | 说明 |
|---|---|---|
| **[ego lite](https://ego.fit) 浏览器 + `ego-browser` CLI** | 发布用浏览器 | 登录态人机共用；装好 ego lite 后 CLI 通常已在 `~/.local/bin/ego-browser` |
| Python 3.9+ | 渲染与编排 | `pip3 install playwright pillow`（playwright 仅出图用，**不要**跑 `playwright install`） |
| Chrome | 渲染出图引擎 | 渲染用系统 Chrome 截图；发布不依赖它 |
| [DSH 桌面版](https://deepseek.com) | 桌面对话入口 | 只用命令行也可以不装，`make.py` 单独能跑 |

## 快速开始

### 1. 登录（每个平台扫一次码，登录态存在 ego lite 里）

```bash
cd publish
python3 ego_login.py --qr      # 小红书：在 ego lite 里用小红书 App 扫码
python3 ego_login.py --check   # 应显示 ✅

python3 wechat_login.py --qr   # 公众号：微信扫码并选择公众号身份
python3 wechat_login.py --check
```

### 2. 写第一篇（单文件，推荐）

复制 `inbox/示例.md` 改成你的稿子，frontmatter 只需要这几行：

```markdown
---
title: 标题，平台口径 20 字以内
body: |
  小红书正文，也用作公众号描述。平台口径 980 字以内。
  可以多段。

tags: [话题一, 话题二]
platform: both      # xhs / wechat / both
schedule: auto      # auto=当天 19:31；或 "21:00"
---

# 封面主标题
> 封面导语
完整长文（进图片）
```

frontmatter 里的 `platform` / `schedule` 只是默认值：命令行不给 `--schedule` / `--draft` / `--dry-run` 时**只出图，不会发布**。

完整字段见 [inbox/README.md](inbox/README.md)。老的两文件方式（`notes/xxx/long.md` + `note.json`）仍然可用，适合要精细控制 kicker/closing/封面的场合。

### 3. 先空跑验证（强烈建议第一次这么做）

```bash
python3 make.py --md inbox/示例.md --platform both --dry-run
```

两个平台各填一遍、停在最后一步、0 次提交点击。截图和运行报告都在 `publish/logs/`，回执在 `notes/<目录>/out/receipt.json`，确认无误再真发。

### 4. 真实发布

```bash
# 小红书：定时发布（默认当天 19:31，已过顺延次日；可指定时间）
python3 make.py --md inbox/稿子.md --schedule
python3 make.py --md inbox/稿子.md --schedule "21:00"
python3 make.py --md inbox/稿子.md --schedule "2026-11-18 09:30"

# 微信公众号：贴图填好 + 保存草稿（到 mp.weixin.qq.com 草稿箱过目后手动发表）
python3 make.py --md inbox/稿子.md --platform wechat

# 两个都发
python3 make.py --md inbox/稿子.md --platform both --schedule
```

发布过程会打开 ego lite 自动操作，全程可见；每一步截图留证。小红书定时发出前可在 App「笔记管理」取消或修改。

## 桌面会话全自动流水线（主入口）

把 [AGENTS.md](AGENTS.md) 的「流水线指令」复制进工作区 `AGENTS.md`（DSH 桌面版每轮会话自动读取），之后在会话里贴稿即可：

> 这篇发小红书，定到今晚 21:00：（粘贴正文）
> 这篇发公众号：（粘贴正文）
> 都发：（粘贴正文）

配置步骤见 **[docs/DESKTOP-SETUP.md](docs/DESKTOP-SETUP.md)**，核心三步：

1. 仓库放进 DSH 工作区（或任意目录，AGENTS.md 里写绝对路径）
2. 把 `AGENTS.md` 的流水线指令追加到工作区根目录 `AGENTS.md`
3. 两个平台各扫码登录一次，先 `--dry-run` 空跑

## 目录结构

```
├── make.py                  总入口：单文件导入 / 渲染 → 打包 → 发布（--platform xhs|wechat|both）
├── AGENTS.md                桌面端流水线指令（复制进工作区 AGENTS.md）
├── render/render.py         长文 → 3:4 卡片图（card/minimal/magazine/dark 四风格）
├── publish/
│   ├── ego_publish.mjs/.py  小红书发布驱动（ego lite；AX 树穿透 closed shadow DOM）
│   ├── ego_login.py         小红书扫码登录/检查
│   ├── wechat_publish.mjs/.py 公众号贴图驱动（逐张自动插图 + 分段描述 + 存草稿）
│   ├── wechat_login.py      公众号扫码登录/检查
│   ├── textlen.py           平台字数口径 + 安全截断（UTF-16 + 组合 emoji）
│   ├── SELECTORS.md         全部平台实测结论（选择器/坑/计数口径）
│   ├── SAFETY.md            会做什么、不会做什么、风险边界
│   └── logs/                每次运行的截图与 JSON 报告（运行痕迹，不入库）
├── inbox/                   单文件稿待导入（格式说明 + 示例）
├── notes/                   笔记目录（模板 + 示例），每篇 = long.md + note.json
├── tests/test_import.py     单文件导入链路的单元测试（不碰浏览器）
└── docs/DESKTOP-SETUP.md    桌面端接入完整指南
```

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `✗ 标题 N 字（平台口径，上限 20）` | 平台按 UTF-16 + 组合 emoji 计数 | 压到 20 字内；脚本不会静默截断 |
| `✗ 正文超长：平台口径 N 字` | 平台口径比肉眼字数大 | 压到 980 字以内（脚本会报平台口径字数） |
| `平台拒绝发布：正文最多支持1000字` | 平台在提交时拦截 | 按提示压缩后重跑 |
| ego lite 弹定位权限 | 部分页面请求 geolocation | 驱动已自动屏蔽，不会弹 |
| 小红书按钮点不到/发布无反应 | 平台改版或窗口尺寸异常 | 看 `logs/` 最新截图；跑 `python3 publish/explore.py` 重新 dump DOM，按 `SELECTORS.md` 修 |
| 公众号为什么只存草稿不自动发 | 公众号每天仅 1 次群发额度，自动点错代价过高 | 设计如此；草稿箱过目后一键发表 |
| 提示未登录 | ego lite 登录态过期 | 重跑对应平台的 `*_login.py --qr` |
| 想只重新出图 | 已发过的笔记要改排版 | `python3 make.py --dir notes/xxx --keep` 不会重发 |

## 安全边界（诚实版）

- 只操作**你自己的**小红书/公众号账号：真实 ego lite 浏览器、你自己的 IP、你亲手扫码
- 小红书默认**定时发布**（有取消窗口），`--dry-run` 全程 0 次提交点击，运行报告可回溯每一次点击
- 公众号**止步于草稿**，群发按钮永远由你亲手点
- 浏览器自动化在平台用户协议下属灰色地带：直接封号概率低（风控看行为规律），**发布频率与内容合规请自律**——一天 1~3 篇、坚持原创、见验证码就停
- 详见 [publish/SAFETY.md](publish/SAFETY.md)

## 从飞书版迁移

- 入口从「飞书私聊机器人」换成「DSH 桌面会话」，发布能力不变
- 删除 `docs/FEISHU-SETUP.md`、`PIPELINE.agents.md`，新增 [AGENTS.md](AGENTS.md) 与 [docs/DESKTOP-SETUP.md](docs/DESKTOP-SETUP.md)
- 不再需要 ZCode 的 Bot Channel；仓库名保留 `feishu` 只是历史遗留
- 新增 `--md` 单文件导入、`--platform both`、自动编号、`out/receipt.json` 汇总回执
- 历史提交里的飞书接入文档仍可查（`git log -- docs/FEISHU-SETUP.md`）

## 许可证

本仓库代码以 [MIT](LICENSE) 开源。推荐搭配的 `dbs-content-risk-check` Skill 归原作者所有（CC BY-NC 4.0，非商业），本仓库不含其副本。
