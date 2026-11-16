# 稿子待导入（单文件稿）

把要发的稿子写成**一个 Markdown 文件**丢进这个目录，然后在流水线目录执行：

```bash
python3 make.py --md inbox/你的稿子.md --schedule          # 小红书定时
python3 make.py --md inbox/你的稿子.md --platform wechat   # 公众号草稿
python3 make.py --md inbox/你的稿子.md --platform both --schedule
```

脚本会自动编号建 `notes/NNN-标题/`、渲染卡片图、发布，并打印回执。

## 文件格式

```markdown
---
title: 标题（平台口径 ≤20 字，必填）
body: |
  小红书正文，也用作公众号描述。平台口径 ≤980 字。
  这里可以多段。

tags: [话题一, 话题二]
kicker: 系列名 · 004        # 可选：封面左上角小标签
author: 七帅                # 可选
closing: |                  # 可选：结尾页大字；不写就没有结尾页
  多做有复利的事
platform: xhs               # 可选：xhs / wechat / both
schedule: auto              # 可选：auto=当天 19:31；或 "21:00" / "2026-11-18 21:00"
style: card                 # 可选，固定 card
---

# 封面主标题
> 封面导语

## 小节标题

完整长文放这里，会渲染进卡片图。支持：
普通段落、==高亮==、**加粗**、- 列表、Markdown 表格。
```

## 几点说明

- **frontmatter 里的 `platform` / `schedule` 只是默认值**：命令行不给 `--schedule` / `--draft` / `--dry-run` 时只出图，绝不会因为稿子里写了 `schedule: auto` 就自己发出去
- **frontmatter 只支持够用的子集**：`key: value`、`key: [a, b]`、`key: |` 多行块、引号字符串
- `body` 也可以写成 `body_file: body.txt`，引用同目录的文本文件
- 没写 `body` 也能出图（只渲染），但发布前必须补上
- 文件名随意，导入后会按标题自动生成 `notes/NNN-slug/`
- 导入是**新建**一篇，不会覆盖已有目录；重跑会得到新序号（配色跟着轮换）
- `inbox/` 里的稿子默认不入 git（`inbox/README.md` 和 `示例.md` 除外）

## 看示例

```bash
python3 make.py --md inbox/示例.md --platform both --dry-run
```
