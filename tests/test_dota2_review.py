import argparse
import os
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dota2_review import (  # noqa: E402
    AI_COACH_INSTRUCTIONS,
    AIReviewError,
    ability_name,
    build_team_gold_curve,
    build_match_artifact_stem,
    build_hero_training_report,
    build_parser,
    cleanup_old_downloads,
    delete_generated_tree,
    generate_report,
    hero_sample_metrics,
    find_latest_private_chat,
    fetch_high_rank_hero_matches,
    lead_change_minutes,
    load_ai_settings,
    load_serverchan_settings,
    load_zh_names,
    load_settings,
    make_chatgpt_bundle,
    notify_wechat_primary_with_telegram_fallback,
    open_chatgpt_handoff,
    parsed_sections,
    player_match_won,
    public_match_contains_hero,
    resolve_steam_account_id,
    purge_generated_data,
    request_ai_review,
    execute_daily_single_review,
    resolve_patch_label,
    validate_ai_review_contract,
    version_calibration_lines,
    run_daily_review,
    save_account_id,
    save_ai_settings,
    save_serverchan_settings,
    save_project_url,
    save_telegram_settings,
    select_daily_representatives,
    serverchan_send,
    telegram_api_request,
    telegram_match_caption,
    validate_chatgpt_project_url,
    validate_match_id,
    validate_telegram_bot_token,
    wait_for_parse,
)


HEROES = {
    "104": {"id": 104, "name": "npc_dota_hero_legion_commander", "localized_name": "Legion Commander", "roles": ["Initiator", "Durable", "Disabler"]},
    "8": {"id": 8, "name": "npc_dota_hero_juggernaut", "localized_name": "Juggernaut", "roles": ["Carry", "Pusher", "Escape"]},
}

ITEMS = {
    "blink": {"id": 1, "dname": "Blink Dagger", "cost": 2250},
    "blade_mail": {"id": 2, "dname": "Blade Mail", "cost": 2100},
    "tango": {"id": 3, "dname": "Tango", "cost": 90},
}


def make_player(slot, hero_id, gold, *, death_time=None):
    death_log = [] if death_time is None else [{"time": death_time, "killername": "npc_dota_hero_juggernaut"}]
    return {
        "player_slot": slot,
        "account_id": 1000 + slot,
        "personaname": f"P{slot}",
        "hero_id": hero_id,
        "kills": 3,
        "deaths": len(death_log),
        "assists": 5,
        "gold_per_min": 500,
        "xp_per_min": 600,
        "last_hits": 150,
        "denies": 10,
        "net_worth": 15000,
        "gold_t": gold,
        "purchase_log": [
            {"time": 10, "key": "tango"},
            {"time": 600, "key": "blink"},
            {"time": 900, "key": "blade_mail"},
        ],
        "deaths_log": death_log,
        "item_0": 1,
        "item_1": 2,
    }


