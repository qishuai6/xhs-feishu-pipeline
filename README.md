# xhs-feishu-pipeline

**飞书发稿 → 发布前风险检测 → 自动改稿 → 小红书定时发布**，一条全自动流水线。

在飞书聊天里把稿子发给你的机器人，剩下的不用管：逐句风险检测（平台机器审核层 + 内容实质层）、按检测结果做最小修改、长文自动拆成 3:4 多页图文、填好标题/正文/话题、定时当天 19:31 发布，并把检测摘要、改动清单和发布凭证回发到飞书。

```
飞书私聊（你发稿子）
   ↓
ZCode 机器人（Bot Channel，长连接）
   ↓
dbs-content-risk-check 风险检测（机器审核触发点 + 内容实质问题双层）
   ↓
最小修改（保留观点、口语风格与传播钩子）
   ↓
xhs-auto：长文 → 3:4 多页 PNG → 填表 → 定时 19:31
   ↓
飞书收回执：检测结果 + 改动清单 + 「审核中」回读凭证
```

## 组成

| 目录 | 作用 |
|---|---|
| `make.py` | 总入口：渲染 → 打包 → 登录检查 → 定时发布 |
| `render/render.py` | 长文 Markdown → 小红书 3:4 多页 PNG（HTML/CSS 排版 + Chrome 截图，minimal/magazine/dark 三风格） |
| `publish/` | 浏览器自动化：扫码登录、填表、定时发布、DOM dump 工具 |
| `notes/` | 笔记目录（`模板/` + `示例笔记/`），每篇 = `long.md`（长文，进图片）+ `note.json`（标题/正文/话题，进表单） |
| `docs/FEISHU-SETUP.md` | 飞书开放平台 + ZCode 绑定的完整配置指南（含全部踩坑点） |
| `PIPELINE.agents.md` | 流水线常驻指令模板，复制进你的 `AGENTS.md` 即可 |

## 快速开始

### 0. 依赖

- Google Chrome（脚本调系统 Chrome，**不要**跑 `playwright install`）
- Python 3.9+，两个包：`pip3 install playwright pillow`
- [ZCode](https://zcode.app) 桌面版（流水线的机器人运行时）

### 1. 小红书登录（每台电脑一次）

```bash
cd publish
python3 login.py --qr --headed    # 弹出 Chrome，小红书 App 扫码
python3 login.py --check          # 应显示 ✅ 已登录
```

### 2. 单机模式（不接飞书也能用）

```bash
python3 make.py --new 001-选题名        # 从模板新建笔记目录
# 编辑 notes/001-选题名/long.md（长文）和 note.json（标题/正文/话题）
python3 make.py --dir notes/001-选题名 --dry-run --headed   # 试填，停在最后一步，0 次发布点击
python3 make.py --dir notes/001-选题名 --schedule --headed  # 定时当天 19:31
```

### 3. 接入飞书流水线

按 [docs/FEISHU-SETUP.md](docs/FEISHU-SETUP.md) 配置（约 10 分钟），核心三步：

1. 飞书开放平台创建应用并**发布**（可用范围选**全体成员**——见踩坑清单）
2. ZCode 设置 → 移动端远程控制 → Bot Channels → 新建飞书机器人（扫码自动创建应用）→ 飞书里发 `/bind` 绑定工作区
3. 把 [PIPELINE.agents.md](PIPELINE.agents.md) 内容复制进你工作区的 `AGENTS.md`

之后在飞书私聊发稿子即可，例如：

> 帮我检测一下这篇能不能发，改好以后定到今晚 21:00：（粘贴正文）

## 风险检测 Skill

流水线的检测环节使用 [dontbesilent/dbskill](https://github.com/dontbesilent2025/dbskill) 中的 **`dbs-content-risk-check`**（面向创作者的发布前风险检查：平台机器审核触发点 + 内容实质问题双层逐句检测，保留强观点与传播钩子，拒绝绕审写法）。

原作者以 **CC BY-NC 4.0** 许可发布（非商业用途）。安装方式（任选其一）：

```bash
# 方式一：安装完整工具箱（含全部 30+ skills）
npx -y skills add dontbesilent2025/dbskill -g --all

# 方式二：只要这一个 skill —— 从上游仓库复制 skills/dbs-content-risk-check/SKILL.md
# 到你的 Agent 技能目录（ZCode: ~/.zcode/skills/dbs-content-risk-check/）
```

本仓库**不包含**该 Skill 的副本，版权与许可归原作者。

## 安全须知（诚实版）

- 脚本操作的是**你自己的**小红书账号：真实 Chrome、真实 IP、你亲手扫码登录、只碰发布页、单账号串行、带随机延迟
- 默认**定时发布**（有取消窗口），`--dry-run` 停在最后一步且全程 0 次提交点击，每次运行留运行报告与截图
- 浏览器自动化在平台用户协议下属灰色地带；直接封号的概率低（风控看的是行为规律），**发布频率与内容合规由你自律**：一天 1~3 篇封顶、坚持原创、见验证码就停
- 详见 [publish/SAFETY.md](publish/SAFETY.md)（会做什么/不会做什么/风险来自哪里）

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `chromium_headless_shell 不存在` | 跑了 `playwright install` | 不需要它；确认脚本用 `channel="chrome"` |
| 提示未登录 | 登录态过期 | `python3 publish/login.py --qr --headed` 重扫 |
| 按钮找不到 / 页面改版 | 小红书改版 | `python3 publish/explore.py` 重新 dump DOM，按 `publish/SELECTORS.md` 修选择器 |
| 定时时间回读不一致 | 给了过去的时间 | 换将来时间，脚本拒收过去时间 |
| 机器人不回消息 | 见 [docs/FEISHU-SETUP.md](docs/FEISHU-SETUP.md) 踩坑清单 | 通常是事件订阅/可用范围/版本未发布三件事 |

## 许可证

本仓库代码以 [MIT](LICENSE) 许可开源；推荐搭配的 `dbs-content-risk-check` Skill 归原作者所有，CC BY-NC 4.0（非商业）。
