# xhs-feishu-pipeline

**飞书发稿 → 发布前风险检测 → 自动改稿 → 小红书定时发布 + 微信公众号贴图草稿**，一条全自动流水线。

在飞书聊天里把稿子发给机器人，它会：逐句风险检测（机器审核层 + 内容实质层）→ 按检测结果最小修改 → 长文自动排版成深色卡片图 → 填进两个平台（小红书定时发布、公众号贴图存草稿）→ 回发检测摘要、改动清单和发布凭证。

```
飞书私聊（你发稿子）
   ↓  ZCode 机器人（Bot Channel，长连接）
dbs-content-risk-check 风险检测
   ↓  只做最小修改，保留观点、口语风格与传播钩子
render：长文 → 3:4 深色卡片图（一篇一色：蓝→绿→红→黑轮换）
   ↓                              ↓
小红书：ego lite 填表          公众号：ego lite「贴图」
  标题/正文/话题/定时            6 图 + 标题 + 描述 → 保存草稿
  默认当天 19:31 到点自动发      （每天 1 次群发额度，发表动作留给你）
   ↓                              ↓
飞书收回执：检测摘要 + 改动清单 + 「定时发布/草稿」回读凭证
```

## 支持什么

| 能力 | 说明 |
|---|---|
| 小红书图文定时发布 | card 深色卡片风格，整篇单色、颜色按笔记序号轮换（001蓝→002绿→003红→004黑）；封面大字号、信息流缩略图可读 |
| 微信公众号贴图 | 「贴图」模式：卡片图 + 标题(≤20字) + 描述(≤1000字，自动分段)，**填好保存草稿**，不自动群发 |
| 双平台字数预检 | 按平台真实口径（UTF-16 + 组合 emoji）预检，超限直接拦下，不再白跑 |
| 发布凭证 | 每次运行留截图 + JSON 运行报告（`publish/logs/`），定时记录回读验证 |
| 平台拒绝检测 | 发布后抓平台错误提示（字数超限/内容不合规等），失败原因一目了然 |
| 风险检测 | 集成 [dbs-content-risk-check](https://github.com/dontbesilent2025/dbskill)（CC BY-NC 4.0，需单独安装） |

## 环境要求

| 依赖 | 用途 | 说明 |
|---|---|---|
| **[ego lite](https://ego.fit) 浏览器 + `ego-browser` CLI** | 发布用浏览器 | 登录态人机共用；装好 ego lite 后 CLI 通常已在 `~/.local/bin/ego-browser` |
| Python 3.9+ | 渲染与编排 | `pip3 install playwright pillow`（playwright 仅出图用，**不要**跑 `playwright install`） |
| Chrome | 渲染出图引擎 | 渲染用系统 Chrome 截图；发布不依赖它 |
| [ZCode](https://zcode.app) 桌面版 | 飞书流水线的机器人运行时 | 只用单机命令行模式可以不装 |

## 快速开始

### 1. 登录（每个平台扫一次码，登录态存在 ego lite 里）

```bash
cd publish
python3 ego_login.py --qr      # 小红书：在 ego lite 里用小红书 App 扫码
python3 ego_login.py --check   # 应显示 ✅

python3 wechat_login.py --qr   # 公众号：微信扫码并选择公众号身份
python3 wechat_login.py --check
```

### 2. 写第一篇笔记

```bash
python3 make.py --new 001-我的选题名
```

编辑生成的两个文件：

- `notes/001-我的选题名/long.md` —— 完整长文（进图片），支持 `#` 标题 / `##` 小节 / 列表 / `==高亮==` / 引用 / **Markdown 表格**（渲染成带表头底色的可视化表格，超长自动跨页、续页保留表头）
- `notes/001-我的选题名/note.json` —— 平台参数：

```json
{
  "style": "card",
  "kicker": "系列名 · 001",
  "author": "你的署名",
  "closing": "结尾页大字\n可换行",
  "title": "小红书标题，20 字以内",
  "body": "小红书正文（也用作公众号贴图描述），平台口径 980 字以内",
  "tags": ["话题一", "话题二"]
}
```

> `style` 固定写 `"card"`：整篇统一一个深色底，颜色由目录名数字前缀轮换（001蓝→002绿→003红→004黑），重渲染不变色。

### 3. 先空跑验证（强烈建议第一次这么做）

```bash
python3 make.py --dir notes/001-我的选题名 --dry-run --keep
```

两个平台各填一遍、停在最后一步、0 次提交点击。截图和运行报告都在 `publish/logs/`，确认无误再真发。

### 4. 真实发布

```bash
# 小红书：定时发布（默认当天 19:31，已过顺延次日；可指定时间）
python3 make.py --dir notes/001-我的选题名 --schedule
python3 make.py --dir notes/001-我的选题名 --schedule "2026-11-10 20:00"

# 微信公众号：贴图填好 + 保存草稿（到 mp.weixin.qq.com 草稿箱过目后手动发表）
python3 make.py --dir notes/001-我的选题名 --platform wechat
```

发布过程会打开 ego lite 自动操作，全程可见；每一步截图留证。小红书定时发出前可在 App「笔记管理」取消或修改。

## 飞书全自动流水线（可选，推荐）

把上面的人工命令变成「飞书发稿即自动执行」。配置约 10 分钟，完整步骤见 **[docs/FEISHU-SETUP.md](docs/FEISHU-SETUP.md)**，核心三步：

1. 飞书开放平台创建应用并发布（**可用范围选全体成员**——最大的坑，空名单=谁也搜不到机器人）
2. ZCode 设置 → 移动端远程控制 → Bot Channels → 新建飞书机器人（扫码）→ 飞书里发 `/bind` 绑定工作区
3. 把 [PIPELINE.agents.md](PIPELINE.agents.md) 的内容复制进你工作区的 `AGENTS.md`

之后在飞书私聊发稿即可：

> 这篇发小红书，定到今晚 21:00：（粘贴正文）
> 这篇发公众号：（粘贴正文）
> 都发：（粘贴正文）

## 目录结构

```
├── make.py                  总入口：渲染 → 打包 → 发布（--platform xhs|wechat）
├── render/render.py         长文 → 3:4 卡片图（card/minimal/magazine/dark 四风格）
├── publish/
│   ├── ego_publish.mjs/.py  小红书发布驱动（ego lite；AX 树穿透 closed shadow DOM）
│   ├── ego_login.py         小红书扫码登录/检查
│   ├── wechat_publish.mjs/.py 公众号贴图驱动（逐张自动插图 + 分段描述 + 存草稿）
│   ├── wechat_login.py      公众号扫码登录/检查
│   ├── SELECTORS.md         全部平台实测结论（选择器/坑/计数口径）
│   ├── SAFETY.md            会做什么、不会做什么、风险边界
│   └── logs/                每次运行的截图与 JSON 报告（运行痕迹，不入库）
├── notes/                   笔记目录（模板 + 示例），每篇 = long.md + note.json
├── docs/FEISHU-SETUP.md     飞书接入完整指南
└── PIPELINE.agents.md       AGENTS.md 流水线指令模板
```

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `❌ 正文超长：平台口径 N 字` | 平台按 UTF-16+组合 emoji 计数，肉眼字数会低估 | 压缩正文（脚本会报出平台口径字数） |
| `平台拒绝发布：正文最多支持1000字` | 平台在提交时拦截（预检漏网） | 按提示压缩后重跑 |
| ego lite 弹定位权限 | 部分页面请求 geolocation | 驱动已自动屏蔽（注入拒绝回调），不会弹 |
| 小红书按钮点不到/发布无反应 | 平台改版或窗口尺寸异常 | 看 `logs/` 最新截图；跑 `python3 publish/explore.py` 重新 dump DOM，按 `SELECTORS.md` 修 |
| 公众号为什么只存草稿不自动发 | 公众号每天仅 1 次群发额度，自动点错代价过高 | 设计如此；草稿箱过目后一键发表 |
| 提示未登录 | ego lite 登录态过期 | 重跑对应平台的 `*_login.py --qr` |

## 安全边界（诚实版）

- 只操作**你自己的**小红书/公众号账号：真实 ego lite 浏览器、你自己的 IP、你亲手扫码
- 小红书默认**定时发布**（有取消窗口），`--dry-run` 全程 0 次提交点击，运行报告可回溯每一次点击
- 公众号**止步于草稿**，群发按钮永远由你亲手点
- 浏览器自动化在平台用户协议下属灰色地带：直接封号概率低（风控看行为规律），**发布频率与内容合规请自律**——一天 1~3 篇、坚持原创、见验证码就停
- 详见 [publish/SAFETY.md](publish/SAFETY.md)

## 许可证

本仓库代码以 [MIT](LICENSE) 开源。推荐搭配的 `dbs-content-risk-check` Skill 归原作者所有（CC BY-NC 4.0，非商业），本仓库不含其副本。