class ReviewTests(unittest.TestCase):
    def test_public_match_hero_filter_checks_both_teams(self):
        match = {"radiant_team": [1, 2, 3, 4, 5], "dire_team": [6, 7, 8, 9, 10]}
        self.assertTrue(public_match_contains_hero(match, 3))
        self.assertTrue(public_match_contains_hero(match, 9))
        self.assertFalse(public_match_contains_hero(match, 42))

    @patch("dota2_review.request_json")
    def test_high_rank_hero_matches_filter_and_paginate(self, request):
        request.side_effect = [
            [
                {"match_id": 200, "radiant_team": [1, 2], "dire_team": [3, 4]},
                {"match_id": 190, "radiant_team": [8, 2], "dire_team": [3, 4]},
            ],
            [
                {"match_id": 180, "radiant_team": [1, 2], "dire_team": [8, 4]},
            ],
        ]
        rows = fetch_high_rank_hero_matches(8, 2)
        self.assertEqual([row["match_id"] for row in rows], [190, 180])
        self.assertIn("min_rank=80", request.call_args_list[0].args[0])
        self.assertIn("less_than_match_id=190", request.call_args_list[1].args[0])

    def test_hero_sample_metrics_and_report(self):
        samples = [
            {
                "summary": {"match_id": 9000000001},
                "match": {
                    "match_id": 9000000001,
                    "radiant_win": True,
                    "start_time": 1700000000,
                    "duration": 2400,
                },
                "player": {
                    "player_slot": 0,
                    "kills": 8,
                    "deaths": 2,
                    "assists": 10,
                    "gold_per_min": 650,
                    "xp_per_min": 700,
                    "last_hits": 280,
                    "hero_damage": 24000,
                    "tower_damage": 5000,
                },
            }
        ]
        metrics = hero_sample_metrics(samples)
        self.assertEqual(metrics["win_rate"], 100.0)
        self.assertEqual(metrics["gold_per_min"], 650.0)
        report = build_hero_training_report(8, "主宰", samples, samples, "pro")
        self.assertIn("主宰 · 英雄专项复盘", report)
        self.assertIn("职业比赛", report)
        self.assertIn("优先检查的差距", report)
        self.assertIn("每局阵容与选人上下文", report)
        self.assertIn("训练模式/自定义房机械练习", report)
        self.assertIn("一张三局记录表", report)

    def setUp(self):
        radiant_gold = [600, 1000, 1600, 2200, 2800, 3400] + [3400] * 25
        dire_gold = [600, 1100, 1700, 2100, 2700, 3200] + [3200] * 25
        self.match = {
            "match_id": 8943397976,
            "version": 21,
            "radiant_win": True,
            "radiant_score": 30,
            "dire_score": 20,
            "duration": 1800,
            "start_time": 1700000000,
            "game_mode": 22,
            "lobby_type": 7,
            "players": [
                *[make_player(i, 104, radiant_gold, death_time=300 if i == 0 else None) for i in range(5)],
                *[make_player(128 + i, 8, dire_gold, death_time=600 if i == 0 else None) for i in range(5)],
            ],
        }

    def test_match_id_validation(self):
        self.assertEqual(validate_match_id("8943397976"), 8943397976)
        with self.assertRaises(ValueError):
            validate_match_id("abc")

    def test_steam_identifier_conversion(self):
        self.assertEqual(resolve_steam_account_id("123456789"), 123456789)
        self.assertEqual(
            resolve_steam_account_id("76561198083722517"), 123456789
        )
        self.assertEqual(
            resolve_steam_account_id(
                "https://steamcommunity.com/profiles/76561198083722517/"
            ),
            123456789,
        )
        with self.assertRaises(ValueError):
            resolve_steam_account_id("https://steamcommunity.com/id/custom-name")

    def test_recent_match_result(self):
        self.assertTrue(player_match_won({"player_slot": 0, "radiant_win": True}))
        self.assertTrue(player_match_won({"player_slot": 128, "radiant_win": False}))
        self.assertFalse(player_match_won({"player_slot": 128, "radiant_win": True}))

    def test_auto_parse_is_enabled_by_default(self):
        parser = build_parser()
        defaults = parser.parse_args(["8943397976"])
        self.assertTrue(defaults.request_parse)
        self.assertEqual(defaults.parse_timeout, 60)
        self.assertFalse(
            parser.parse_args(["8943397976", "--no-request-parse"]).request_parse
        )

    def test_chatgpt_project_url_validation(self):
        url = "https://chatgpt.com/g/g-p-example/project"
        self.assertEqual(validate_chatgpt_project_url(url), url)
        with self.assertRaises(ValueError):
            validate_chatgpt_project_url("https://example.com/project")

    def test_telegram_token_validation_and_private_chat_discovery(self):
        token = "123456789:abcdefghijklmnopqrstuvwxyz_ABCDE"
        self.assertEqual(validate_telegram_bot_token(token), token)
        with self.assertRaises(ValueError):
            validate_telegram_bot_token("not-a-token")
        latest = find_latest_private_chat(
            [
                {
                    "update_id": 1,
                    "message": {
                        "chat": {
                            "id": 987654321,
                            "type": "private",
                            "first_name": "Test",
                        }
                    },
                }
            ]
        )
        self.assertEqual(latest, (987654321, "Test"))

    def test_telegram_settings_are_saved_separately(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            telegram_path = Path(temp_dir) / "telegram_settings.json"
            save_telegram_settings(
                telegram_path,
                bot_token="123456789:abcdefghijklmnopqrstuvwxyz_ABCDE",
                chat_id=987654321,
                bot_username="review_bot",
            )
            data = load_settings(telegram_path)
            self.assertEqual(data["chat_id"], 987654321)
            self.assertEqual(data["bot_username"], "review_bot")
            if sys.platform != "win32":
                self.assertEqual(telegram_path.stat().st_mode & 0o777, 0o600)

    def test_telegram_match_caption_uses_chinese_hero_name(self):
        caption = telegram_match_caption(
            date(2026, 8, 14),
            "表现最好",
            {
                "match_id": 8945194047,
                "hero_id": 75,
                "kills": 1,
                "deaths": 5,
                "assists": 28,
            },
            {75: "沉默术士"},
        )
        self.assertIn("沉默术士", caption)
        self.assertIn("KDA 1/5/28", caption)
        self.assertIn("Match 8945194047", caption)

    def test_openwrt_without_webbrowser_module_is_supported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_path = Path(temp_dir) / "bundle.md"
            bundle_path.write_text("test", encoding="utf-8")
            with patch("dota2_review.webbrowser", None):
                open_chatgpt_handoff(bundle_path, "https://chatgpt.com/g/example")

    def test_cleanup_removes_only_old_heavy_data_and_preserves_summaries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            script_dir = Path(temp_dir)
            match_dir = script_dir / "reports" / "old-match"
            daily_dir = script_dir / "reports" / "daily" / "2026-01-01"
            log_dir = script_dir / "daily_logs"
            match_dir.mkdir(parents=True)
            daily_dir.mkdir(parents=True)
            log_dir.mkdir(parents=True)

            old_raw = match_dir / "old_OpenDota原始数据.json"
            old_bundle = match_dir / "old_GPT复盘包.md"
            old_daily_bundle = daily_dir / "daily_2026-01-01_chatgpt_bundle.md"
            old_log = log_dir / "openwrt-2026-01-01.log"
            old_summary = match_dir / "old_复盘摘要.md"
            new_raw = match_dir / "new_OpenDota原始数据.json"
            for path in (
                old_raw,
                old_bundle,
                old_daily_bundle,
                old_log,
                old_summary,
                new_raw,
            ):
                path.write_text("x" * 100, encoding="utf-8")

            old_timestamp = time.time() - 40 * 24 * 60 * 60
            for path in (old_raw, old_bundle, old_daily_bundle, old_log, old_summary):
                os.utime(path, (old_timestamp, old_timestamp))

            result = cleanup_old_downloads(script_dir, retention_days=30)
            self.assertEqual(result["removed_files"], 4)
            for path in (old_raw, old_bundle, old_daily_bundle, old_log):
                self.assertFalse(path.exists())
            self.assertTrue(old_summary.exists())
            self.assertTrue(new_raw.exists())

    def test_cleanup_dry_run_does_not_delete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            script_dir = Path(temp_dir)
            reports = script_dir / "reports"
            reports.mkdir()
            old_raw = reports / "old_OpenDota原始数据.json"
            old_raw.write_text("old", encoding="utf-8")
            old_timestamp = time.time() - 40 * 24 * 60 * 60
            os.utime(old_raw, (old_timestamp, old_timestamp))
            result = cleanup_old_downloads(
                script_dir, retention_days=30, dry_run=True
            )
            self.assertEqual(result["removed_files"], 1)
            self.assertTrue(old_raw.exists())

    @patch("dota2_review.urlopen")
    def test_telegram_document_uses_official_multipart_api(self, urlopen_mock):
        response = urlopen_mock.return_value.__enter__.return_value
        response.read.return_value = b'{"ok":true,"result":{"message_id":1}}'
        with tempfile.TemporaryDirectory() as temp_dir:
            document_path = Path(temp_dir) / "复盘摘要.md"
            document_path.write_text("review-body", encoding="utf-8")
            telegram_api_request(
                "123456789:abcdefghijklmnopqrstuvwxyz_ABCDE",
                "sendDocument",
                fields={"chat_id": 987654321, "caption": "测试"},
                document_path=document_path,
            )
        request = urlopen_mock.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/sendDocument"))
        self.assertIn(b'name="document"', request.data)
        self.assertIn('filename="复盘摘要.md"'.encode("utf-8"), request.data)
        self.assertNotIn(b'filename="review.md"', request.data)
        self.assertIn(b"review-body", request.data)

    def test_parse_requires_gold_and_purchase_data_for_all_ten_players(self):
        match = dict(self.match)
        match["players"] = [dict(player) for player in self.match["players"]]
        match["players"][9]["gold_t"] = None
        parsed, missing = parsed_sections(match)
        self.assertFalse(parsed)
        self.assertIn("经济曲线", missing)

    def test_non_hero_deaths_do_not_make_parsed_match_incomplete(self):
        match = dict(self.match)
        players = [dict(player) for player in self.match["players"]]
        for index, player in enumerate(players):
            player.pop("deaths_log", None)
            player["kills_log"] = [
                {"time": 60 + row, "key": "npc_dota_hero_juggernaut"}
                for row in range(8)
            ]
            player["deaths"] = 9 if index < 5 else 8
        match["players"] = players
        parsed, missing = parsed_sections(match)
        self.assertTrue(parsed)
        self.assertNotIn("死亡时间线", missing)

    def test_delete_generated_tree_refuses_to_delete_allowed_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "reports" / "daily"
            target = root / "2026-08-14"
            target.mkdir(parents=True)
            (target / "data.md").write_text("data", encoding="utf-8")
            removed, reclaimed = delete_generated_tree(target, root)
            self.assertEqual(removed, 1)
            self.assertGreater(reclaimed, 0)
            self.assertFalse(target.exists())
            with self.assertRaises(OSError):
                delete_generated_tree(root, root)

    def test_purge_generated_data_preserves_settings_and_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            script_dir = Path(temp_dir)
            for folder in ("reports", "daily_logs", ".cache"):
                path = script_dir / folder
                path.mkdir()
                (path / "generated.txt").write_text("x", encoding="utf-8")
            for name in ("settings.json", "telegram_settings.json", "daily_state.json"):
                (script_dir / name).write_text("{}", encoding="utf-8")
            result = purge_generated_data(script_dir)
            self.assertEqual(result["removed_files"], 3)
            for folder in ("reports", "daily_logs", ".cache"):
                self.assertFalse((script_dir / folder).exists())
            for name in ("settings.json", "telegram_settings.json", "daily_state.json"):
                self.assertTrue((script_dir / name).exists())

    def test_settings_updates_preserve_other_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            save_account_id(settings_path, 123456789)
            save_project_url(
                settings_path, "https://chatgpt.com/g/g-p-example/project"
            )
            settings = load_settings(settings_path)
            self.assertEqual(settings["account_id"], 123456789)
            self.assertIn("chatgpt_project_url", settings)

    def test_ai_settings_are_saved_separately_and_loaded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "ai_settings.json"
            save_ai_settings(
                settings_path,
                {
                    "provider": "openai",
                    "api_key": "sk-test-secret-key",
                    "model": "gpt-test",
                    "reasoning_effort": "medium",
                },
            )
            config = load_ai_settings(settings_path)
            self.assertIsNotNone(config)
            self.assertEqual(config["provider"], "openai")
            self.assertEqual(config["model"], "gpt-test")

    def test_serverchan_settings_are_saved_separately(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "serverchan_settings.json"
            save_serverchan_settings(settings_path, "SCT1234567890abcdef")
            config = load_serverchan_settings(settings_path)
            self.assertIsNotNone(config)
            self.assertEqual(config["sendkey"], "SCT1234567890abcdef")

    @patch("dota2_review.urlopen")
    def test_serverchan_sends_markdown_without_logging_key(self, urlopen_mock):
        response = urlopen_mock.return_value.__enter__.return_value
        response.read.return_value = b'{"code":0,"message":"success"}'
        serverchan_send(
            {"sendkey": "SCT1234567890abcdef"},
            "Dota 2 测试",
            "# AI 最终复盘\n\n正文",
        )
        request = urlopen_mock.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/SCT1234567890abcdef.send"))
        self.assertIn(b"desp=", request.data)

    @patch("dota2_review.notify_telegram_if_configured")
    @patch("dota2_review.notify_serverchan_if_configured", return_value=True)
    def test_wechat_success_does_not_duplicate_to_telegram(
        self, wechat_mock, telegram_mock
    ):
        sent = notify_wechat_primary_with_telegram_fallback(
            serverchan_settings_path=Path("serverchan.json"),
            telegram_settings_path=Path("telegram.json"),
            wechat_enabled=True,
            telegram_enabled=True,
            wechat_title="复盘",
            wechat_content="正文",
            telegram_text="备用正文",
        )
        self.assertTrue(sent)
        wechat_mock.assert_called_once()
        telegram_mock.assert_not_called()

    @patch("dota2_review.notify_telegram_if_configured", return_value=True)
    @patch("dota2_review.notify_serverchan_if_configured", return_value=False)
    def test_wechat_failure_falls_back_to_telegram(
        self, wechat_mock, telegram_mock
    ):
        sent = notify_wechat_primary_with_telegram_fallback(
            serverchan_settings_path=Path("serverchan.json"),
            telegram_settings_path=Path("telegram.json"),
            wechat_enabled=True,
            telegram_enabled=True,
            wechat_title="复盘",
            wechat_content="正文",
            telegram_text="备用正文",
        )
        self.assertTrue(sent)
        wechat_mock.assert_called_once()
        telegram_mock.assert_called_once()

    @patch("dota2_review._request_ai_json")
    def test_openai_review_uses_responses_api_shape(self, request_mock):
        request_mock.return_value = {
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {
                                "title": "Dota 2 官方更新",
                                "url": "https://www.dota2.com/patches/7.41",
                            }
                        ]
                    },
                },
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "复盘完成"}],
                }
            ]
        }
        result = request_ai_review(
            {
                "provider": "openai",
                "api_key": "sk-test-secret-key",
                "model": "gpt-test",
                "reasoning_effort": "medium",
            },
            "比赛摘要",
        )
        self.assertEqual(result, "复盘完成")
        url, payload, _key = request_mock.call_args.args
        self.assertEqual(url, "https://api.openai.com/v1/responses")
        self.assertEqual(payload["input"], "比赛摘要")
        self.assertFalse(payload["store"])
        self.assertEqual(payload["tools"][0]["type"], "web_search")
        self.assertEqual(
            payload["tools"][0]["filters"]["allowed_domains"],
            ["dota2.com", "opendota.com"],
        )
        self.assertEqual(payload["include"], ["web_search_call.action.sources"])
        self.assertEqual(payload["tool_choice"], "required")

    @patch("dota2_review._request_ai_json")
    def test_openai_review_rejects_missing_web_sources(self, request_mock):
        request_mock.return_value = {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "复盘正文"}],
                }
            ]
        }
        with self.assertRaises(AIReviewError):
            request_ai_review(
                {
                    "provider": "openai",
                    "api_key": "sk-test-secret-key",
                    "model": "gpt-test",
                    "reasoning_effort": "medium",
                },
                "比赛摘要",
            )

    @patch("dota2_review.run", return_value=0)
    def test_frozen_daily_match_runs_in_process_without_gui_argparse(self, run_mock):
        with patch("dota2_review.sys.frozen", True, create=True):
            result = execute_daily_single_review(["123", "--no-ai-review"])
        self.assertEqual(result, 0)
        run_mock.assert_called_once_with(["123", "--no-ai-review"])

    @patch("dota2_review._request_ai_json")
    def test_deepseek_review_uses_chat_completions_shape(self, request_mock):
        request_mock.return_value = {
            "choices": [{"message": {"content": "深度复盘完成"}}]
        }
        result = request_ai_review(
            {
                "provider": "deepseek",
                "api_key": "sk-test-secret-key",
                "model": "deepseek-v4-pro",
                "reasoning_effort": "high",
            },
            "比赛摘要",
        )
        self.assertEqual(result, "深度复盘完成")
        url, payload, _key = request_mock.call_args.args
        self.assertEqual(url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(payload["messages"][1]["content"], "比赛摘要")
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertNotIn("tools", payload)
        self.assertIn("没有原生网页搜索工具", payload["messages"][0]["content"])

    def test_ai_review_contract_requires_ten_sections_in_order(self):
        text = "\n\n".join(
            f"## {number}. {title}\n内容"
            for number, title in enumerate(
                (
                    "一句话结论", "责任归因表", "证据摘要", "关键时间窗",
                    "用户个人评价", "队友评价", "地图与团战建议", "下一阶段训练",
                    "量化验收", "下次所需材料",
                ),
                start=1,
            )
        )
        validate_ai_review_contract(text)
        with self.assertRaises(AIReviewError):
            validate_ai_review_contract(text.replace("## 6. 队友评价\n内容\n\n", ""))

    def test_patch_calibration_uses_patch_field_not_parse_version(self):
        patches = [{"id": 60, "name": "7.41"}]
        match = {"patch": 60, "version": 999}
        self.assertEqual(resolve_patch_label(patches, match["patch"]), "7.41")
        lines = "\n".join(version_calibration_lines(match, patches))
        self.assertIn("7.41", lines)
        self.assertNotIn("999", lines)

    @patch("dota2_review._request_ai_json")
    def test_deepseek_max_never_downgrades_when_final_content_is_empty(
        self, request_mock
    ):
        request_mock.return_value = {
            "choices": [
                {"message": {"reasoning_content": "很长的推理", "content": ""}}
            ]
        }
        with self.assertRaises(AIReviewError):
            request_ai_review(
                {
                    "provider": "deepseek",
                    "api_key": "sk-test-secret-key",
                    "model": "deepseek-v4-pro",
                    "reasoning_effort": "max",
                },
                "比赛摘要",
            )
        self.assertEqual(request_mock.call_count, 1)
        payload = request_mock.call_args.args[1]
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "max")
        self.assertEqual(payload["max_tokens"], 64000)

    def test_bundled_chinese_names_cover_items_and_skills(self):
        names = load_zh_names(Path(__file__).resolve().parents[1])
        self.assertEqual(names["items"]["solar_crest"], "炎阳纹章")
        self.assertEqual(
            ability_name("sven_storm_bolt", names["abilities"]), "风暴之拳"
        )

    def test_set_steam_saves_identifier_without_querying_matches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_script = Path(temp_dir) / "dota2_review.py"
            with patch("dota2_review.__file__", str(fake_script)):
                from dota2_review import run

                result = run(["--set-steam", "123456789"])
            self.assertEqual(result, 0)
            self.assertEqual(
                load_settings(Path(temp_dir) / "settings.json")["account_id"],
                123456789,
            )

    def test_curve_and_lead_changes(self):
        curve = build_team_gold_curve(self.match)
        self.assertEqual(curve[0][3], 0)
        self.assertEqual(curve[1][3], -500)
        self.assertEqual(curve[-1][3], 1000)
        self.assertEqual(lead_change_minutes(curve), [3])

    def test_report_contains_required_sections(self):
        report, missing = generate_report(self.match, HEROES, ITEMS)
        self.assertEqual(missing, [])
        for heading in (
            "在线版本校准",
            "比赛概况",
            "天辉阵容与数据",
            "夜魇阵容与数据",
            "经济曲线关键点",
            "出装与购买时间",
            "死亡时间线",
        ):
            self.assertIn(heading, report)
        self.assertIn("军团指挥官", report)
        self.assertNotIn("Legion Commander", report)
        self.assertIn("Blink Dagger", report)
        self.assertNotIn("10: Tango", report)

    def test_report_highlights_my_performance(self):
        report, _ = generate_report(
            self.match,
            HEROES,
            ITEMS,
            focus_account_id=1000,
            focus_player_slot=0,
        )
        self.assertIn("## 我的表现", report)
        self.assertIn("👉 P0", report)
        self.assertIn("军团指挥官", report)
        self.assertIn("## 双方阵容、选择顺序与选人分析输入", report)
        self.assertIn("未提供可靠的 `picks_bans` 顺序", report)
        self.assertIn("选人评分任务", report)

    def test_report_uses_real_draft_order_and_visible_enemy_picks(self):
        self.match["picks_bans"] = [
            {"order": 0, "team": 0, "is_pick": False, "hero_id": 8},
            {"order": 1, "team": 1, "is_pick": True, "hero_id": 8},
            {"order": 2, "team": 0, "is_pick": True, "hero_id": 104},
        ]
        report, _ = generate_report(
            self.match,
            HEROES,
            ITEMS,
            focus_account_id=1000,
            focus_player_slot=0,
        )
        self.assertIn("OpenDota 记录的选择/禁用顺序", report)
        self.assertIn("全场第 2 个选择、己方第 1 个选择", report)
        self.assertIn("此前已出现的敌人**：主宰", report)
        self.assertIn("天辉功能标签", report)

    def test_report_adds_role_farming_and_teamfight_coaching_evidence(self):
        focus = self.match["players"][0]
        focus.update(
            {
                "lane_role": 2,
                "lane_efficiency_pct": 78.5,
                "lh_t": list(range(31)),
                "teamfight_participation": 0.72,
                "hero_damage": 18000,
                "tower_damage": 4200,
                "life_state_dead": 180,
                "ability_uses": {"legion_commander_duel": 8},
                "item_uses": {"blink": 5, "tpscroll": 3},
                "benchmarks": {
                    "gold_per_min": {"pct": 0.61},
                    "last_hits_per_min": {"pct": 0.57},
                    "hero_damage_per_min": {"pct": 0.66},
                },
            }
        )
        for index, player in enumerate(self.match["players"]):
            player.setdefault("hero_damage", 5000 + index)
            player.setdefault("tower_damage", 1000 + index)
            player.setdefault("lh_t", list(range(31)))
        self.match["teamfights"] = [
            {
                "start": 900,
                "end": 930,
                "players": [
                    {
                        "ability_uses": {"legion_commander_duel": 1},
                        "item_uses": {"blink": 1},
                        "killed": {"npc_dota_hero_juggernaut": 1},
                        "deaths": 0,
                        "damage": 2500,
                        "gold_delta": 600,
                    },
                    *[{} for _ in range(9)],
                ],
            }
        ]
        report, _ = generate_report(
            self.match,
            HEROES,
            ITEMS,
            zh_names={
                "items": {"blink": "闪烁匕首", "blade_mail": "刃甲", "tango": "树之祭祀"},
                "abilities": {"legion_commander_duel": "决斗"},
            },
            focus_account_id=1000,
            focus_player_slot=0,
        )
        self.assertIn("## 英雄定位与胜利责任证据（Max）", report)
        self.assertIn("核心位倾向（C 位检查表适用）", report)
        self.assertIn("10:00 10 补刀", report)
        self.assertIn("最高效窗口", report)
        self.assertIn("## 团战切入与技能释放证据（Max）", report)
        self.assertIn("技能：决斗 ×1", report)
        self.assertIn("道具：闪烁匕首 ×1", report)

    def test_coach_prompt_requires_more_than_death_analysis(self):
        self.assertIn("不能只对被击杀作出反应", AI_COACH_INSTRUCTIONS)
        self.assertIn("团战切入", AI_COACH_INSTRUCTIONS)
        self.assertIn("目标转化", AI_COACH_INSTRUCTIONS)
        self.assertIn("至少指出一项可复制的优点", AI_COACH_INSTRUCTIONS)
        self.assertIn("1–10 分的“选人评分”", AI_COACH_INSTRUCTIONS)
        self.assertIn("替代英雄", AI_COACH_INSTRUCTIONS)
        self.assertIn("训练模式/自定义房机械练习", AI_COACH_INSTRUCTIONS)

    def test_chatgpt_bundle_contains_prompt_report_and_raw_json(self):
        report, _ = generate_report(self.match, HEROES, ITEMS)
        bundle = make_chatgpt_bundle(
            self.match, report, focus_account_id=1000
        )
        self.assertIn("三条下一局能直接执行的改进建议", bundle)
        self.assertIn("## 完整 OpenDota JSON", bundle)
        self.assertIn('"match_id": 8943397976', bundle)
        self.assertIn("## 比赛概况", bundle)

    def test_match_artifact_stem_uses_date_hero_kda_and_match_id(self):
        stem = build_match_artifact_stem(
            self.match,
            HEROES,
            focus_account_id=1000,
            focus_player_slot=0,
        )
        self.assertIn("_军团指挥官_3-1-5_Match_8943397976", stem)
        self.assertNotIn("/", stem)

    @patch("dota2_review.request_json")
    @patch("dota2_review.time.sleep")
    @patch("dota2_review.time.monotonic", side_effect=[0, 0, 0, 0])
    def test_wait_for_parse_returns_completed_match(
        self, _monotonic, _sleep, request_mock
    ):
        request_mock.return_value = self.match
        refreshed, completed = wait_for_parse(
            8943397976, timeout_seconds=10, poll_interval_seconds=1
        )
        self.assertTrue(completed)
        self.assertEqual(refreshed["match_id"], 8943397976)

    @patch("dota2_review.notify_telegram_if_configured")
    @patch("dota2_review.subprocess.run")
    @patch("dota2_review.fetch_matches_for_local_date", return_value=[])
    def test_daily_mode_with_no_matches_never_reuses_old_match(
        self, _fetch_mock, subprocess_mock, telegram_mock
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            script_dir = Path(temp_dir)
            settings_path = script_dir / "settings.json"
            save_account_id(settings_path, 123456789)
            save_telegram_settings(
                script_dir / "telegram_settings.json",
                bot_token="123456789:abcdefghijklmnopqrstuvwxyz_ABCDE",
                chat_id=987654321,
            )
            args = argparse.Namespace(
                day_offset=1,
                parse_timeout=1,
                request_parse=True,
                all_purchases=False,
                no_open_project=True,
            )
            result = run_daily_review(
                args, script_dir=script_dir, settings_path=settings_path
            )
            self.assertEqual(result, 0)
            subprocess_mock.assert_not_called()
            self.assertFalse((script_dir / "reports").exists())
            telegram_mock.assert_called_once()
            self.assertIn("没有查询到", telegram_mock.call_args.args[1])

    def test_daily_selection_uses_detailed_performance_and_distinct_matches(self):
        good = {
            "match_id": 1,
            "player_slot": 0,
            "radiant_win": True,
            "duration": 2400,
            "kills": 12,
            "deaths": 2,
            "assists": 15,
            "gold_per_min": 750,
            "xp_per_min": 800,
            "hero_damage": 35000,
            "tower_damage": 9000,
        }
        bad = {
            "match_id": 2,
            "player_slot": 128,
            "radiant_win": True,
            "duration": 2400,
            "kills": 1,
            "deaths": 12,
            "assists": 3,
            "gold_per_min": 380,
            "xp_per_min": 420,
            "hero_damage": 9000,
            "tower_damage": 0,
        }
        selected = select_daily_representatives([bad, good])
        self.assertEqual(selected[0][1]["match_id"], 1)
        self.assertEqual(selected[1][1]["match_id"], 2)

    def test_missing_parse_data_is_reported(self):
        match = dict(self.match)
        match["version"] = None
        match["players"] = [dict(player, gold_t=None, purchase_log=None, deaths_log=None) for player in self.match["players"]]
        parsed, missing = parsed_sections(match)
        self.assertFalse(parsed)
        self.assertIn("经济曲线", missing)
        self.assertIn("购买时间线", missing)
        self.assertIn("死亡时间线", missing)


if __name__ == "__main__":
    unittest.main()
