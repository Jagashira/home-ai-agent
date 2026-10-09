from __future__ import annotations

import unittest

from app.mail.safe_text import REDACTION_MARKER, redact_sensitive_values


class SafeTextTests(unittest.TestCase):
    def assert_redacted(self, value: str, secret: str) -> None:
        rendered = redact_sensitive_values(value)
        self.assertNotIn(secret, rendered)
        self.assertIn(REDACTION_MARKER, rendered)

    def test_english_confirmation_code_after_label(self) -> None:
        self.assert_redacted(
            "Your X confirmation code is xehwxzpk",
            "xehwxzpk",
        )

    def test_english_verification_code(self) -> None:
        self.assert_redacted("Verification code: AB12CD34", "AB12CD34")

    def test_japanese_authentication_code(self) -> None:
        self.assert_redacted("認証コードは350295です", "350295")

    def test_japanese_confirmation_code(self) -> None:
        self.assert_redacted("確認コード: kakunin88", "kakunin88")

    def test_otp_and_one_time_password(self) -> None:
        for value, secret in (
            ("OTP: 482910", "482910"),
            ("one-time password is AbCdEf90", "AbCdEf90"),
            ("one-time passcode: 778899", "778899"),
        ):
            with self.subTest(value=value):
                self.assert_redacted(value, secret)

    def test_value_before_code_label_is_redacted(self) -> None:
        self.assert_redacted(
            "xehwxzpk is your confirmation code",
            "xehwxzpk",
        )

    def test_ordinary_numbers_are_preserved(self) -> None:
        value = "2026年10月9日、料金は12,345円、企業番号は123456です。"
        self.assertEqual(redact_sensitive_values(value), value)


if __name__ == "__main__":
    unittest.main()
