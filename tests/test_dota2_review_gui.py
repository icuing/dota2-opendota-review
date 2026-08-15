import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dota2_review_gui import (  # noqa: E402
    build_hero_run_args,
    build_run_args,
    load_pc_settings,
    load_schedule_settings,
    masked_status,
    model_options,
    save_schedule_settings,
    save_pc_settings,
    validate_schedule_time,
)


class GuiTests(unittest.TestCase):
    def test_hero_training_arguments(self):
        args = build_hero_run_args(
            hero_id=8,
            history_count=10,
            compare_source="high_rank",
            benchmark_count=5,
            output_root="",
            enable_ai=False,
        )
        self.assertEqual(
            args,
            [
                "--hero-review", "8",
                "--history-count", "10",
                "--compare-source", "high_rank",
                "--benchmark-count", "5",
                "--no-open-project",
                "--no-ai-review",
            ],
        )

    def test_pc_settings_round_trip(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "pc_settings.json"
            output = Path(temp_dir) / "reports"
            save_pc_settings(path, output_root=str(output))
            self.assertEqual(load_pc_settings(path)["output_root"], str(output.resolve()))

    def test_single_match_arguments(self):
        self.assertEqual(
            build_run_args(
                match_id="8943397976",
                enable_ai=True,
                enable_push=False,
            ),
            ["8943397976", "--no-open-project"],
        )

    def test_daily_offline_arguments(self):
        self.assertEqual(
            build_run_args(
                daily=True,
                day_offset=2,
                enable_ai=False,
                enable_push=False,
            ),
            [
                "--daily",
                "--day-offset",
                "2",
                "--no-wechat",
                "--no-telegram",
                "--no-ai-review",
                "--no-open-project",
            ],
        )

    def test_invalid_match_id_is_rejected_before_worker_starts(self):
        with self.assertRaises(ValueError):
            build_run_args(match_id="not-a-match")

    def test_status_never_returns_a_secret(self):
        self.assertEqual(masked_status(True, "DeepSeek · max"), ("已连接", "DeepSeek · max"))
        self.assertEqual(masked_status(False, ""), ("待设置", "点击配置后即可使用"))

    def test_model_options_cover_both_providers(self):
        self.assertIn("gpt-5.6-terra", model_options("openai"))
        self.assertIn("deepseek-v4-pro", model_options("deepseek"))
        self.assertEqual(model_options("unknown"), ())

    def test_schedule_time_validation(self):
        self.assertEqual(validate_schedule_time("06:15"), "06:15")
        self.assertEqual(validate_schedule_time("23:59"), "23:59")
        with self.assertRaises(ValueError):
            validate_schedule_time("25:00")

    def test_schedule_settings_round_trip(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "schedule_settings.json"
            save_schedule_settings(path, enabled=True, run_time="07:35")
            self.assertEqual(
                load_schedule_settings(path),
                {"enabled": True, "time": "07:35"},
            )


if __name__ == "__main__":
    unittest.main()
