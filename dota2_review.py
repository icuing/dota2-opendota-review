#!/usr/bin/env python3
"""Generate a Markdown Dota 2 match review from the public OpenDota API."""

from __future__ import annotations

import argparse
import getpass
import json
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

try:
    import webbrowser
except ImportError:  # OpenWrt's minimal Python build may omit this desktop-only module.
    webbrowser = None  # type: ignore[assignment]


API_BASE = "https://api.opendota.com/api"
TELEGRAM_API_BASE = "https://api.telegram.org"
APP_VERSION = "1.3.9"
USER_AGENT = f"dota2-match-review/{APP_VERSION}"
CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
STEAM_ID64_BASE = 76561197960265728
MAX_ACCOUNT_ID = 2**32 - 1
PARSE_POLL_INTERVAL_SECONDS = 10 * 60
PARSE_WAIT_TIMEOUT_SECONDS = 60 * 60
DEFAULT_RETENTION_DAYS = 30

GAME_MODES = {
    1: "All Pick",
    2: "Captain's Mode",
    3: "Random Draft",
    4: "Single Draft",
    5: "All Random",
    12: "Least Played",
    16: "Captain's Draft",
    18: "Ability Draft",
    22: "Ranked All Pick",
    23: "Turbo",
}

LOBBY_TYPES = {
    0: "普通匹配",
    1: "练习房",
    2: "锦标赛",
    5: "组队匹配",
    6: "单排匹配",
    7: "天梯匹配",
    9: "对抗机器人",
}

LANE_ROLES = {1: "优势路", 2: "中路", 3: "劣势路", 4: "打野"}


class OpenDotaError(RuntimeError):
    """A user-facing OpenDota error."""


class MatchNotFound(OpenDotaError):
    """The requested match does not exist or is not public."""


class TelegramError(RuntimeError):
    """A user-facing Telegram Bot API error."""


