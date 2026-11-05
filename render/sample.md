# 示例长文
# 用来测试渲染链路

> 一篇 Markdown 如何变成多页 3:4 图文

这是一篇用来跑通渲染链路的示例文章。把 `--input` 指向任意 Markdown 文件即可生成同风格的图文页面。

## 支持的语法

- 空行分段；`-` 与 `1.` 列表；`>` 引用；`---` 分隔线
- **加粗**与 ==高亮==，高亮句通常放每节的核心判断
- 开头两行 `#` 作为封面主/副标题，`>` 导语显示在封面

## 三种风格

`--style` 支持 minimal、magazine、dark，配合 `--kicker`、`--author`、`--closing` 控制封面标签、署名与结尾页大字。

> 一篇长文的传播力，取决于结构，而不是字数。

---

跑一次试试：

    python3 render/render.py --input render/sample.md --out /tmp/out --style magazine
