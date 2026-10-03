"""Tests for deterministic email body normalization."""

from __future__ import annotations

import unittest

from app.mail.body_normalizer import normalize_email_body


class NormalizeEmailBodyTests(unittest.TestCase):
    def test_plain_text_is_preserved(self) -> None:
        text = "Hello team,\n\nThe release is ready.\nRegards,\nAlice"
        self.assertEqual(normalize_email_body(text), text)

    def test_japanese_mid_sentence_line_break_is_joined(self) -> None:
        text = "このメールは重要な\nお知らせです。\n\n次の段落です。"
        expected = "このメールは重要なお知らせです。\n\n次の段落です。"
        self.assertEqual(normalize_email_body(text), expected)

    def test_long_tracking_url_is_removed_but_link_text_remains(self) -> None:
        tracking_url = "https://click.unsplash.com/click?token=" + "x" * 300
        text = f"Cast your vote ({tracking_url})"
        self.assertEqual(normalize_email_body(text), "Cast your vote")

    def test_non_breaking_spaces_become_regular_spaces(self) -> None:
        self.assertEqual(normalize_email_body("Hello\u00a0world"), "Hello world")

    def test_zero_width_characters_are_removed(self) -> None:
        self.assertEqual(normalize_email_body("zero\u200bwidth\ufefftext"), "zerowidthtext")

    def test_excess_blank_lines_are_compressed(self) -> None:
        self.assertEqual(normalize_email_body("First\n\n\n\nSecond"), "First\n\nSecond")

    def test_consecutive_duplicate_link_is_kept_once(self) -> None:
        url = "https://example.com/jobs/123"
        self.assertEqual(normalize_email_body(f"{url}\n{url}\n{url}"), url)

    def test_important_normal_url_is_preserved(self) -> None:
        url = "https://company.example/careers/apply?job=123"
        self.assertIn(url, normalize_email_body(f"Apply here: {url}"))

    def test_empty_body_remains_empty(self) -> None:
        self.assertEqual(normalize_email_body(""), "")

    def test_meaningful_image_alt_text_is_preserved(self) -> None:
        text = "[image: Quarterly revenue chart]"
        self.assertEqual(normalize_email_body(text), text)


if __name__ == "__main__":
    unittest.main()
