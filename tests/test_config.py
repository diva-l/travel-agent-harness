from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from travel_agent_harness.config import HarnessConfig


def _from_env(env: dict[str, str], **overrides: object) -> HarnessConfig:
    with patch.dict(os.environ, env, clear=True):
        return HarnessConfig.from_env(**overrides)


def _as_linux():
    """vllm-mode validation is platform-gated; these tests exercise the mode
    itself, so they run the validate() call as if on a Linux host."""
    return patch("travel_agent_harness.config.platform.system", return_value="Linux")


class PlannerModePresetTests(unittest.TestCase):
    def test_api_mode_defaults_stay_on_hosted_api(self) -> None:
        config = _from_env({"TRAVEL_HARNESS_API_KEY": "sk-test"})
        self.assertEqual("api", config.planner_mode)
        self.assertEqual("native", config.model_protocol)
        self.assertEqual("https://api.deepseek.com", config.base_url)
        self.assertEqual(0.1, config.model_temperature)
        self.assertEqual(1.0, config.model_top_p)
        self.assertEqual(0, config.model_top_k)
        self.assertEqual(1800, config.model_max_output_tokens)
        # report stage inherits the planner credential in api mode
        self.assertEqual("sk-test", config.report_api_key)
        config.validate()

    def test_vllm_mode_fills_local_server_defaults(self) -> None:
        config = _from_env(
            {
                "TRAVEL_HARNESS_PLANNER_MODE": "vllm",
                "TRAVEL_HARNESS_REPORT_API_KEY": "sk-report",
            }
        )
        self.assertEqual("vllm", config.planner_mode)
        self.assertEqual("tagged", config.model_protocol)
        self.assertEqual("http://127.0.0.1:8000/v1", config.base_url)
        self.assertEqual("travel-planner", config.model)
        self.assertEqual("vllm-local", config.api_key)
        self.assertEqual(0.2, config.model_temperature)
        self.assertEqual(0.95, config.model_top_p)
        self.assertEqual(50, config.model_top_k)
        self.assertEqual(5000, config.model_max_output_tokens)
        self.assertEqual(600.0, config.max_seconds)
        with _as_linux():
            config.validate()

    def test_vllm_mode_report_stage_defaults_to_hosted_api(self) -> None:
        config = _from_env(
            {
                "TRAVEL_HARNESS_PLANNER_MODE": "vllm",
                "TRAVEL_HARNESS_REPORT_API_KEY": "sk-report",
            }
        )
        self.assertEqual("https://api.deepseek.com", config.report_base_url)
        self.assertEqual("deepseek-v4-flash", config.report_model)
        self.assertEqual("sk-report", config.report_api_key)

    def test_vllm_mode_never_inherits_planner_key_for_report(self) -> None:
        config = _from_env(
            {
                "TRAVEL_HARNESS_PLANNER_MODE": "vllm",
                "TRAVEL_HARNESS_API_KEY": "vllm-side-key",
            }
        )
        self.assertEqual("", config.report_api_key)
        with _as_linux():
            with self.assertRaises(ValueError):
                config.validate()

    def test_vllm_mode_requires_report_key_when_report_enabled(self) -> None:
        config = _from_env({"TRAVEL_HARNESS_PLANNER_MODE": "vllm"})
        with _as_linux():
            with self.assertRaises(ValueError):
                config.validate()

    def test_vllm_mode_allows_disabling_report(self) -> None:
        config = _from_env(
            {
                "TRAVEL_HARNESS_PLANNER_MODE": "vllm",
                "TRAVEL_HARNESS_REPORT_ENABLED": "false",
            }
        )
        with _as_linux():
            config.validate()

    def test_vllm_mode_rejected_on_windows(self) -> None:
        config = _from_env(
            {
                "TRAVEL_HARNESS_PLANNER_MODE": "vllm",
                "TRAVEL_HARNESS_REPORT_API_KEY": "sk-report",
            }
        )
        with patch("travel_agent_harness.config.platform.system", return_value="Windows"):
            with self.assertRaises(ValueError) as ctx:
                config.validate()
        self.assertIn("TRAVEL_HARNESS_PLANNER_MODE=api", str(ctx.exception))

    def test_explicit_env_vars_beat_preset(self) -> None:
        config = _from_env(
            {
                "TRAVEL_HARNESS_PLANNER_MODE": "vllm",
                "TRAVEL_HARNESS_BASE_URL": "http://10.0.0.8:9000/v1",
                "TRAVEL_HARNESS_MODEL": "my-checkpoint",
                "TRAVEL_HARNESS_MODEL_TEMPERATURE": "0.7",
                "TRAVEL_HARNESS_MAX_SECONDS": "900",
                "TRAVEL_HARNESS_REPORT_API_KEY": "sk-report",
            }
        )
        self.assertEqual("http://10.0.0.8:9000/v1", config.base_url)
        self.assertEqual("my-checkpoint", config.model)
        self.assertEqual(0.7, config.model_temperature)
        self.assertEqual(900.0, config.max_seconds)
        # untouched fields still come from the preset
        self.assertEqual("tagged", config.model_protocol)
        self.assertEqual(50, config.model_top_k)

    def test_planner_mode_override_uses_preset(self) -> None:
        config = _from_env(
            {"TRAVEL_HARNESS_REPORT_API_KEY": "sk-report"},
            planner_mode="vllm",
        )
        self.assertEqual("tagged", config.model_protocol)
        self.assertEqual("http://127.0.0.1:8000/v1", config.base_url)

    def test_unknown_planner_mode_rejected(self) -> None:
        config = _from_env(
            {
                "TRAVEL_HARNESS_PLANNER_MODE": "mystery",
                "TRAVEL_HARNESS_API_KEY": "sk-test",
            }
        )
        with self.assertRaises(ValueError):
            config.validate()

    def test_invalid_sampling_params_rejected(self) -> None:
        config = _from_env(
            {
                "TRAVEL_HARNESS_API_KEY": "sk-test",
                "TRAVEL_HARNESS_MODEL_TOP_P": "1.5",
            }
        )
        with self.assertRaises(ValueError):
            config.validate()

    def test_firecrawl_search_provider_requires_key(self) -> None:
        config = _from_env(
            {
                "TRAVEL_HARNESS_API_KEY": "sk-test",
                "TRAVEL_HARNESS_SEARCH_PROVIDER": "firecrawl",
            }
        )
        with self.assertRaises(ValueError):
            config.validate()

    def test_firecrawl_search_provider_from_env(self) -> None:
        config = _from_env(
            {
                "TRAVEL_HARNESS_API_KEY": "sk-test",
                "TRAVEL_HARNESS_SEARCH_PROVIDER": "firecrawl",
                "TRAVEL_HARNESS_FIRECRAWL_KEY": "fc-test",
            }
        )
        self.assertEqual("firecrawl", config.search_provider)
        self.assertEqual("fc-test", config.firecrawl_api_key)
        config.validate()

    def test_unknown_search_provider_rejected(self) -> None:
        config = _from_env(
            {
                "TRAVEL_HARNESS_API_KEY": "sk-test",
                "TRAVEL_HARNESS_SEARCH_PROVIDER": "mystery",
            }
        )
        with self.assertRaises(ValueError):
            config.validate()


if __name__ == "__main__":
    unittest.main()
