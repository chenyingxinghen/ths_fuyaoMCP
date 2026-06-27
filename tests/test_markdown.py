from __future__ import annotations

import unittest

from fuyao_agent.markdown import decode_markdown_output


class MarkdownOutputDecodeTests(unittest.TestCase):
    def test_decodes_json_wrapped_markdown(self) -> None:
        text = '"# 标题\\n\\n- \\u4e0a\\u8bc1\\u6307\\u6570"'

        self.assertEqual(decode_markdown_output(text), "# 标题\n\n- 上证指数")

    def test_decodes_literal_markdown_escapes(self) -> None:
        text = "\\# 摘要\\n\\n\\*\\*结论\\*\\*: 中性"

        self.assertEqual(decode_markdown_output(text), "# 摘要\n\n**结论**: 中性")

    def test_strips_single_markdown_fence(self) -> None:
        text = "```markdown\n## 结果\n\n数据已更新。\n```"

        self.assertEqual(decode_markdown_output(text), "## 结果\n\n数据已更新。")

    def test_decodes_html_entities(self) -> None:
        text = "A 股 &gt; 指数 &amp; 板块"

        self.assertEqual(decode_markdown_output(text), "A 股 > 指数 & 板块")

    def test_preserves_json_string_escapes_inside_code_fence(self) -> None:
        text = 'MEMORY_JSON:\n```json\n{"lesson": "第一行\\n第二行"}\n```'

        self.assertEqual(decode_markdown_output(text), text)


if __name__ == "__main__":
    unittest.main()
