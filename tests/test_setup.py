from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from travel_agent_harness.setup_wizard import run_setup


def _run(tmp: str, inputs: list[str], system: str) -> tuple[Path, list[str]]:
    """Run the wizard with scripted answers; returns (env path, printed lines)."""
    answers = iter(inputs)
    printed: list[str] = []
    output = Path(tmp) / ".env"
    run_setup(
        output,
        input_fn=lambda _prompt: next(answers),
        print_fn=printed.append,
        system=system,
    )
    return output, printed


class SetupWizardTests(unittest.TestCase):
    def test_windows_offers_api_only_and_writes_env(self):
        with tempfile.TemporaryDirectory() as folder:
            output, printed = _run(
                folder,
                [
                    "sk-test-key",  # DeepSeek key (required)
                    "",  # base_url default
                    "",  # model default
                    "",  # tool provider -> demo
                    "",  # search provider -> demo
                    "",  # db path default
                    "",  # no api token
                ],
                system="Windows",
            )
            text = output.read_text(encoding="utf-8")
            self.assertIn("TRAVEL_HARNESS_PLANNER_MODE=api", text)
            self.assertIn("TRAVEL_HARNESS_API_KEY=sk-test-key", text)
            self.assertIn("TRAVEL_HARNESS_BASE_URL=https://api.deepseek.com", text)
            self.assertIn("TRAVEL_HARNESS_DB=./travel_harness.db", text)
            self.assertNotIn("vllm", text)
            transcript = "\n".join(printed)
            self.assertIn("Windows", transcript)
            self.assertIn("仅支持 api 模式", transcript)
            # no mode choice was presented on Windows
            self.assertNotIn("两种 Planner 模式", transcript)

    def test_linux_can_pick_vllm_and_collects_report_key(self):
        with tempfile.TemporaryDirectory() as folder:
            output, printed = _run(
                folder,
                [
                    "2",  # planner mode -> vllm
                    "sk-report-key",  # report key (required)
                    "amap",  # tool provider
                    "amap-key-1",  # amap key
                    "",  # search provider -> demo
                    "",  # db path default
                    "tok-123",  # api token
                ],
                system="Linux",
            )
            text = output.read_text(encoding="utf-8")
            self.assertIn("TRAVEL_HARNESS_PLANNER_MODE=vllm", text)
            self.assertIn("TRAVEL_HARNESS_REPORT_API_KEY=sk-report-key", text)
            self.assertIn("TRAVEL_HARNESS_TOOL_PROVIDER=amap", text)
            self.assertIn("TRAVEL_HARNESS_AMAP_KEY=amap-key-1", text)
            self.assertIn("TRAVEL_HARNESS_API_TOKEN=tok-123", text)
            self.assertNotIn("TRAVEL_HARNESS_API_KEY=", text)
            self.assertIn("两种 Planner 模式", "\n".join(printed))

    def test_existing_env_is_not_overwritten_without_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            env_path = Path(folder) / ".env"
            env_path.write_text("ORIGINAL=1\n", encoding="utf-8")
            _run(
                folder,
                [
                    "sk-test-key",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "n",  # decline overwrite
                ],
                system="Windows",
            )
            self.assertEqual("ORIGINAL=1\n", env_path.read_text(encoding="utf-8"))

    def test_linux_defaults_to_api_when_enter_pressed(self):
        with tempfile.TemporaryDirectory() as folder:
            output, _ = _run(
                folder,
                ["", "sk-test-key", "", "", "", "", "", ""],
                system="Linux",
            )
            text = output.read_text(encoding="utf-8")
            self.assertIn("TRAVEL_HARNESS_PLANNER_MODE=api", text)
            self.assertIn("TRAVEL_HARNESS_API_KEY=sk-test-key", text)


if __name__ == "__main__":
    unittest.main()