def request_json(
    path: str,
    *,
    method: str = "GET",
    timeout: int = 25,
    retries: int = 2,
) -> Any:
    """Request JSON with retries and readable errors."""
    url = f"{API_BASE}{path}"
    request = Request(
        url,
        method=method,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
                raw = response.read()
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise OpenDotaError("OpenDota 返回了无法解析的数据，请稍后重试。") from exc
        except HTTPError as exc:
            if exc.code == 404:
                if path.startswith("/matches/"):
                    raise MatchNotFound(
                        "没有找到这场比赛。请确认 Match ID 正确，并确认比赛数据已公开。"
                    ) from exc
                raise OpenDotaError("OpenDota 没有找到请求的资源。") from exc
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After")
                suffix = f"，约 {retry_after} 秒后再试" if retry_after else "，请稍后再试"
                raise OpenDotaError(f"OpenDota 请求过于频繁{suffix}。") from exc
            if exc.code in {500, 502, 503, 504} and attempt < retries:
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
                continue
            raise OpenDotaError(f"OpenDota 请求失败（HTTP {exc.code}）。") from exc
        except (URLError, TimeoutError, socket.timeout) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            reason = getattr(exc, "reason", exc)
            raise OpenDotaError(
                f"无法连接 OpenDota：{reason}。请检查网络、代理或防火墙设置。"
            ) from exc

    raise OpenDotaError(f"OpenDota 请求失败：{last_error}")


def load_constant(resource: str, cache_dir: Path) -> Any:
    """Load a static OpenDota resource, using a short-lived local cache."""
    cache_file = cache_dir / f"{resource}.json"
    try:
        if (
            cache_file.exists()
            and time.time() - cache_file.stat().st_mtime < CACHE_MAX_AGE_SECONDS
        ):
            return json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass

    data = request_json(f"/constants/{resource}")
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        # A read-only folder should not prevent report generation.
        pass
    return data


def validate_match_id(raw: str) -> int:
    value = raw.strip()
    if not value.isdigit() or not 1 <= len(value) <= 20:
        raise ValueError("Match ID 应当是 1 到 20 位数字，例如 8943397976。")
    match_id = int(value)
    if match_id <= 0:
        raise ValueError("Match ID 必须大于 0。")
    return match_id


def resolve_steam_account_id(raw: str) -> int:
    """Convert a Dota friend code, SteamID64, or numeric profile URL to account_id."""
    value = raw.strip().rstrip("/")
    profile_match = re.fullmatch(
        r"https?://(?:www\.)?steamcommunity\.com/profiles/(\d+)", value, re.IGNORECASE
    )
    if profile_match:
        value = profile_match.group(1)
    elif "steamcommunity.com/id/" in value.lower():
        raise ValueError(
            "自定义名称形式的 Steam 链接无法用公开接口直接换算。"
            "请改填 Dota 2 好友代码，或 /profiles/数字 形式的 Steam 链接。"
        )

    if not value.isdigit():
        raise ValueError(
            "请填写 Dota 2 好友代码、SteamID64，或 /profiles/数字 形式的 Steam 链接。"
        )

    numeric = int(value)
    account_id = numeric - STEAM_ID64_BASE if numeric >= STEAM_ID64_BASE else numeric
    if not 1 <= account_id <= MAX_ACCOUNT_ID:
        raise ValueError("Steam 标识超出有效范围，请检查是否复制完整。")
    return account_id


def load_settings(settings_path: Path) -> dict[str, Any]:
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(settings_path: Path, data: dict[str, Any]) -> None:
    settings_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_saved_account_id(settings_path: Path) -> int | None:
    try:
        account_id = int(load_settings(settings_path).get("account_id"))
        return account_id if 1 <= account_id <= MAX_ACCOUNT_ID else None
    except (ValueError, TypeError):
        return None


def save_account_id(settings_path: Path, account_id: int) -> None:
    settings = load_settings(settings_path)
    settings["account_id"] = account_id
    save_settings(settings_path, settings)


def validate_chatgpt_project_url(raw: str) -> str:
    value = raw.strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in {"chatgpt.com", "www.chatgpt.com"}:
        raise ValueError("请粘贴以 https://chatgpt.com/ 开头的项目页面链接。")
    if not parsed.path or parsed.path == "/":
        raise ValueError("这个链接不是具体的 ChatGPT 项目页面，请打开项目后复制地址。")
    return value


def load_saved_project_url(settings_path: Path) -> str | None:
    raw = load_settings(settings_path).get("chatgpt_project_url")
    try:
        return validate_chatgpt_project_url(str(raw)) if raw else None
    except ValueError:
        return None


def save_project_url(settings_path: Path, project_url: str) -> None:
    settings = load_settings(settings_path)
    settings["chatgpt_project_url"] = validate_chatgpt_project_url(project_url)
    save_settings(settings_path, settings)


def validate_telegram_bot_token(raw: str) -> str:
    token = raw.strip()
    if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{20,}", token):
        raise ValueError("Bot Token 格式不正确，请重新从 @BotFather 复制。")
    return token


def load_telegram_settings(settings_path: Path) -> dict[str, Any] | None:
    settings = load_settings(settings_path)
    try:
        token = validate_telegram_bot_token(str(settings.get("bot_token") or ""))
        chat_id = int(settings.get("chat_id"))
    except (ValueError, TypeError):
        return None
    return {
        "bot_token": token,
        "chat_id": chat_id,
        "bot_username": str(settings.get("bot_username") or ""),
    }


def save_telegram_settings(
    settings_path: Path,
    *,
    bot_token: str,
    chat_id: int,
    bot_username: str = "",
) -> None:
    settings_path.write_text(
        json.dumps(
            {
                "bot_token": validate_telegram_bot_token(bot_token),
                "chat_id": int(chat_id),
                "bot_username": bot_username,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        settings_path.chmod(0o600)
    except OSError:
        pass


def telegram_api_request(
    bot_token: str,
    method: str,
    *,
    fields: dict[str, Any] | None = None,
    document_path: Path | None = None,
    timeout: int = 60,
) -> Any:
    """Call the Telegram Bot API without exposing the token in errors."""
    token = validate_telegram_bot_token(bot_token)
    url = f"{TELEGRAM_API_BASE}/bot{token}/{method}"
    form_fields = fields or {}

    if document_path is None:
        body = urlencode({key: str(value) for key, value in form_fields.items()}).encode(
            "utf-8"
        )
        content_type = "application/x-www-form-urlencoded"
    else:
        if not document_path.is_file():
            raise TelegramError(f"要发送的文件不存在：{document_path.name}")
        boundary = f"Dota2ReviewBoundary{time.time_ns()}"
        chunks: list[bytes] = []
        for key, value in form_fields.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode("ascii"),
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(
                        "ascii"
                    ),
                    str(value).encode("utf-8"),
                    b"\r\n",
                ]
            )
        safe_name = document_path.name.replace('"', "'").replace("\r", "").replace("\n", "")
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                (
                    "Content-Disposition: form-data; name=\"document\"; "
                    f"filename=\"{safe_name}\"\r\n"
                ).encode("utf-8"),
                b"Content-Type: text/markdown; charset=utf-8\r\n\r\n",
                document_path.read_bytes(),
                b"\r\n",
                f"--{boundary}--\r\n".encode("ascii"),
            ]
        )
        body = b"".join(chunks)
        content_type = f"multipart/form-data; boundary={boundary}"

    request = Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": content_type, "User-Agent": USER_AGENT},
    )
    try:
        with urlopen(
            request, timeout=timeout, context=ssl.create_default_context()
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        description = ""
        try:
            error_payload = json.loads(exc.read().decode("utf-8"))
            description = str(error_payload.get("description") or "")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            pass
        suffix = f"：{description}" if description else ""
        raise TelegramError(f"Telegram 请求失败（HTTP {exc.code}）{suffix}") from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        reason = getattr(exc, "reason", exc)
        raise TelegramError(f"无法连接 Telegram：{reason}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TelegramError("Telegram 返回了无法解析的数据。") from exc

    if not isinstance(payload, dict) or not payload.get("ok"):
        description = payload.get("description") if isinstance(payload, dict) else None
        raise TelegramError(f"Telegram 操作失败：{description or '未知错误'}")
    return payload.get("result")


def telegram_send_message(config: dict[str, Any], text: str) -> None:
    telegram_api_request(
        str(config["bot_token"]),
        "sendMessage",
        fields={"chat_id": config["chat_id"], "text": text[:4096]},
    )


def telegram_send_document(
    config: dict[str, Any], document_path: Path, *, caption: str = ""
) -> None:
    telegram_api_request(
        str(config["bot_token"]),
        "sendDocument",
        fields={"chat_id": config["chat_id"], "caption": caption[:1024]},
        document_path=document_path,
        timeout=120,
    )


def find_latest_private_chat(updates: Any) -> tuple[int, str] | None:
    if not isinstance(updates, list):
        return None
    for update in reversed(updates):
        if not isinstance(update, dict):
            continue
        message = next(
            (
                update.get(key)
                for key in ("message", "edited_message")
                if isinstance(update.get(key), dict)
            ),
            None,
        )
        chat = message.get("chat") if isinstance(message, dict) else None
        if not isinstance(chat, dict) or chat.get("type") != "private":
            continue
        try:
            chat_id = int(chat.get("id"))
        except (TypeError, ValueError):
            continue
        display_name = " ".join(
            str(chat.get(key) or "").strip() for key in ("first_name", "last_name")
        ).strip()
        return chat_id, display_name or str(chat.get("username") or chat_id)
    return None


def setup_telegram(telegram_settings_path: Path) -> int:
    print("请在 Telegram 中通过官方 @BotFather 创建机器人并复制 Bot Token。")
    try:
        token = validate_telegram_bot_token(getpass.getpass("请输入 Bot Token（输入不会显示）："))
        bot = telegram_api_request(token, "getMe")
        if not isinstance(bot, dict):
            raise TelegramError("没有取得机器人资料。")
        username = str(bot.get("username") or "")
        bot_name = f"@{username}" if username else str(bot.get("first_name") or "你的机器人")
        print(f"已连接机器人：{bot_name}")
        input(f"现在打开 Telegram，给 {bot_name} 发送 /start；发送完成后按回车继续：")
        updates = telegram_api_request(
            token,
            "getUpdates",
            fields={"timeout": 0, "limit": 100, "allowed_updates": '["message"]'},
        )
        latest = find_latest_private_chat(updates)
        if latest is None:
            raise TelegramError(
                "没有读取到你的私聊。请确认已给机器人发送 /start，然后重新运行设置。"
            )
        chat_id, display_name = latest
        save_telegram_settings(
            telegram_settings_path,
            bot_token=token,
            chat_id=chat_id,
            bot_username=username,
        )
        telegram_send_message(
            load_telegram_settings(telegram_settings_path) or {},
            "✅ Dota 2 每日复盘推送已连接。以后软路由会把每日复盘发送到这里。",
        )
        print(f"Telegram 推送设置成功，接收人：{display_name}")
        return 0
    except ValueError as exc:
        print(f"输入错误：{exc}", file=sys.stderr)
        return 2
    except TelegramError as exc:
        print(f"Telegram 设置失败：{exc}", file=sys.stderr)
        return 6
    except OSError as exc:
        print(f"Telegram 设置保存失败：{exc}", file=sys.stderr)
        return 5
    except (EOFError, KeyboardInterrupt):
        print("\n已取消 Telegram 设置。", file=sys.stderr)
        return 130


def notify_telegram_if_configured(
    telegram_settings_path: Path,
    text: str,
    *,
    documents: list[tuple[Path, str]] | None = None,
) -> bool:
    config = load_telegram_settings(telegram_settings_path)
    if config is None:
        return False
    try:
        telegram_send_message(config, text)
        for document_path, caption in documents or []:
            telegram_send_document(config, document_path, caption=caption)
        print("Telegram 推送已发送。")
        return True
    except TelegramError as exc:
        print(f"Telegram 推送失败：{exc}", file=sys.stderr)
        return False


def telegram_plain_text(text: str) -> str:
    return text.replace("**", "").replace("`", "")


def telegram_match_caption(
    target_date: date,
    label: str,
    match: dict[str, Any],
    hero_by_id: dict[int, str],
) -> str:
    hero = hero_name(match.get("hero_id"), hero_by_id)
    kills = int(match.get("kills") or 0)
    deaths = int(match.get("deaths") or 0)
    assists = int(match.get("assists") or 0)
    return (
        f"{target_date.isoformat()}｜{label}｜{hero}｜"
        f"KDA {kills}/{deaths}/{assists}｜Match {match.get('match_id')}｜基础数据复盘"
    )


def load_daily_state(state_path: Path) -> dict[str, Any]:
    state = load_settings(state_path)
    sent = state.get("sent")
    return {"sent": sent if isinstance(sent, dict) else {}}


def daily_was_sent(state_path: Path, target_date: date) -> bool:
    return target_date.isoformat() in load_daily_state(state_path)["sent"]


def mark_daily_sent(
    state_path: Path,
    target_date: date,
    completed: list[tuple[str, dict[str, Any], Path]],
) -> None:
    state = load_daily_state(state_path)
    sent = state["sent"]
    sent[target_date.isoformat()] = {
        "sent_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "match_ids": [int(match.get("match_id") or 0) for _, match, _ in completed],
    }
    if len(sent) > 120:
        for key in sorted(sent)[:-120]:
            sent.pop(key, None)
    save_settings(state_path, state)


def delete_generated_tree(path: Path, allowed_root: Path) -> tuple[int, int]:
    """Remove one exact tool-created tree after validating its containment."""
    if not path.exists():
        return 0, 0
    resolved_path = path.resolve()
    resolved_root = allowed_root.resolve()
    resolved_path.relative_to(resolved_root)
    if resolved_path == resolved_root or path.is_symlink() or not path.is_dir():
        raise OSError("拒绝删除非预期目录。")
    files = [item for item in path.rglob("*") if item.is_file() and not item.is_symlink()]
    file_count = len(files)
    total_bytes = sum(item.stat().st_size for item in files)
    shutil.rmtree(path)
    return file_count, total_bytes


def delete_generated_files(paths: Iterable[Path]) -> tuple[int, int]:
    removed = 0
    reclaimed = 0
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        try:
            if path.is_symlink() or not path.is_file():
                continue
            size = path.stat().st_size
            path.unlink()
            removed += 1
            reclaimed += size
        except OSError:
            continue
    return removed, reclaimed


def purge_generated_data(script_dir: Path) -> dict[str, int]:
    removed = 0
    reclaimed = 0
    for name in ("reports", "daily_logs", ".cache"):
        root = script_dir / name
        if not root.is_dir() or root.is_symlink():
            continue
        files = [item for item in root.rglob("*") if item.is_file() and not item.is_symlink()]
        removed += len(files)
        reclaimed += sum(item.stat().st_size for item in files)
        shutil.rmtree(root)
    return {"removed_files": removed, "reclaimed_bytes": reclaimed}


def cleanup_old_downloads(
    script_dir: Path,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete only old, bulky tool-generated data while preserving summaries."""
    cutoff = time.time() - retention_days * 24 * 60 * 60
    reports_root = script_dir / "reports"
    logs_root = script_dir / "daily_logs"
    candidates: list[tuple[Path, Path]] = []
    if reports_root.is_dir():
        for pattern in (
            "*_OpenDota原始数据.json",
            "*_GPT复盘包.md",
            "daily_*_chatgpt_bundle.md",
        ):
            candidates.extend((path, reports_root) for path in reports_root.rglob(pattern))
    if logs_root.is_dir():
        candidates.extend((path, logs_root) for path in logs_root.glob("*.log"))

    removed_files = 0
    reclaimed_bytes = 0
    errors: list[str] = []
    seen: set[Path] = set()
    for path, allowed_root in candidates:
        if path in seen:
            continue
        seen.add(path)
        try:
            if path.is_symlink() or not path.is_file():
                continue
            path.resolve().relative_to(allowed_root.resolve())
            stat = path.stat()
            if stat.st_mtime >= cutoff:
                continue
            if not dry_run:
                path.unlink()
            removed_files += 1
            reclaimed_bytes += stat.st_size
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: {exc}")

    return {
        "removed_files": removed_files,
        "reclaimed_bytes": reclaimed_bytes,
        "errors": errors,
        "dry_run": dry_run,
        "retention_days": retention_days,
    }


def format_byte_size(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{int(size)} B"


def print_cleanup_result(result: dict[str, Any]) -> None:
    action = "预计可清理" if result.get("dry_run") else "已清理"
    print(
        f"存储清理：保留最近 {result.get('retention_days')} 天，"
        f"{action} {result.get('removed_files')} 个大文件，"
        f"释放 {format_byte_size(int(result.get('reclaimed_bytes') or 0))}。"
    )
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        print(f"其中 {len(errors)} 个文件清理失败，已跳过。", file=sys.stderr)


def player_match_won(match: dict[str, Any]) -> bool:
    radiant = int(match.get("player_slot") or 0) < 128
    return bool(match.get("radiant_win")) == radiant


def fetch_recent_matches(account_id: int) -> list[dict[str, Any]]:
    response = request_json(f"/players/{account_id}/recentMatches")
    if not isinstance(response, list):
        raise OpenDotaError("OpenDota 返回的最近比赛列表格式异常。")
    return [row for row in response if isinstance(row, dict)]


def fetch_matches_for_local_date(
    account_id: int, target_date: date
) -> list[dict[str, Any]]:
    today = datetime.now().astimezone().date()
    lookback_days = max(2, (today - target_date).days + 2)
    response = request_json(f"/players/{account_id}/matches?date={lookback_days}")
    if not isinstance(response, list):
        raise OpenDotaError("OpenDota 返回的比赛历史格式异常。")
    matches: list[dict[str, Any]] = []
    for row in response:
        if not isinstance(row, dict):
            continue
        try:
            match_date = datetime.fromtimestamp(int(row.get("start_time"))).astimezone().date()
        except (TypeError, ValueError, OSError):
            continue
        if match_date == target_date:
            matches.append(row)
    return matches


def enrich_match_summaries(
    account_id: int, matches: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Add the user's detailed performance fields before representative selection."""
    enriched: list[dict[str, Any]] = []
    for index, summary in enumerate(matches, start=1):
        match_id = summary.get("match_id")
        print(f"  正在读取详细数据 {index}/{len(matches)}：{match_id}")
        try:
            details = request_json(f"/matches/{int(match_id)}", retries=1)
            players = details.get("players") if isinstance(details, dict) else None
            if not isinstance(players, list):
                enriched.append(summary)
                continue
            target = next(
                (
                    player
                    for player in players
                    if player.get("account_id") is not None
                    and int(player.get("account_id")) == account_id
                ),
                None,
            )
            if target is None:
                target = next(
                    (
                        player
                        for player in players
                        if int(player.get("player_slot") or 0)
                        == int(summary.get("player_slot") or 0)
                    ),
                    None,
                )
            merged = dict(summary)
            if isinstance(target, dict):
                merged.update(target)
            if isinstance(details, dict):
                for key in ("radiant_win", "duration", "start_time", "game_mode", "lobby_type"):
                    if key in details:
                        merged[key] = details[key]
            enriched.append(merged)
        except (OpenDotaError, TypeError, ValueError):
            enriched.append(summary)
    return enriched


def numeric_value(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def performance_score(match: dict[str, Any]) -> float:
    """Estimate individual impact for selecting representative daily matches."""
    duration = max(1.0, numeric_value(match.get("duration")))
    kills = numeric_value(match.get("kills"))
    deaths = numeric_value(match.get("deaths"))
    assists = numeric_value(match.get("assists"))
    gpm = numeric_value(match.get("gold_per_min"))
    xpm = numeric_value(match.get("xp_per_min"))
    hero_damage_per_min = numeric_value(match.get("hero_damage")) * 60 / duration
    tower_damage_per_min = numeric_value(match.get("tower_damage")) * 60 / duration
    result_score = 25.0 if player_match_won(match) else -25.0
    return (
        result_score
        + kills * 2.0
        + assists * 0.8
        - deaths * 3.0
        + gpm / 40.0
        + xpm / 60.0
        + hero_damage_per_min / 150.0
        + tower_damage_per_min / 100.0
    )


def select_daily_representatives(
    matches: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    if not matches:
        return []
    ranked = sorted(matches, key=performance_score)
    if len(ranked) == 1:
        return [("当日唯一比赛", ranked[0])]
    return [("表现最好", ranked[-1]), ("表现最差", ranked[0])]


def match_selection_summary(
    label: str, match: dict[str, Any], hero_by_id: dict[int, str]
) -> str:
    result = "胜利" if player_match_won(match) else "失败"
    hero = hero_name(match.get("hero_id"), hero_by_id)
    kda = f"{match.get('kills', '-')}/{match.get('deaths', '-')}/{match.get('assists', '-')}"
    return (
        f"- **{label}**：Match ID `{match.get('match_id', '-')}`，{hero}，{result}，"
        f"K/D/A {kda}，GPM {match.get('gold_per_min', '-')}，"
        f"XPM {match.get('xp_per_min', '-')}，综合分 {performance_score(match):.1f}。"
    )


def print_recent_matches(
    matches: list[dict[str, Any]], hero_by_id: dict[int, str]
) -> None:
    print("\n我的最近比赛：")
    print("序号  时间              结果  英雄                    K/D/A       时长      Match ID")
    print("-" * 88)
    for index, match in enumerate(matches, start=1):
        timestamp = format_local_time(match.get("start_time"))
        if timestamp != "-":
            timestamp = timestamp[:16]
        result = "胜利" if player_match_won(match) else "失败"
        hero = hero_name(match.get("hero_id"), hero_by_id)
        kda = f"{match.get('kills', '-')}/{match.get('deaths', '-')}/{match.get('assists', '-')}"
        print(
            f"{index:>2}.   {timestamp:<16}  {result:<4}  {hero[:20]:<20}  "
            f"{kda:<10}  {format_duration(match.get('duration')):<8}  {match.get('match_id', '-')}"
        )


def choose_recent_match(matches: list[dict[str, Any]]) -> dict[str, Any]:
    while True:
        try:
            raw = input(f"\n请选择比赛序号（1-{len(matches)}，输入 Q 取消）：").strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise KeyboardInterrupt from exc
        if raw.lower() == "q":
            raise KeyboardInterrupt
        if raw.isdigit() and 1 <= int(raw) <= len(matches):
            return matches[int(raw) - 1]
        print("输入无效，请输入列表左侧的序号。")


def is_radiant(player: dict[str, Any]) -> bool:
    return int(player.get("player_slot") or 0) < 128


def player_label(player: dict[str, Any]) -> str:
    return (
        player.get("personaname")
        or (f"匿名玩家 {player.get('account_id')}" if player.get("account_id") else "匿名玩家")
    )


def find_focus_player(
    match: dict[str, Any],
    *,
    focus_account_id: int | None,
    focus_player_slot: int | None,
) -> dict[str, Any] | None:
    for player in match.get("players") or []:
        try:
            if (
                focus_account_id is not None
                and player.get("account_id") is not None
                and int(player.get("account_id")) == focus_account_id
            ):
                return player
            if (
                focus_player_slot is not None
                and int(player.get("player_slot") or 0) == focus_player_slot
            ):
                return player
        except (TypeError, ValueError):
            continue
    return None


def safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned or "未知比赛"


def build_match_artifact_stem(
    match: dict[str, Any],
    heroes: Any,
    *,
    focus_account_id: int | None,
    focus_player_slot: int | None,
) -> str:
    player = find_focus_player(
        match,
        focus_account_id=focus_account_id,
        focus_player_slot=focus_player_slot,
    )
    if player is None and match.get("hero_id") is not None:
        # Player match-history rows already represent the bound user.
        player = match
    hero_by_id, _ = make_hero_maps(heroes)
    hero = hero_name(player.get("hero_id"), hero_by_id) if player else "未知英雄"
    if player:
        kda = f"{int(player.get('kills') or 0)}-{int(player.get('deaths') or 0)}-{int(player.get('assists') or 0)}"
    else:
        kda = "未知KDA"
    try:
        match_date = datetime.fromtimestamp(int(match.get("start_time"))).astimezone().strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        match_date = "未知日期"
    match_id = match.get("match_id") or "未知ID"
    return safe_filename(f"{match_date}_{hero}_{kda}_Match_{match_id}")


def md_escape(value: Any) -> str:
    return str(value if value is not None else "-").replace("|", "\\|").replace("\n", " ")


def format_duration(seconds: Any) -> str:
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError):
        return "-"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def format_game_time(seconds: Any) -> str:
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "-"
    sign = "-" if total < 0 else ""
    total = abs(total)
    minutes, secs = divmod(total, 60)
    return f"{sign}{minutes}:{secs:02d}"


def format_local_time(timestamp: Any) -> str:
    try:
        return datetime.fromtimestamp(int(timestamp)).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except (TypeError, ValueError, OSError):
        return "-"


def format_number(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "-"


def format_gold_delta(value: int) -> str:
    if value == 0:
        return "持平"
    side = "天辉" if value > 0 else "夜魇"
    return f"{side} +{abs(value):,}"


def load_chinese_hero_names() -> dict[int, str]:
    """Load the bundled Simplified Chinese hero names, falling back safely."""
    names_path = Path(__file__).resolve().parent / "hero_names_zh.json"
    try:
        raw = json.loads(names_path.read_text(encoding="utf-8"))
        return {
            int(hero_id): str(name)
            for hero_id, name in raw.items()
            if str(hero_id).isdigit() and name
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError, AttributeError):
        return {}


def make_hero_maps(heroes: Any) -> tuple[dict[int, str], dict[str, str]]:
    by_id: dict[int, str] = {}
    by_internal: dict[str, str] = {}
    chinese_names = load_chinese_hero_names()
    values: Iterable[Any] = heroes.values() if isinstance(heroes, dict) else heroes or []
    for hero in values:
        if not isinstance(hero, dict):
            continue
        try:
            hero_id = int(hero.get("id"))
        except (TypeError, ValueError):
            continue
        display = (
            chinese_names.get(hero_id)
            or hero.get("localized_name")
            or hero.get("name")
            or f"英雄 {hero_id}"
        )
        by_id[hero_id] = display
        internal = hero.get("name")
        if internal:
            by_internal[str(internal)] = display
    return by_id, by_internal


def make_item_maps(items: Any) -> tuple[dict[int, str], dict[str, dict[str, Any]]]:
    by_id: dict[int, str] = {}
    by_key: dict[str, dict[str, Any]] = {}
    if not isinstance(items, dict):
        return by_id, by_key
    for key, item in items.items():
        if not isinstance(item, dict):
            continue
        display = item.get("dname") or str(key).replace("_", " ").title()
        normalized = dict(item)
        normalized["_display"] = display
        by_key[str(key)] = normalized
        try:
            by_id[int(item.get("id"))] = display
        except (TypeError, ValueError):
            pass
    return by_id, by_key


def hero_name(hero_id: Any, hero_by_id: dict[int, str]) -> str:
    try:
        hid = int(hero_id)
    except (TypeError, ValueError):
        return "未知英雄"
    return hero_by_id.get(hid, f"英雄 {hid}")


def item_name(item_id: Any, item_by_id: dict[int, str]) -> str:
    try:
        iid = int(item_id)
    except (TypeError, ValueError):
        return "未知物品"
    if iid <= 0:
        return ""
    return item_by_id.get(iid, f"物品 {iid}")


def purchase_name(key: Any, item_by_key: dict[str, dict[str, Any]]) -> str:
    normalized = str(key or "").removeprefix("item_")
    item = item_by_key.get(normalized, {})
    return str(item.get("_display") or normalized.replace("_", " ").title() or "未知物品")


def killer_name(raw: Any, hero_by_internal: dict[str, str]) -> str:
    value = str(raw or "未知来源")
    if value in hero_by_internal:
        return hero_by_internal[value]
    aliases = {
        "npc_dota_goodguys_tower": "天辉防御塔",
        "npc_dota_badguys_tower": "夜魇防御塔",
        "npc_dota_roshan": "Roshan",
        "npc_dota_neutral": "中立生物",
    }
    for prefix, label in aliases.items():
        if value.startswith(prefix):
            return label
    return value.removeprefix("npc_dota_hero_").removeprefix("npc_dota_").replace("_", " ").title()


def parsed_sections(match: dict[str, Any]) -> tuple[bool, list[str]]:
    players = [player for player in (match.get("players") or []) if isinstance(player, dict)]
    expected_gold_points = max(2, int(match.get("duration") or 0) // 60)
    has_full_roster = len(players) >= 10
    has_gold = has_full_roster and all(
        isinstance(player.get("gold_t"), list)
        and len(player["gold_t"]) >= expected_gold_points
        for player in players
    )
    has_purchases = has_full_roster and all(
        isinstance(player.get("purchase_log"), list) for player in players
    )
    # Parsed matches can legitimately have more deaths than hero-kill log rows
    # because suicides, neutral deaths and denies are not represented there.
    # Presence of a log array for every player is the reliable completeness signal.
    has_deaths = has_full_roster and (
        all(isinstance(player.get("kills_log"), list) for player in players)
        or all(isinstance(player.get("deaths_log"), list) for player in players)
    )
    missing: list[str] = []
    if not has_gold:
        missing.append("经济曲线")
    if not has_purchases:
        missing.append("购买时间线")
    if not has_deaths:
        missing.append("死亡时间线")
    parsed = bool(match.get("version")) and not missing
    return parsed, missing


def build_team_gold_curve(match: dict[str, Any]) -> list[tuple[int, int, int, int]]:
    players = match.get("players") or []
    radiant = [player for player in players if is_radiant(player)]
    dire = [player for player in players if not is_radiant(player)]
    if not radiant or not dire:
        return []

    lengths = [
        len(player.get("gold_t") or [])
        for player in players
        if isinstance(player.get("gold_t"), list)
    ]
    if len(lengths) != len(players) or not lengths:
        return []

    curve: list[tuple[int, int, int, int]] = []
    for minute in range(min(lengths)):
        try:
            radiant_gold = sum(int(player["gold_t"][minute]) for player in radiant)
            dire_gold = sum(int(player["gold_t"][minute]) for player in dire)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        curve.append((minute, radiant_gold, dire_gold, radiant_gold - dire_gold))
    return curve


def economic_key_points(curve: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    if not curve:
        return []
    by_minute = {row[0]: row for row in curve}
    last_minute = curve[-1][0]
    chosen = set(range(0, last_minute + 1, 5))
    chosen.add(last_minute)
    chosen.add(max(curve, key=lambda row: abs(row[3]))[0])
    if len(curve) > 1:
        largest_swing = max(
            zip(curve, curve[1:]), key=lambda pair: abs(pair[1][3] - pair[0][3])
        )[1]
        chosen.add(largest_swing[0])
    return [by_minute[minute] for minute in sorted(chosen) if minute in by_minute]


def lead_change_minutes(curve: list[tuple[int, int, int, int]]) -> list[int]:
    changes: list[int] = []
    previous_sign = 0
    for minute, _, _, delta in curve:
        sign = 1 if delta > 0 else -1 if delta < 0 else 0
        if sign and previous_sign and sign != previous_sign:
            changes.append(minute)
        if sign:
            previous_sign = sign
    return changes


def final_item_ids(player: dict[str, Any]) -> list[int]:
    fields = [f"item_{index}" for index in range(6)]
    fields += [f"backpack_{index}" for index in range(3)]
    fields += ["item_neutral"]
    result: list[int] = []
    for field in fields:
        try:
            value = int(player.get(field) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            result.append(value)
    return result


def notable_purchases(
    player: dict[str, Any],
    item_by_id: dict[int, str],
    item_by_key: dict[str, dict[str, Any]],
    *,
    include_all: bool,
) -> list[dict[str, Any]]:
    final_names = {item_name(iid, item_by_id) for iid in final_item_ids(player)}
    result: list[dict[str, Any]] = []
    for purchase in player.get("purchase_log") or []:
        if not isinstance(purchase, dict):
            continue
        key = str(purchase.get("key") or "").removeprefix("item_")
        display = purchase_name(key, item_by_key)
        data = item_by_key.get(key, {})
        try:
            cost = int(data.get("cost") or 0)
        except (TypeError, ValueError):
            cost = 0
        is_recipe = key.startswith("recipe_")
        if include_all or (not is_recipe and (cost >= 500 or display in final_names)):
            result.append({"time": purchase.get("time"), "name": display})
    result.sort(key=lambda row: int(row.get("time") or 0))
    return result


def markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    output = [
        "| " + " | ".join(md_escape(value) for value in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    output.extend(
        "| " + " | ".join(md_escape(value) for value in row) + " |" for row in rows
    )
    return output


def generate_report(
    match: dict[str, Any],
    heroes: Any,
    items: Any,
    *,
    include_all_purchases: bool = False,
    focus_account_id: int | None = None,
    focus_player_slot: int | None = None,
) -> tuple[str, list[str]]:
    """Return Markdown and the list of missing parsed sections."""
    hero_by_id, hero_by_internal = make_hero_maps(heroes)
    item_by_id, item_by_key = make_item_maps(items)
    match_id = int(match.get("match_id") or 0)
    players = match.get("players") or []
    parsed, missing = parsed_sections(match)
    winner = "天辉" if match.get("radiant_win") else "夜魇"
    mode_id = match.get("game_mode")
    lobby_id = match.get("lobby_type")
    mode = f"{GAME_MODES.get(mode_id, '未知模式')}（{mode_id}）"
    lobby = f"{LOBBY_TYPES.get(lobby_id, '未知类型')}（{lobby_id}）"
    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    focus_player = find_focus_player(
        match,
        focus_account_id=focus_account_id,
        focus_player_slot=focus_player_slot,
    )

    lines = [
        f"# Dota 2 比赛 {match_id} 复盘摘要",
        "",
        f"> 数据来源：[OpenDota 比赛页面](https://www.opendota.com/matches/{match_id})；生成时间：{generated_at}",
        "",
    ]
    if missing:
        lines += [
            "> [!WARNING]",
            f"> OpenDota 当前缺少解析数据：{'、'.join(missing)}。基础信息仍会输出；完整时间线需要先 Request Parse。",
            f"> 命令：`py dota2_review.py {match_id} --request-parse`",
            "",
        ]

    lines += ["## 比赛概况", ""]
    overview_rows = [
        ["胜方", winner],
        ["比分", f"天辉 {match.get('radiant_score', '-')} : {match.get('dire_score', '-')} 夜魇"],
        ["时长", format_duration(match.get("duration"))],
        ["开始时间", format_local_time(match.get("start_time"))],
        ["模式", mode],
        ["房间类型", lobby],
        ["解析状态", "完整" if parsed else "不完整"],
    ]
    lines += markdown_table(["项目", "内容"], overview_rows)

    if focus_player is not None:
        my_kills = int(focus_player.get("kills") or 0)
        my_deaths = int(focus_player.get("deaths") or 0)
        my_assists = int(focus_player.get("assists") or 0)
        my_side_won = bool(match.get("radiant_win")) == is_radiant(focus_player)
        lines += ["", "## 我的表现", ""]
        lines += markdown_table(
            ["结果", "英雄", "K/D/A", "KDA", "GPM", "XPM", "终局经济"],
            [[
                "胜利" if my_side_won else "失败",
                hero_name(focus_player.get("hero_id"), hero_by_id),
                f"{my_kills}/{my_deaths}/{my_assists}",
                f"{(my_kills + my_assists) / max(1, my_deaths):.2f}",
                focus_player.get("gold_per_min", "-"),
                focus_player.get("xp_per_min", "-"),
                format_number(focus_player.get("net_worth")),
            ]],
        )

    for side_name, side_players in (
        ("天辉", [player for player in players if is_radiant(player)]),
        ("夜魇", [player for player in players if not is_radiant(player)]),
    ):
        lines += ["", f"## {side_name}阵容与数据", ""]
        rows: list[list[Any]] = []
        for index, player in enumerate(side_players, start=1):
            kills = int(player.get("kills") or 0)
            deaths = int(player.get("deaths") or 0)
            assists = int(player.get("assists") or 0)
            kda = (kills + assists) / max(1, deaths)
            rows.append(
                [
                    index,
                    ("👉 " if player is focus_player else "") + player_label(player),
                    hero_name(player.get("hero_id"), hero_by_id),
                    LANE_ROLES.get(player.get("lane_role"), "-"),
                    f"{kills}/{deaths}/{assists}",
                    f"{kda:.2f}",
                    player.get("gold_per_min", "-"),
                    player.get("xp_per_min", "-"),
                    f"{player.get('last_hits', '-')} / {player.get('denies', '-')}",
                    format_number(player.get("net_worth")),
                ]
            )
        lines += markdown_table(
            ["#", "玩家", "英雄", "分路", "K/D/A", "KDA", "GPM", "XPM", "正/反补", "终局经济"],
            rows,
        )

    lines += ["", "## 经济曲线关键点", ""]
    curve = build_team_gold_curve(match)
    if curve:
        key_rows = [
            [f"{minute}:00", format_number(radiant), format_number(dire), format_gold_delta(delta)]
            for minute, radiant, dire, delta in economic_key_points(curve)
        ]
        lines += markdown_table(["时间", "天辉团队经济", "夜魇团队经济", "经济领先"], key_rows)
        max_lead = max(curve, key=lambda row: abs(row[3]))
        swing_from, swing_to = max(
            zip(curve, curve[1:]), key=lambda pair: abs(pair[1][3] - pair[0][3])
        ) if len(curve) > 1 else (curve[0], curve[0])
        changes = lead_change_minutes(curve)
        lines += [
            "",
            f"- 最大经济差：{max_lead[0]}:00，{format_gold_delta(max_lead[3])}。",
            f"- 最大单分钟摆动：{swing_from[0]}:00 → {swing_to[0]}:00，经济差变化 {abs(swing_to[3] - swing_from[3]):,}。",
            f"- 经济领先易手：{len(changes)} 次" + (f"（{', '.join(f'{m}:00' for m in changes[:12])}）" if changes else "。"),
        ]
    else:
        lines.append("未取得完整的 `gold_t` 数据，无法生成团队经济曲线。请先 Request Parse。")

    lines += ["", "## 出装与购买时间", ""]
    if not include_all_purchases:
        lines += ["> 默认仅列出价格不低于 500 的物品和终局保留物品；加 `--all-purchases` 可列出全部购买记录。", ""]
    for player in players:
        hero = hero_name(player.get("hero_id"), hero_by_id)
        side = "天辉" if is_radiant(player) else "夜魇"
        inventory = [item_name(iid, item_by_id) for iid in final_item_ids(player)]
        inventory = [name for name in inventory if name]
        purchases = notable_purchases(
            player,
            item_by_id,
            item_by_key,
            include_all=include_all_purchases,
        )
        purchase_text = "；".join(
            f"{format_game_time(row['time'])} {row['name']}" for row in purchases
        ) or "无可用购买记录"
        lines += [
            f"### {side} · {hero} · {player_label(player)}",
            "",
            f"- 终局物品：{'、'.join(inventory) if inventory else '无可用数据'}",
            f"- 购买顺序：{purchase_text}",
            "",
        ]

    lines += ["## 死亡时间线", ""]
    deaths: list[dict[str, Any]] = []
    hero_values: Iterable[Any] = heroes.values() if isinstance(heroes, dict) else heroes or []
    internal_by_id: dict[int, str] = {}
    for hero in hero_values:
        if not isinstance(hero, dict) or not hero.get("name"):
            continue
        try:
            internal_by_id[int(hero.get("id"))] = str(hero["name"])
        except (TypeError, ValueError):
            continue
    player_by_internal = {
        internal_by_id.get(int(player.get("hero_id") or 0)): player
        for player in players
        if internal_by_id.get(int(player.get("hero_id") or 0))
    }

    has_kills_log = any(player.get("kills_log") for player in players)
    if has_kills_log:
        for killer_player in players:
            for kill in killer_player.get("kills_log") or []:
                if not isinstance(kill, dict):
                    continue
                victim_key = str(kill.get("key") or "")
                victim_player = player_by_internal.get(victim_key)
                victim_hero = killer_name(victim_key, hero_by_internal)
                victim_label = (
                    f"{victim_hero}（{player_label(victim_player)}）"
                    if victim_player is not None
                    else victim_hero
                )
                deaths.append(
                    {
                        "time": kill.get("time"),
                        "side": (
                            "天辉" if is_radiant(victim_player) else "夜魇"
                        ) if victim_player is not None else "-",
                        "victim": victim_label,
                        "killer": (
                            f"{hero_name(killer_player.get('hero_id'), hero_by_id)}"
                            f"（{player_label(killer_player)}）"
                        ),
                    }
                )
    else:
        # Compatibility with older fixtures or alternate OpenDota payloads.
        for player in players:
            for death in player.get("deaths_log") or []:
                if not isinstance(death, dict):
                    continue
                deaths.append(
                    {
                        "time": death.get("time"),
                        "side": "天辉" if is_radiant(player) else "夜魇",
                        "victim": f"{hero_name(player.get('hero_id'), hero_by_id)}（{player_label(player)}）",
                        "killer": killer_name(death.get("killername"), hero_by_internal),
                    }
                )
    deaths.sort(key=lambda row: int(row.get("time") or 0))
    if deaths:
        lines += markdown_table(
            ["时间", "阵营", "阵亡者", "击杀来源"],
            [[format_game_time(row["time"]), row["side"], row["victim"], row["killer"]] for row in deaths],
        )
    else:
        lines.append("没有取得死亡日志；若本局确有击杀，请先 Request Parse。")

    lines += [
        "",
        "## 数据说明",
        "",
        "- 本报告只读取 OpenDota 公开 API，不登录 Steam、完美世界电竞或其他账号。",
        "- 经济关键点由每名玩家的 `gold_t` 按阵营求和计算；时间粒度为 1 分钟。",
        "- OpenDota 的回放解析结果可能晚于比赛结束时间，且过期或不可下载的回放可能无法补全。",
        "",
    ]
    return "\n".join(lines), missing


def request_parse(match_id: int) -> str | None:
    response = request_json(f"/request/{match_id}", method="POST", retries=1)
    if not isinstance(response, dict):
        return None
    job = response.get("job") or {}
    return str(job.get("jobId")) if job.get("jobId") is not None else None


def wait_for_parse(
    match_id: int,
    *,
    timeout_seconds: int = PARSE_WAIT_TIMEOUT_SECONDS,
    poll_interval_seconds: int = PARSE_POLL_INTERVAL_SECONDS,
) -> tuple[dict[str, Any], bool]:
    """Poll OpenDota until the parsed match data is complete or time runs out."""
    deadline = time.monotonic() + max(0, timeout_seconds)
    latest: dict[str, Any] = {}
    poll_number = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(max(1, poll_interval_seconds), max(0, remaining)))
        poll_number += 1
        response = request_json(f"/matches/{match_id}", retries=1)
        if not isinstance(response, dict):
            continue
        latest = response
        parsed, missing = parsed_sections(latest)
        status = "解析完成" if parsed else f"仍缺少：{'、'.join(missing) or '解析版本'}"
        print(f"  第 {poll_number} 次检查：{status}")
        if parsed:
            return latest, True
    return latest, False


def make_chatgpt_bundle(
    match: dict[str, Any],
    report: str,
    *,
    focus_account_id: int | None,
) -> str:
    match_id = match.get("match_id", "-")
    focus_text = str(focus_account_id) if focus_account_id is not None else "请根据报告中的 👉 标记识别"
    instructions = [
        f"# Dota 2 比赛 {match_id} · ChatGPT 复盘数据包",
        "",
        "请结合本项目的复盘要求，分析这场比赛。重点关注：",
        "",
        "- 对线期补刀、换血、死亡与资源获取；",
        "- 刷钱路线、关键装备时间和经济曲线；",
        "- 团战站位、技能与物品使用；",
        "- 导致胜负的关键决策；",
        "- 给出三条下一局能直接执行的改进建议。",
        "",
        f"我的 OpenDota account_id：`{focus_text}`。",
        "",
        "下面先给出自动摘要，再附完整 OpenDota 原始数据。不要只复述数据，要结合时间线判断具体问题。",
        "",
        "---",
        "",
        report,
        "",
        "---",
        "",
        "## 完整 OpenDota JSON",
        "",
        "```json",
        json.dumps(match, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(instructions)


def open_chatgpt_handoff(bundle_path: Path, project_url: str | None) -> None:
    """Open Explorer beside the bundle and open the saved ChatGPT project page."""
    if sys.platform.startswith("win"):
        try:
            subprocess.Popen(["explorer.exe", f"/select,{bundle_path}"])
        except OSError:
            pass
    if project_url and webbrowser is not None:
        webbrowser.open(project_url, new=2)


def run_daily_review(
    args: argparse.Namespace,
    *,
    script_dir: Path,
    settings_path: Path,
) -> int:
    telegram_settings_path = script_dir / "telegram_settings.json"
    daily_state_path = script_dir / "daily_state.json"
    if not getattr(args, "no_cleanup", False):
        cleanup_result = cleanup_old_downloads(
            script_dir,
            retention_days=int(getattr(args, "retention_days", DEFAULT_RETENTION_DAYS)),
        )
        print_cleanup_result(cleanup_result)
    account_id = load_saved_account_id(settings_path)
    if account_id is None:
        print(
            "每日复盘尚未绑定 Dota 好友代码。请先双击 run.bat，选择“我的 Steam 最近比赛”完成绑定。",
            file=sys.stderr,
        )
        return 2

    target_date = datetime.now().astimezone().date() - timedelta(days=args.day_offset)
    if (
        not getattr(args, "no_telegram", False)
        and load_telegram_settings(telegram_settings_path) is not None
        and daily_was_sent(daily_state_path, target_date)
    ):
        print(f"{target_date.isoformat()} 的复盘已经成功发送到 Telegram，本次不重复生成或发送。")
        return 0
    print(f"正在读取 {target_date.isoformat()} 的全部比赛 …")
    try:
        matches = fetch_matches_for_local_date(account_id, target_date)
        if not matches:
            print(f"{target_date.isoformat()} 没有查询到公开比赛，无需生成每日复盘。")
            if not getattr(args, "no_telegram", False):
                notify_telegram_if_configured(
                    telegram_settings_path,
                    f"🎮 {target_date.isoformat()} 没有查询到公开 Dota 2 比赛，今日不生成复盘。",
                )
            return 0
        print(f"当天共找到 {len(matches)} 场，正在补充个人经济和伤害数据 …")
        matches = enrich_match_summaries(account_id, matches)
        heroes = load_constant("heroes", script_dir / ".cache")
        hero_by_id, _ = make_hero_maps(heroes)
    except OpenDotaError as exc:
        print(f"每日比赛查询失败：{exc}", file=sys.stderr)
        if not getattr(args, "no_telegram", False):
            notify_telegram_if_configured(
                telegram_settings_path,
                f"⚠️ {target_date.isoformat()} Dota 2 每日比赛查询失败：{exc}",
            )
        return 4

    selected = select_daily_representatives(matches)
    print(f"当天共 {len(matches)} 场，已选出 {len(selected)} 场代表比赛：")
    for label, match in selected:
        print("  " + match_selection_summary(label, match, hero_by_id).removeprefix("- "))

    output_dir = script_dir / "reports" / "daily" / target_date.isoformat()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"创建每日复盘目录失败：{exc}", file=sys.stderr)
        return 5

    completed: list[tuple[str, dict[str, Any], Path]] = []
    for label, match in selected:
        try:
            match_id = validate_match_id(str(match.get("match_id") or ""))
        except ValueError:
            print(f"跳过无效比赛记录：{match.get('match_id')}", file=sys.stderr)
            continue
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            str(match_id),
            "--no-open-project",
            "--parse-timeout",
            str(args.parse_timeout),
            "--output-root",
            str(output_dir),
            "--require-complete",
        ]
        if not args.request_parse:
            command.append("--no-request-parse")
        if args.all_purchases:
            command.append("--all-purchases")
        print(f"\n正在处理{label}的一局：{match_id}")
        result = subprocess.run(command, check=False)
        artifact_stem = build_match_artifact_stem(
            match,
            heroes,
            focus_account_id=account_id,
            focus_player_slot=int(match.get("player_slot") or 0),
        )
        match_dir = output_dir / artifact_stem
        bundle_path = match_dir / f"{artifact_stem}_GPT复盘包.md"
        if result.returncode == 0 and bundle_path.exists():
            completed.append((label, match, bundle_path))
        else:
            print(f"比赛 {match_id} 处理失败，退出代码 {result.returncode}。", file=sys.stderr)

    if len(completed) != len(selected):
        print(
            "代表比赛未全部取得完整解析数据，本次不会生成或发送残缺的每日数据包。",
            file=sys.stderr,
        )
        try:
            removed, reclaimed = delete_generated_tree(
                output_dir, script_dir / "reports" / "daily"
            )
            if removed:
                print(
                    f"已删除本次不完整的临时文件 {removed} 个，"
                    f"释放 {format_byte_size(reclaimed)}。"
                )
        except (OSError, ValueError) as exc:
            print(f"不完整临时文件清理失败：{exc}", file=sys.stderr)
        if not getattr(args, "no_telegram", False):
            notify_telegram_if_configured(
                telegram_settings_path,
                f"⏳ {target_date.isoformat()} 的代表局尚未全部解析完整，"
                "本次没有发送复盘文件；下次运行会重新检查。",
            )
        return 7

    summary_lines = [
        f"# {target_date.isoformat()} · Dota 2 每日双局复盘",
        "",
        f"当天共检测到 **{len(matches)}** 场公开比赛，按胜负、KDA、GPM、XPM、"
        "英雄伤害与建筑伤害综合选取代表局。",
        "",
    ]
    summary_lines.extend(
        match_selection_summary(label, match, hero_by_id)
        for label, match, _ in completed
    )
    summary_lines += [
        "",
        "请分别复盘这两场，重点比较：表现好时哪些决策值得保留，表现差时从哪个节点开始失控；"
        "最后给出下一次排位最需要执行的三条改进动作。",
        "",
    ]
    summary_path = output_dir / f"daily_{target_date.isoformat()}_summary.md"
    combined_path = output_dir / f"daily_{target_date.isoformat()}_chatgpt_bundle.md"
    combined_lines = list(summary_lines)
    for label, match, bundle_path in completed:
        combined_lines += [
            "",
            "---",
            "",
            f"# {label} · Match {match.get('match_id')}",
            "",
            bundle_path.read_text(encoding="utf-8-sig"),
        ]
    try:
        summary_path.write_text("\n".join(summary_lines), encoding="utf-8-sig", newline="\n")
        combined_path.write_text("\n".join(combined_lines), encoding="utf-8-sig", newline="\n")
    except OSError as exc:
        print(f"每日复盘包写入失败：{exc}", file=sys.stderr)
        return 5

    print(f"\n每日复盘摘要已生成：{summary_path}")
    print(f"每日双局 ChatGPT 数据包已生成：{combined_path}")
    sent_to_telegram = False
    if not getattr(args, "no_telegram", False):
        message_lines = [
            f"🎮 {target_date.isoformat()} Dota 2 每日复盘",
            f"当天比赛：{len(matches)} 场",
            "",
        ]
        message_lines.extend(
            telegram_plain_text(
                match_selection_summary(label, match, hero_by_id).removeprefix("- ")
            )
            for label, match, _ in completed
        )
        documents: list[tuple[Path, str]] = []
        for label, match, bundle_path in completed:
            report_paths = sorted(bundle_path.parent.glob("*_复盘摘要.md"))
            if report_paths:
                documents.append(
                    (
                        report_paths[0],
                        telegram_match_caption(target_date, label, match, hero_by_id),
                    )
                )
        documents.append((combined_path, "待 GPT 分析的完整数据包｜这不是 GPT 最终复盘结果"))
        sent = notify_telegram_if_configured(
            telegram_settings_path,
            "\n".join(message_lines),
            documents=documents,
        )
        if sent:
            try:
                mark_daily_sent(daily_state_path, target_date, completed)
                sent_to_telegram = True
            except (OSError, ValueError) as exc:
                print(f"发送成功，但已发送状态保存失败，将保留本地文件：{exc}", file=sys.stderr)
    if not args.no_open_project:
        project_url = load_saved_project_url(settings_path)
        open_chatgpt_handoff(combined_path, project_url)
        if project_url:
            print("已打开“Dota2复盘”项目和数据包位置，请把每日数据包拖入项目聊天并发送。")
        else:
            print("已打开数据包位置；先在普通模式保存项目链接，即可同时自动打开项目。")
    if sent_to_telegram:
        try:
            removed, reclaimed = delete_generated_tree(
                output_dir, script_dir / "reports" / "daily"
            )
            print(
                f"Telegram 已确认接收，已删除本次本地临时文件 {removed} 个，"
                f"释放 {format_byte_size(reclaimed)}。"
            )
        except (OSError, ValueError) as exc:
            print(f"发送成功，但本地临时文件清理失败：{exc}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="用 OpenDota 公开 API 生成 Dota 2 比赛 Markdown 复盘摘要。"
    )
    parser.add_argument("match_id", nargs="?", help="Dota 2 比赛编号（Match ID）")
    parser.add_argument(
        "--steam",
        metavar="标识",
        help="绑定并查询自己的最近比赛；可填好友代码、SteamID64 或数字版个人资料链接",
    )
    parser.add_argument(
        "--set-steam",
        metavar="标识",
        help="只保存 Steam/Dota 标识后退出，适合在软路由或无交互终端上首次配置",
    )
    parser.add_argument(
        "--my-matches", action="store_true", help="使用已保存的 Steam 标识查看最近比赛"
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="自动选取指定日期表现最好和最差的比赛并生成每日双局复盘包",
    )
    parser.add_argument(
        "--day-offset",
        type=int,
        default=1,
        metavar="天数",
        help="每日模式处理几天前的比赛；0 为今天，默认 1（昨天）",
    )
    parser.add_argument(
        "--forget-steam", action="store_true", help="清除本机保存的 Steam 标识后退出"
    )
    parser.add_argument(
        "--project-url",
        metavar="链接",
        help="保存并打开 ChatGPT“Dota2复盘”项目页面链接",
    )
    parser.add_argument(
        "--forget-project", action="store_true", help="清除保存的 ChatGPT 项目链接后退出"
    )
    parser.add_argument(
        "--setup-telegram",
        action="store_true",
        help="交互式连接 Telegram 机器人并自动识别接收私聊",
    )
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="向已连接的 Telegram 私聊发送一条测试消息后退出",
    )
    parser.add_argument(
        "--forget-telegram",
        action="store_true",
        help="删除本机保存的 Telegram Bot Token 和接收人后退出",
    )
    parser.add_argument(
        "-o", "--output", type=Path, help="自定义复盘摘要路径；默认按日期、英雄、KDA创建独立文件夹"
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        help="比赛独立文件夹的根目录；默认使用工具目录下的 reports",
    )
    parser.add_argument(
        "--request-parse",
        dest="request_parse",
        action="store_true",
        help="自动申请并等待 OpenDota 解析（默认启用，保留此参数用于兼容旧版本）",
    )
    parser.add_argument(
        "--no-request-parse",
        dest="request_parse",
        action="store_false",
        help="本次不申请解析，直接使用 OpenDota 当前已有数据",
    )
    parser.add_argument(
        "--parse-timeout",
        type=int,
        default=60,
        metavar="分钟",
        help="等待 OpenDota 解析的最长时间，默认 60 分钟",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="经济曲线、购买和死亡时间线不完整时不写文件也不发送（每日模式自动启用）",
    )
    parser.add_argument(
        "--no-open-project",
        action="store_true",
        help="生成复盘数据包后不自动打开 ChatGPT 项目页面和文件位置",
    )
    parser.add_argument(
        "--send-telegram",
        action="store_true",
        help="单场模式生成完成后把自动复盘摘要和 GPT 数据包发送到 Telegram",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="本次每日任务不发送 Telegram 通知或复盘文件",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        metavar="天数",
        help="每日任务保留原始数据、GPT数据包和日志的天数，默认 30 天",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="本次每日任务不执行旧数据清理",
    )
    parser.add_argument(
        "--cleanup-only",
        action="store_true",
        help="只清理过期的大文件和日志，不查询比赛",
    )
    parser.add_argument(
        "--cleanup-dry-run",
        action="store_true",
        help="与 --cleanup-only 配合，仅显示预计清理数量，不删除文件",
    )
    parser.add_argument(
        "--purge-generated-data",
        action="store_true",
        help="立即删除工具 reports、daily_logs 和 .cache 中的全部生成数据",
    )
    parser.add_argument(
        "--all-purchases", action="store_true", help="在报告中列出所有购买记录，包括消耗品"
    )
    parser.add_argument("--version", action="version", version=APP_VERSION)
    parser.set_defaults(request_parse=True)
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    script_dir = Path(__file__).resolve().parent
    settings_path = script_dir / "settings.json"
    telegram_settings_path = script_dir / "telegram_settings.json"

    if not 1 <= args.parse_timeout <= 60:
        print("输入错误：解析等待时间应为 1 到 60 分钟。", file=sys.stderr)
        return 2
    if not 0 <= args.day_offset <= 30:
        print("输入错误：每日复盘日期偏移应为 0 到 30 天。", file=sys.stderr)
        return 2
    if not 1 <= args.retention_days <= 3650:
        print("输入错误：数据保留时间应为 1 到 3650 天。", file=sys.stderr)
        return 2
    if args.cleanup_dry_run and not args.cleanup_only:
        print("输入错误：--cleanup-dry-run 需要和 --cleanup-only 一起使用。", file=sys.stderr)
        return 2
    if args.purge_generated_data:
        try:
            result = purge_generated_data(script_dir)
            print(
                f"已删除全部本地生成数据：{result['removed_files']} 个文件，"
                f"释放 {format_byte_size(result['reclaimed_bytes'])}。"
            )
            print("Steam、Telegram 设置和防重复发送记录均已保留。")
            return 0
        except OSError as exc:
            print(f"清理全部生成数据失败：{exc}", file=sys.stderr)
            return 5
    if args.cleanup_only:
        if args.no_cleanup:
            print("输入错误：--cleanup-only 不能和 --no-cleanup 同时使用。", file=sys.stderr)
            return 2
        result = cleanup_old_downloads(
            script_dir,
            retention_days=args.retention_days,
            dry_run=args.cleanup_dry_run,
        )
        print_cleanup_result(result)
        return 5 if result.get("errors") else 0

    if args.setup_telegram:
        return setup_telegram(telegram_settings_path)

    if args.test_telegram:
        config = load_telegram_settings(telegram_settings_path)
        if config is None:
            print("尚未设置 Telegram，请先运行 --setup-telegram。", file=sys.stderr)
            return 2
        try:
            telegram_send_message(config, "✅ Dota 2 复盘工具 Telegram 测试成功。")
            print("Telegram 测试消息已发送。")
            return 0
        except TelegramError as exc:
            print(f"Telegram 测试失败：{exc}", file=sys.stderr)
            return 6

    if args.forget_telegram:
        try:
            if telegram_settings_path.exists():
                telegram_settings_path.unlink()
                print("已删除 Telegram 推送设置。")
            else:
                print("当前没有保存 Telegram 推送设置。")
            return 0
        except OSError as exc:
            print(f"删除 Telegram 设置失败：{exc}", file=sys.stderr)
            return 5

    if args.set_steam:
        if args.match_id or args.steam or args.my_matches or args.daily:
            print("输入错误：--set-steam 不能和比赛查询或每日模式同时使用。", file=sys.stderr)
            return 2
        try:
            account_id = resolve_steam_account_id(args.set_steam)
            save_account_id(settings_path, account_id)
            print(f"已保存 Dota 好友代码：{account_id}")
            return 0
        except ValueError as exc:
            print(f"输入错误：{exc}", file=sys.stderr)
            return 2
        except OSError as exc:
            print(f"设置保存失败：{exc}", file=sys.stderr)
            return 5

    if args.forget_steam or args.forget_project:
        try:
            settings = load_settings(settings_path)
            key = "account_id" if args.forget_steam else "chatgpt_project_url"
            label = "Steam 标识" if args.forget_steam else "ChatGPT 项目链接"
            if key in settings:
                settings.pop(key)
                if settings:
                    save_settings(settings_path, settings)
                elif settings_path.exists():
                    settings_path.unlink()
                print(f"已清除本机保存的{label}。")
            else:
                print(f"当前没有保存{label}。")
            return 0
        except OSError as exc:
            print(f"清除设置失败：{exc}", file=sys.stderr)
            return 5

    if args.project_url:
        try:
            save_project_url(settings_path, args.project_url)
            print("已保存 ChatGPT 项目页面链接。")
        except ValueError as exc:
            print(f"输入错误：{exc}", file=sys.stderr)
            return 2
        except OSError as exc:
            print(f"设置保存失败：{exc}", file=sys.stderr)
            return 5

    if args.daily:
        if args.match_id or args.steam or args.my_matches:
            print("输入错误：每日模式不能同时指定 Match ID 或最近比赛模式。", file=sys.stderr)
            return 2
        return run_daily_review(
            args,
            script_dir=script_dir,
            settings_path=settings_path,
        )

    if args.match_id and (args.steam or args.my_matches):
        print("输入错误：Match ID 模式和我的最近比赛模式不能同时使用。", file=sys.stderr)
        return 2

    raw_match_id = args.match_id
    recent_mode = bool(args.steam or args.my_matches)
    if not raw_match_id and not recent_mode:
        print("请选择使用方式：")
        print("  1. 输入 Match ID 生成复盘")
        print("  2. 查看我的 Steam 最近比赛")
        try:
            choice = input("请输入 1 或 2：").strip()
            if choice == "1":
                raw_match_id = input("请输入 Match ID（例如 8943397976）：").strip()
            elif choice == "2":
                recent_mode = True
            else:
                print("输入错误：请选择 1 或 2。", file=sys.stderr)
                return 2
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。", file=sys.stderr)
            return 130

    focus_account_id: int | None = (
        load_saved_account_id(settings_path) if not recent_mode else None
    )
    focus_player_slot: int | None = None
    cache_dir = script_dir / ".cache"

    if recent_mode:
        try:
            if args.steam:
                focus_account_id = resolve_steam_account_id(args.steam)
                save_account_id(settings_path, focus_account_id)
                print(f"已在本机保存 Dota 好友代码：{focus_account_id}")
            else:
                focus_account_id = load_saved_account_id(settings_path)

            if focus_account_id is None:
                steam_raw = input(
                    "请输入 Dota 2 好友代码、SteamID64 或数字版个人资料链接："
                ).strip()
                focus_account_id = resolve_steam_account_id(steam_raw)
                remember = input("是否在本机记住它？[Y/n]：").strip().lower()
                if remember in {"", "y", "yes"}:
                    save_account_id(settings_path, focus_account_id)
                    print("已保存；下次可直接选择“我的 Steam 最近比赛”。")

            print(f"正在读取好友代码 {focus_account_id} 的最近比赛 …")
            recent_matches = fetch_recent_matches(focus_account_id)
            if not recent_matches:
                raise OpenDotaError(
                    "没有取得最近比赛。请确认 Dota 2 的“公开比赛数据”已开启，"
                    "并检查好友代码是否正确。"
                )
            heroes = load_constant("heroes", cache_dir)
            hero_by_id, _ = make_hero_maps(heroes)
            print_recent_matches(recent_matches, hero_by_id)
            selected = choose_recent_match(recent_matches)
            raw_match_id = str(selected.get("match_id") or "")
            focus_player_slot = int(selected.get("player_slot") or 0)
        except ValueError as exc:
            print(f"输入错误：{exc}", file=sys.stderr)
            return 2
        except OpenDotaError as exc:
            print(f"请求失败：{exc}", file=sys.stderr)
            return 4
        except OSError as exc:
            print(f"设置保存失败：{exc}", file=sys.stderr)
            return 5
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。", file=sys.stderr)
            return 130

    try:
        match_id = validate_match_id(raw_match_id or "")
    except ValueError as exc:
        print(f"输入错误：{exc}", file=sys.stderr)
        return 2

    requested_output_path = args.output
    if requested_output_path is not None and not requested_output_path.is_absolute():
        requested_output_path = Path.cwd() / requested_output_path
    output_root = args.output_root or (script_dir / "reports")
    if not output_root.is_absolute():
        output_root = Path.cwd() / output_root

    try:
        print(f"正在读取比赛 {match_id} …")
        match = request_json(f"/matches/{match_id}")
        if not isinstance(match, dict) or not isinstance(match.get("players"), list):
            raise OpenDotaError("OpenDota 返回的比赛数据不完整，缺少玩家列表。")

        parsed, _ = parsed_sections(match)
        parse_attempted = False
        parse_completed = parsed
        if not parsed and args.request_parse:
            parse_attempted = True
            print(
                "OpenDota 数据尚未完整，正在自动提交 Request Parse "
                "（该操作按 10 次请求计入限流）…"
            )
            try:
                job_id = request_parse(match_id)
                if job_id:
                    print(f"解析任务已提交，Job ID：{job_id}")
                else:
                    print("解析任务已提交，OpenDota 未返回 Job ID。")
                print(
                    f"最长等待 {args.parse_timeout} 分钟，"
                    f"每 {PARSE_POLL_INTERVAL_SECONDS // 60} 分钟检查一次 …"
                )
                refreshed, parse_completed = wait_for_parse(
                    match_id,
                    timeout_seconds=args.parse_timeout * 60,
                )
                if refreshed and isinstance(refreshed.get("players"), list):
                    match = refreshed
            except OpenDotaError as exc:
                print(f"解析请求未完成：{exc}。将先保存当前已有数据。")
        elif parsed:
            print("这场比赛已有完整解析数据，无需重复申请。")

        final_parsed, final_missing = parsed_sections(match)
        if (args.require_complete or args.send_telegram) and not final_parsed:
            missing_text = "、".join(final_missing) or "解析版本"
            print(
                f"OpenDota 数据仍不完整（缺少：{missing_text}）。"
                "本次不会写入或发送残缺复盘；稍后重新运行会继续检查。",
                file=sys.stderr,
            )
            return 7

        print("正在读取英雄与物品名称 …")
        heroes = load_constant("heroes", cache_dir)
        items = load_constant("items", cache_dir)
        artifact_stem = build_match_artifact_stem(
            match,
            heroes,
            focus_account_id=focus_account_id,
            focus_player_slot=focus_player_slot,
        )
        if requested_output_path is not None:
            output_path = requested_output_path
            artifact_dir = output_path.parent
        else:
            artifact_dir = output_root / artifact_stem
            output_path = artifact_dir / f"{artifact_stem}_复盘摘要.md"
        raw_path = artifact_dir / f"{artifact_stem}_OpenDota原始数据.json"
        bundle_path = artifact_dir / f"{artifact_stem}_GPT复盘包.md"
        report, missing = generate_report(
            match,
            heroes,
            items,
            include_all_purchases=args.all_purchases,
            focus_account_id=focus_account_id,
            focus_player_slot=focus_player_slot,
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8-sig", newline="\n")
        raw_path.write_text(
            json.dumps(match, ensure_ascii=False, indent=2),
            encoding="utf-8-sig",
            newline="\n",
        )
        bundle = make_chatgpt_bundle(
            match,
            report,
            focus_account_id=focus_account_id,
        )
        bundle_path.write_text(bundle, encoding="utf-8-sig", newline="\n")
        print(f"复盘摘要已生成：{output_path}")
        print(f"OpenDota 原始数据已下载：{raw_path}")
        print(f"ChatGPT 复盘数据包已生成：{bundle_path}")

        if missing:
            print(f"注意：缺少 {'、'.join(missing)}。")
            if parse_attempted and not parse_completed:
                print("等待时间内尚未解析完成。稍后重新运行，工具会再次检查并下载最新结果。")
            else:
                print("本次已关闭自动解析；去掉 --no-request-parse 后重新运行即可。")
        elif parse_attempted and parse_completed:
            print("OpenDota 解析已完成，数据包使用的是最新解析结果。")

        sent_to_telegram = False
        if args.send_telegram and not args.no_telegram:
            config = load_telegram_settings(telegram_settings_path)
            if config is None:
                print(
                    "尚未设置 Telegram；请先运行 python3 dota2_review.py --setup-telegram。",
                    file=sys.stderr,
                )
                return 2
            try:
                telegram_send_message(
                    config,
                    f"🎮 Dota 2 单场复盘已生成\nMatch ID：{match_id}\n文件将依次发送。",
                )
                telegram_send_document(config, output_path, caption=f"Match {match_id} · 自动复盘摘要")
                telegram_send_document(
                    config,
                    bundle_path,
                    caption=f"Match {match_id} · 待 GPT 分析的完整数据包（不是 GPT 最终复盘）",
                )
                print("Telegram 复盘文件已发送。")
                sent_to_telegram = True
            except TelegramError as exc:
                print(f"Telegram 推送失败：{exc}", file=sys.stderr)

        if not args.no_open_project:
            project_url = load_saved_project_url(settings_path)
            if project_url is None and sys.stdin.isatty():
                try:
                    raw_url = input(
                        "首次使用：请打开 ChatGPT 的“Dota2复盘”项目，复制页面链接并粘贴到这里"
                        "（直接回车可跳过）："
                    ).strip()
                    if raw_url:
                        project_url = validate_chatgpt_project_url(raw_url)
                        save_project_url(settings_path, project_url)
                        print("项目链接已保存，以后会自动打开。")
                except ValueError as exc:
                    print(f"项目链接未保存：{exc}")
                except (EOFError, KeyboardInterrupt):
                    print("\n已跳过打开项目。")
            open_chatgpt_handoff(bundle_path, project_url)
            if project_url:
                print("项目页面和数据包位置已打开，请把复盘数据包拖进项目聊天并发送。")
            else:
                print("数据包位置已打开；设置项目链接后可同时自动打开项目页面。")
        if sent_to_telegram:
            removed, reclaimed = delete_generated_files(
                (output_path, raw_path, bundle_path)
            )
            try:
                if artifact_dir.is_dir() and not any(artifact_dir.iterdir()):
                    artifact_dir.rmdir()
            except OSError:
                pass
            print(
                f"Telegram 已确认接收，已删除本地文件 {removed} 个，"
                f"释放 {format_byte_size(reclaimed)}。"
            )
        return 0
    except MatchNotFound as exc:
        print(f"未找到比赛：{exc}", file=sys.stderr)
        return 3
    except OpenDotaError as exc:
        print(f"请求失败：{exc}", file=sys.stderr)
        return 4
    except OSError as exc:
        print(f"文件写入失败：{exc}", file=sys.stderr)
        return 5
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(run())
