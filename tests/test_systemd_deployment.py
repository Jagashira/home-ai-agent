from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SystemdDeploymentTests(unittest.TestCase):
    def test_service_enables_morning_digest_without_secrets(self) -> None:
        service = (PROJECT_ROOT / "systemd/home-ai-mail.service.template").read_text()
        self.assertIn("daily_run --hours 24 --notify-digest", service)
        for secret_name in ("HOME_ASSISTANT_TOKEN", "DEEPSEEK_API_KEY"):
            self.assertNotIn(secret_name, service)

    def test_timer_remains_daily_and_persistent(self) -> None:
        timer = (PROJECT_ROOT / "systemd/home-ai-mail.timer").read_text()
        self.assertIn("OnCalendar=*-*-* 08:00:00", timer)
        self.assertIn("Persistent=true", timer)

    def test_install_and_uninstall_scripts_have_valid_shell_syntax(self) -> None:
        for script in ("install_mail_systemd.sh", "uninstall_mail_systemd.sh"):
            result = subprocess.run(
                ["bash", "-n", str(PROJECT_ROOT / "scripts" / script)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
