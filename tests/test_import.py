# -*- coding: utf-8 -*-
"""单文件导入链路的单元测试（不碰浏览器、不碰账号）。

跑：python3 -m unittest discover -s tests -v
"""

import argparse
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "publish"))

import make  # noqa: E402
from textlen import platform_len, smart_truncate  # noqa: E402


FRONT = """---
title: 复利的前半段是平地
body: |
  正文第一段。

  正文第二段，带 emoji 😀。
tags: [复利思维, 长期主义]
kicker: "AI 与内容 · 004"
author: 七帅
platform: both
schedule: auto
---

# 封面主标题
> 封面导语

## 小标题

正文长文第一段。
"""


class FrontmatterTest(unittest.TestCase):
    def test_split_keeps_body(self):
        fm, body = make.split_frontmatter(FRONT)
        self.assertEqual(fm["title"], "复利的前半段是平地")
        self.assertEqual(fm["kicker"], "AI 与内容 · 004")
        self.assertEqual(fm["tags"], ["复利思维", "长期主义"])
        self.assertEqual(fm["platform"], "both")
        self.assertIn("正文第二段", fm["body"])
        self.assertTrue(body.lstrip().startswith("# 封面主标题"))
        self.assertNotIn("title:", body)

    def test_no_frontmatter_is_untouched(self):
        raw = "# 标题\n\n正文"
        fm, body = make.split_frontmatter(raw)
        self.assertEqual(fm, {})
        self.assertEqual(body, raw)

    def test_unclosed_frontmatter_is_untouched(self):
        raw = "---\ntitle: x\n\n正文"
        fm, body = make.split_frontmatter(raw)
        self.assertEqual(fm, {})
        self.assertEqual(body, raw)

    def test_tags_from_string(self):
        self.assertEqual(make.as_tags("#复利 #长期主义"), ["复利", "长期主义"])
        self.assertEqual(make.as_tags("复利, 长期主义"), ["复利", "长期主义"])
        self.assertEqual(make.as_tags([]), [])


class ScheduleTest(unittest.TestCase):
    def test_hhmm_rolls_to_tomorrow_when_past(self):
        got = make.resolve_schedule("00:00", {})
        self.assertEqual(got, (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d 00:00"))

    def test_explicit_datetime_passthrough(self):
        self.assertEqual(make.resolve_schedule("2099-01-01 10:00", {}),
                         "2099-01-01 10:00")

    def test_past_datetime_exits(self):
        with self.assertRaises(SystemExit):
            make.resolve_schedule("2000-01-01 10:00", {})

    def test_auto_uses_note_setting(self):
        got = make.resolve_schedule("auto", {"schedule_time": "23:59"})
        self.assertTrue(got.endswith("23:59"))
        self.assertIn(str(datetime.now().year), got)


class NumberingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old_notes, self.old_base = make.NOTES, make.BASE
        make.NOTES = self.tmp / "notes"
        make.NOTES.mkdir()
        make.BASE = self.tmp

    def tearDown(self):
        make.NOTES, make.BASE = self.old_notes, self.old_base

    def test_next_number_ignores_non_numeric_dirs(self):
        (make.NOTES / "001-旧稿").mkdir()
        (make.NOTES / "003-旧稿").mkdir()
        (make.NOTES / "模板").mkdir()
        self.assertEqual(make.next_number(), 4)

    def test_next_number_starts_at_one(self):
        self.assertEqual(make.next_number(), 1)

    def test_slugify(self):
        self.assertEqual(make.slugify("复利的前半段：是平地！"), "复利的前半段-是平地")
        self.assertEqual(len(make.slugify("一二三四五六七八九十十一十二十三")), 12)
        self.assertEqual(make.slugify("!!!"), "未命名")

    def test_import_md_creates_note_dir(self):
        src = self.tmp / "inbox"
        src.mkdir()
        f = src / "稿子.md"
        f.write_text(FRONT, encoding="utf-8")

        note_dir, fm = make.import_md(f, argparse.Namespace(title=None))
        self.assertEqual(note_dir.name, "001-复利的前半段是平地")
        cfg = json.loads((note_dir / "note.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["title"], "复利的前半段是平地")
        self.assertEqual(cfg["style"], "card")
        self.assertEqual(cfg["tags"], ["复利思维", "长期主义"])
        self.assertEqual(cfg["platform"], "both")
        self.assertIn("正文第二段", cfg["body"])
        long_md = (note_dir / "long.md").read_text(encoding="utf-8")
        self.assertTrue(long_md.startswith("# 封面主标题"))
        self.assertNotIn("---", long_md.split("\n")[0])


class TextLenTest(unittest.TestCase):
    def test_ascii_and_chinese(self):
        self.assertEqual(platform_len("abcd"), 4)
        self.assertEqual(platform_len("复利思维"), 4)

    def test_emoji_counts_two_units(self):
        self.assertEqual(platform_len("😀"), 2)

    def test_smart_truncate_drops_whole_paragraph(self):
        text = "A" * 30 + "\n" + "B" * 30
        self.assertEqual(smart_truncate(text, 40), "A" * 30 + "…")

    def test_smart_truncate_sentence_boundary(self):
        text = "复利的前半段是平地。" * 5
        out = smart_truncate(text, 20)
        self.assertLessEqual(platform_len(out), 20)
        self.assertTrue(out.endswith("。…"))

    def test_smart_truncate_never_splits_emoji(self):
        out = smart_truncate("😀" * 30, 11)
        self.assertLessEqual(platform_len(out), 11)
        self.assertNotIn("\ud83d", out.encode("unicode_escape").decode("ascii"))

    def test_short_text_untouched(self):
        self.assertEqual(smart_truncate("短文本", 100), "短文本")


if __name__ == "__main__":
    unittest.main()
