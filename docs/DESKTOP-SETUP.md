# 桌面端接入（DSH 桌面版）

从「飞书机器人发稿」换成「桌面会话发稿」。发布能力（渲染、小红书定时、公众号贴图草稿）完全不变，只换入口。

## 一、两种入口的差别

| | 飞书版（旧） | 桌面版（现在） |
|---|---|---|
| 入口 | 飞书私聊机器人 | DSH 桌面会话里贴稿 |
| 运行时 | ZCode 桌面版 + Bot Channel 长连接 | DSH 桌面版（本会话的 agent） |
| 稿子形态 | 聊天里贴一段文字 | 聊天里贴 / 给文件路径 / `inbox/*.md` 单文件 |
| 发布前预览 | 只能回截图路径 | 回执 + 封面图直接贴在会话里 |
| 风险检测 | `dbs-content-risk-check` Skill | 同（桌面版直接可读技能） |
| 发布动作 | 小红书定时 / 公众号草稿 | 同 |

飞书相关文件已删除（`docs/FEISHU-SETUP.md`、`PIPELINE.agents.md`），历史提交里仍可查。仓库名里的 `feishu` 是历史遗留，不影响使用。

## 二、装依赖（一次性）

```bash
# Python 依赖（渲染出图用）
pip3 install playwright pillow

# ego lite 浏览器 + CLI（发布用，登录态人机共用）
# 装好 ego lite 后，确认 CLI 在 PATH：
which ego-browser          # 常见位置 ~/.local/bin/ego-browser

# 渲染用系统 Chrome，不需要跑 playwright install
```

## 三、把仓库放进 DSH 工作区

DSH 桌面版默认工作区是 `~/Documents/deepseek-harness/default-workspace`。推荐把流水线放进去，这样会话里能直接 `cd` 到它、也能直接看到渲染出的图片：

```bash
cd ~/Documents/deepseek-harness/default-workspace
git clone https://github.com/qishuai6/xhs-feishu-pipeline.git
```

下文把「这个目录」记作 `<流水线目录>`：`~/Documents/deepseek-harness/default-workspace/xhs-feishu-pipeline`。

## 四、接进工作区 AGENTS.md

DSH 每轮会话读工作区根目录的 `AGENTS.md`。把仓库里 [AGENTS.md](../AGENTS.md) 的「流水线指令」整段复制进：

```
~/Documents/deepseek-harness/default-workspace/AGENTS.md
```

复制时把 `<流水线目录>` 换成真实路径。之后在桌面会话里贴稿就会自动走「检测 → 改稿 → 渲染 → 发布 → 回执」。

> 想先试试不改 AGENTS.md？直接把整段流水线指令贴在对话里说「按这个流程发」，效果一样。

## 五、登录两个平台（各扫一次码）

```bash
cd <流水线目录>/publish
python3 ego_login.py --qr      # 小红书：在 ego lite 里用小红书 App 扫码
python3 ego_login.py --check   # 应显示 ✅

python3 wechat_login.py --qr   # 公众号：微信扫码并选择公众号身份
python3 wechat_login.py --check
```

登录态存在 ego lite 自己的用户配置里，人机共用；过期了重跑一次 `--qr`。

## 六、第一次空跑（强烈建议）

```bash
cd <流水线目录>
python3 make.py --md inbox/示例.md --platform both --dry-run
```

两个平台各填一遍、停在最后一步、**0 次提交点击**。截图和运行报告在 `publish/logs/`，回执在 `notes/<目录>/out/receipt.json`。确认无误再去掉 `--dry-run`。

## 七、日常用法

**A. 桌面会话里贴稿（主入口）**

```
这篇发小红书，定到今晚 21:00：（粘贴正文）
这篇发公众号：（粘贴正文）
都发：（粘贴正文）
```

agent 会完成检测、改稿、落稿、发布，并把封面图和回执贴回来。想先看效果就说「先空跑」或「出图给我看，别发」。

**B. 给文件**

桌面会话里直接说「发这个文件：~/Desktop/稿子.md」。文件按 [inbox/README.md](../inbox/README.md) 的 frontmatter 格式写即可。

**C. 纯命令行（不开对话）**

```bash
cd <流水线目录>
python3 make.py --md inbox/稿子.md --schedule            # 小红书定时
python3 make.py --md inbox/稿子.md --platform wechat     # 公众号草稿
python3 make.py --md inbox/稿子.md --platform both --schedule
```

## 八、排错

| 现象 | 处理 |
|---|---|
| `❌ 标题 N 字（平台口径，上限 20）` | 标题压到 20 字内，脚本不会静默截断 |
| `❌ 正文超长：平台口径 N 字` | 压到 980 以内；平台按 UTF-16 + 组合 emoji 计数 |
| `❌ ego lite 未登录` | 跑对应 `*_login.py --qr` |
| 找不到元素 / 点不到按钮 | 看 `publish/logs/` 最新截图；平台改版时按 `publish/SELECTORS.md` 修选择器，别硬猜 |
| 公众号只存草稿不发 | 设计如此：每天仅 1 次群发额度，发表按钮留给你 |
| 想重新渲染不重发 | `python3 make.py --dir notes/<目录> --keep` 或去掉发布参数只出图 |

## 九、安全边界

- 只操作用你自己的账号、你自己的浏览器、你自己的 IP
- 小红书默认定时发布（有取消窗口）；公众号止步于草稿
- 每次运行的截图和 JSON 报告都在 `publish/logs/`，可回溯每一次点击
- 发布频率自律：一天 1~3 篇，见验证码就停
