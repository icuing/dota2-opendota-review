#!/usr/bin/env python3
"""Windows desktop interface for Dota 2 OpenDota Review."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import END, BOTH, LEFT, RIGHT, X, Y, BooleanVar, IntVar, StringVar, Tk, Toplevel
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

import dota2_review as core


COLORS = {
    "bg": "#050504",
    "sidebar": "#0b0d0d",
    "nav": "#171a1d",
    "panel": "#15120d",
    "panel_alt": "#241d12",
    "panel_hover": "#332817",
    "border": "#514326",
    "border_gold": "#8d7135",
    "text": "#eee5cf",
    "muted": "#aaa189",
    "cyan": "#79a9bd",
    "green": "#77a56d",
    "green_dark": "#41633c",
    "green_bright": "#86bd75",
    "orange": "#c7953f",
    "gold": "#d3ad58",
    "gold_bright": "#f0ca70",
    "red": "#9f3e2d",
    "red_bright": "#d45a3f",
    "violet": "#7d74aa",
}

AI_MODEL_OPTIONS = {
    "openai": (
        "gpt-5.6-terra",
        "gpt-5.6-sol",
        "gpt-5.5",
        "gpt-5.4",
    ),
    "deepseek": (
        "deepseek-v4-pro",
        "deepseek-reasoner",
        "deepseek-chat",
    ),
}
REASONING_EFFORT_OPTIONS = ("low", "medium", "high", "max")
WINDOWS_TASK_NAME = "Dota2 Review Coach Daily"
SCHEDULE_SETTINGS_FILE = "schedule_settings.json"
PC_SETTINGS_FILE = "pc_settings.json"


def model_options(provider: str) -> tuple[str, ...]:
    """Return selectable models while keeping the model box editable."""
    return AI_MODEL_OPTIONS.get(provider.strip().lower(), ())


def validate_schedule_time(value: str) -> str:
    try:
        parsed = datetime.strptime(value.strip(), "%H:%M")
    except ValueError as exc:
        raise ValueError("时间格式应为 HH:mm，例如 06:15 或 23:30。") from exc
    return parsed.strftime("%H:%M")


def load_schedule_settings(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return {"enabled": False, "time": "06:15"}
    enabled = bool(raw.get("enabled")) if isinstance(raw, dict) else False
    try:
        run_time = validate_schedule_time(str(raw.get("time") or "06:15"))
    except ValueError:
        run_time = "06:15"
    return {"enabled": enabled, "time": run_time}


def save_schedule_settings(path: Path, *, enabled: bool, run_time: str) -> None:
    path.write_text(
        json.dumps(
            {"enabled": bool(enabled), "time": validate_schedule_time(run_time)},
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def load_pc_settings(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    output_root = str(raw.get("output_root") or "").strip()
    return {"output_root": output_root} if output_root else {}


def save_pc_settings(path: Path, *, output_root: str) -> None:
    root = str(Path(output_root).expanduser().resolve())
    path.write_text(
        json.dumps({"output_root": root}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def hero_options() -> tuple[tuple[str, int], ...]:
    names = core.load_chinese_hero_names()
    return tuple(sorted(((name, hero_id) for hero_id, name in names.items()), key=lambda row: row[0]))


def build_hero_run_args(
    *,
    hero_id: int,
    history_count: int,
    compare_source: str,
    benchmark_count: int,
    output_root: str = "",
    enable_ai: bool = True,
) -> list[str]:
    if history_count not in core.HERO_HISTORY_COUNTS:
        raise ValueError("个人样本只能选择 3、5 或 10 局。")
    if benchmark_count not in core.HERO_BENCHMARK_COUNTS:
        raise ValueError("对比样本只能选择 3 或 5 局。")
    if compare_source not in {"self", "pro", "high_rank"}:
        raise ValueError("请选择有效的对比方式。")
    args = [
        "--hero-review",
        str(int(hero_id)),
        "--history-count",
        str(history_count),
        "--compare-source",
        compare_source,
        "--benchmark-count",
        str(benchmark_count),
        "--no-open-project",
    ]
    if output_root:
        args += ["--output-root", str(Path(output_root).expanduser().resolve())]
    if not enable_ai:
        args.append("--no-ai-review")
    return args


def scheduled_run_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}" --run-daily'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        located = shutil.which("pythonw.exe")
        pythonw = Path(located) if located else pythonw
    if not pythonw.exists():
        raise OSError(
            "源码版要静默定时运行必须安装 pythonw.exe；请使用官方 Python for Windows，"
            "或改用已打包的 Windows EXE。"
        )
    return f'"{pythonw}" "{Path(__file__).resolve()}" --run-daily'


def hidden_subprocess_kwargs() -> dict[str, int]:
    return {"creationflags": int(getattr(subprocess, "CREATE_NO_WINDOW", 0))}


def centered_dialog_geometry(
    parent_x: int,
    parent_y: int,
    parent_width: int,
    parent_height: int,
    width: int,
    height: int,
    screen_width: int,
    screen_height: int,
) -> str:
    x = max(0, min(parent_x + (parent_width - width) // 2, screen_width - width))
    y = max(0, min(parent_y + (parent_height - height) // 2, screen_height - height))
    return f"{width}x{height}+{x}+{y}"


def configure_windows_schedule(app_dir: Path, run_time: str, *, enabled: bool) -> None:
    if os.name != "nt":
        raise OSError("图形界面的定时任务功能仅适用于 Windows。")
    settings_path = app_dir / SCHEDULE_SETTINGS_FILE
    normalized = validate_schedule_time(run_time)
    if enabled:
        completed = subprocess.run(
            [
                "schtasks.exe",
                "/Create",
                "/F",
                "/TN",
                WINDOWS_TASK_NAME,
                "/SC",
                "DAILY",
                "/ST",
                normalized,
                "/TR",
                scheduled_run_command(),
            ],
            capture_output=True,
            text=True,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "创建任务失败").strip()
            raise OSError(detail)
        subprocess.run(
            ["schtasks.exe", "/Delete", "/F", "/TN", "Dota2 Daily Review"],
            capture_output=True,
            text=True,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        save_schedule_settings(settings_path, enabled=True, run_time=normalized)
        return

    for task_name in (WINDOWS_TASK_NAME, "Dota2 Daily Review"):
        completed = subprocess.run(
            ["schtasks.exe", "/Delete", "/F", "/TN", task_name],
            capture_output=True,
            text=True,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        if completed.returncode not in (0, 1):
            detail = (completed.stderr or completed.stdout or "删除任务失败").strip()
            raise OSError(detail)
    save_schedule_settings(settings_path, enabled=False, run_time=normalized)


def build_run_args(
    *,
    match_id: str = "",
    daily: bool = False,
    day_offset: int = 1,
    enable_ai: bool = True,
    enable_push: bool = True,
    output_root: str = "",
) -> list[str]:
    if daily:
        args = ["--daily", "--day-offset", str(day_offset)]
        if not enable_push:
            args += ["--no-wechat", "--no-telegram"]
    else:
        args = [str(core.validate_match_id(match_id))]
        if enable_push:
            args.append("--send-telegram")
    if not enable_ai:
        args.append("--no-ai-review")
    if output_root:
        args += ["--output-root", str(Path(output_root).expanduser().resolve())]
    args.append("--no-open-project")
    return args


def masked_status(configured: bool, detail: str) -> tuple[str, str]:
    return ("已连接", detail) if configured else ("待设置", "点击配置后即可使用")


class QueueWriter:
    def __init__(self, events: queue.Queue[tuple[str, object]]) -> None:
        self.events = events

    def write(self, value: str) -> int:
        if value:
            self.events.put(("log", value))
        return len(value)

    def flush(self) -> None:
        return None


class ReviewApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.app_dir = core.application_dir()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.running = False
        self.page_name = "review"
        self.pages: dict[str, tk.Frame] = {}
        self.nav_buttons: dict[str, tk.Button] = {}
        self.hero_rows = hero_options()
        self.hero_name_to_id = {name: hero_id for name, hero_id in self.hero_rows}
        pc_settings = load_pc_settings(self.app_dir / PC_SETTINGS_FILE)
        self.output_root_var = StringVar(
            value=pc_settings.get("output_root", str(self.app_dir / "reports"))
        )

        root.title(f"Dota 2 复盘教练 · v{core.APP_VERSION}")
        root.geometry("1760x960")
        root.minsize(1120, 720)
        root.configure(bg=COLORS["bg"])
        self._configure_styles()
        self._build_shell()
        self.show_page("review")
        self.refresh_status()
        self.root.after(100, self._drain_events)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Coach.TEntry",
            fieldbackground=COLORS["panel_alt"],
            foreground=COLORS["text"],
            insertcolor=COLORS["text"],
            bordercolor=COLORS["border"],
            padding=10,
        )
        style.configure(
            "Coach.TCombobox",
            fieldbackground=COLORS["panel_alt"],
            background=COLORS["panel_alt"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["gold_bright"],
            bordercolor=COLORS["border_gold"],
            lightcolor=COLORS["border_gold"],
            darkcolor=COLORS["border_gold"],
            selectbackground=COLORS["panel_hover"],
            selectforeground=COLORS["text"],
            padding=8,
        )
        style.map(
            "Coach.TCombobox",
            fieldbackground=[
                ("readonly", COLORS["panel_alt"]),
                ("disabled", COLORS["panel_alt"]),
            ],
            background=[
                ("readonly", COLORS["panel_alt"]),
                ("active", COLORS["panel_hover"]),
            ],
            foreground=[
                ("readonly", COLORS["text"]),
                ("disabled", COLORS["muted"]),
            ],
            arrowcolor=[
                ("readonly", COLORS["gold_bright"]),
                ("active", COLORS["gold_bright"]),
            ],
            bordercolor=[
                ("focus", COLORS["gold_bright"]),
                ("readonly", COLORS["border_gold"]),
            ],
        )
        self.root.option_add("*TCombobox*Listbox.background", COLORS["panel_alt"])
        self.root.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", COLORS["green_dark"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", COLORS["text"])
        style.configure(
            "Coach.TCheckbutton",
            background=COLORS["panel"],
            foreground=COLORS["text"],
            font=("Microsoft YaHei UI", 10),
        )
        style.map("Coach.TCheckbutton", background=[("active", COLORS["panel"])])
        style.configure(
            "Coach.Horizontal.TProgressbar",
            background=COLORS["green_bright"],
            troughcolor=COLORS["panel_alt"],
            bordercolor=COLORS["panel_alt"],
        )

    def _build_shell(self) -> None:
        self.stage = tk.Canvas(
            self.root,
            bg=COLORS["bg"],
            highlightthickness=0,
            bd=0,
        )
        self.stage.pack(fill=BOTH, expand=True)
        assets = core.resource_dir() / "assets"
        self.background_image: tk.PhotoImage | None = None
        self.drow_image: tk.PhotoImage | None = None
        self.windranger_image: tk.PhotoImage | None = None
        try:
            self.background_image = tk.PhotoImage(file=str(assets / "dark-arena-background.png"))
            self.drow_image = tk.PhotoImage(file=str(assets / "drow-mascot-cutout.png")).subsample(3, 3)
            self.windranger_image = tk.PhotoImage(file=str(assets / "windranger-mascot-cutout.png")).subsample(3, 3)
        except tk.TclError:
            pass
        self.background_id = (
            self.stage.create_image(0, 0, image=self.background_image, anchor="center")
            if self.background_image
            else None
        )
        self.drow_id = (
            self.stage.create_image(0, 0, image=self.drow_image, anchor="sw")
            if self.drow_image
            else None
        )
        self.windranger_id = (
            self.stage.create_image(0, 0, image=self.windranger_image, anchor="se")
            if self.windranger_image
            else None
        )

        main = tk.Frame(
            self.stage,
            bg=COLORS["sidebar"],
            highlightbackground=COLORS["border_gold"],
            highlightthickness=1,
        )
        self.shell_window = self.stage.create_window(0, 18, window=main, anchor="n")
        self.stage.bind("<Configure>", self._layout_stage)

        topbar = tk.Frame(main, bg=COLORS["nav"], height=104)
        topbar.pack(fill=X)
        topbar.pack_propagate(False)
        brand = tk.Frame(topbar, bg=COLORS["nav"])
        brand.pack(side=LEFT, padx=(26, 24), pady=18)
        tk.Label(
            brand,
            text="D2",
            bg=COLORS["orange"],
            fg=COLORS["text"],
            font=("Segoe UI", 16, "bold"),
            width=3,
            height=1,
            highlightbackground=COLORS["gold_bright"],
            highlightthickness=1,
        ).pack(side=LEFT)
        brand_text = tk.Frame(brand, bg=COLORS["nav"])
        brand_text.pack(side=LEFT, padx=12)
        tk.Label(
            brand_text,
            text="复盘教练",
            bg=COLORS["nav"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(anchor="w")
        tk.Label(
            brand_text,
            text=f"OPEN DOTA · MAX · v{core.APP_VERSION}",
            bg=COLORS["nav"],
            fg=COLORS["gold"],
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")

        nav = tk.Frame(topbar, bg=COLORS["nav"])
        nav.pack(side=RIGHT, padx=24, pady=26)
        nav_items = [
            ("review", "战局复盘"),
            ("hero", "英雄训练"),
            ("settings", "连接设置"),
            ("logs", "战报日志"),
        ]
        for name, label in nav_items:
            button = tk.Button(
                nav,
                text=label,
                command=lambda page=name: self.show_page(page),
                bg=COLORS["nav"],
                fg=COLORS["muted"],
                activebackground=COLORS["panel_alt"],
                activeforeground=COLORS["text"],
                relief="flat",
                padx=18,
                pady=10,
                font=("Microsoft YaHei UI", 10, "bold"),
                cursor="hand2",
            )
            button.pack(side=LEFT, padx=3)
            self.nav_buttons[name] = button

        tk.Frame(main, bg=COLORS["gold"], height=2).pack(fill=X)
        header = tk.Frame(main, bg=COLORS["sidebar"], height=88)
        header.pack(fill=X, padx=30, pady=(18, 0))
        header.pack_propagate(False)
        self.title_label = tk.Label(
            header,
            text="开始复盘",
            bg=COLORS["sidebar"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 21, "bold"),
        )
        self.title_label.pack(anchor="w")
        self.subtitle_label = tk.Label(
            header,
            text="从公开比赛数据中提炼下一局能执行的改进动作",
            bg=COLORS["sidebar"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        )
        self.subtitle_label.pack(anchor="w", pady=(5, 0))

        self.content = tk.Frame(main, bg=COLORS["sidebar"])
        self.content.pack(fill=BOTH, expand=True, padx=30, pady=(0, 26))
        self._build_review_page()
        self._build_hero_page()
        self._build_settings_page()
        self._build_logs_page()

    def _layout_stage(self, event: tk.Event[tk.Misc]) -> None:
        width = max(1, int(event.width))
        height = max(1, int(event.height))
        if self.background_id is not None:
            self.stage.coords(self.background_id, width // 2, height // 2)
        if self.drow_id is not None:
            self.stage.coords(self.drow_id, 0, height)
        if self.windranger_id is not None:
            self.stage.coords(self.windranger_id, width, height)
        shell_width = max(860, min(1080, width - 700)) if width >= 1560 else width - 34
        self.stage.coords(self.shell_window, width // 2, 18)
        self.stage.itemconfigure(
            self.shell_window,
            width=max(860, shell_width),
            height=max(680, height - 36),
        )

    def _panel(self, parent: tk.Widget, *, padx: int = 22, pady: int = 20) -> tk.Frame:
        panel = tk.Frame(
            parent,
            bg=COLORS["panel"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        panel._coach_padx = padx  # type: ignore[attr-defined]
        panel._coach_pady = pady  # type: ignore[attr-defined]
        return panel

    def _build_review_page(self) -> None:
        page = tk.Frame(self.content, bg=COLORS["sidebar"])
        self.pages["review"] = page

        status_row = tk.Frame(page, bg=COLORS["sidebar"])
        status_row.pack(fill=X, pady=(0, 18))
        self.status_cards: dict[str, tuple[tk.Label, tk.Label]] = {}
        for index, (key, label) in enumerate(
            (("steam", "Dota 账号"), ("ai", "AI 教练"), ("wechat", "微信推送"))
        ):
            card = self._panel(status_row)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            status_row.grid_columnconfigure(index, weight=1)
            tk.Frame(
                card,
                bg=(COLORS["violet"], COLORS["red"], COLORS["green"])[index],
                height=3,
            ).pack(fill=X)
            tk.Label(
                card, text=label, bg=COLORS["panel"], fg=COLORS["muted"],
                font=("Microsoft YaHei UI", 9)
            ).pack(anchor="w", padx=16, pady=(13, 3))
            main_label = tk.Label(
                card, text="检查中", bg=COLORS["panel"], fg=COLORS["text"],
                font=("Microsoft YaHei UI", 12, "bold")
            )
            main_label.pack(anchor="w", padx=16)
            detail_label = tk.Label(
                card, text="", bg=COLORS["panel"], fg=COLORS["muted"],
                font=("Microsoft YaHei UI", 8)
            )
            detail_label.pack(anchor="w", padx=16, pady=(2, 13))
            self.status_cards[key] = (main_label, detail_label)

        radar = self._panel(page)
        radar.pack(fill=X, pady=(0, 18))
        radar_head = tk.Frame(radar, bg=COLORS["panel"])
        radar_head.pack(fill=X, padx=20, pady=(13, 8))
        tk.Label(
            radar_head,
            text="核心胜利条件追踪",
            bg=COLORS["panel"],
            fg=COLORS["orange"],
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(side=LEFT)
        tk.Label(
            radar_head,
            text="每场根据英雄职责动态判断",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(side=RIGHT)
        radar_row = tk.Frame(radar, bg=COLORS["panel"])
        radar_row.pack(fill=X, padx=20, pady=(0, 14))
        for index, (number, label) in enumerate(
            (("Ⅰ", "打钱节奏"), ("Ⅱ", "装备强势期"), ("Ⅲ", "团战切入"), ("Ⅳ", "技能释放"), ("Ⅴ", "地图转化"))
        ):
            chip = tk.Frame(
                radar_row,
                bg=COLORS["panel_alt"],
                highlightbackground=COLORS["border"],
                highlightthickness=1,
            )
            chip.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 5, 0))
            radar_row.grid_columnconfigure(index, weight=1)
            tk.Label(
                chip,
                text=number,
                bg=COLORS["panel_alt"],
                fg=COLORS["red_bright"],
                font=("Georgia", 11, "bold"),
            ).pack(side=LEFT, padx=(10, 6), pady=8)
            tk.Label(
                chip,
                text=label,
                bg=COLORS["panel_alt"],
                fg=COLORS["text"],
                font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(side=LEFT, pady=8)

        run_panel = self._panel(page)
        run_panel.pack(fill=X, pady=(0, 18))
        tk.Label(
            run_panel,
            text="单场 · MAX 教练复盘",
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(anchor="w", padx=22, pady=(19, 5))
        tk.Label(
            run_panel,
            text="输入比赛 Match ID，生成包含 C 位打钱、强势期、切入、技能与目标转化分析的中文复盘。",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", padx=22)
        input_row = tk.Frame(run_panel, bg=COLORS["panel"])
        input_row.pack(fill=X, padx=22, pady=(16, 14))
        self.match_var = StringVar()
        ttk.Entry(
            input_row,
            textvariable=self.match_var,
            style="Coach.TEntry",
            font=("Segoe UI", 12),
        ).pack(side=LEFT, fill=X, expand=True, padx=(0, 12))
        self.run_match_button = self._action_button(
            input_row, "生成复盘", self.run_single, primary=True, width=14
        )
        self.run_match_button.pack(side=RIGHT)

        option_row = tk.Frame(run_panel, bg=COLORS["panel"])
        option_row.pack(fill=X, padx=22, pady=(0, 18))
        self.enable_ai_var = BooleanVar(value=True)
        self.enable_push_var = BooleanVar(value=False)
        self._toggle_control(
            option_row, "启用付费 AI 深度复盘", self.enable_ai_var
        ).pack(side=LEFT)
        self._toggle_control(
            option_row, "完成后推送", self.enable_push_var
        ).pack(side=LEFT, padx=12)

        daily_panel = self._panel(page)
        daily_panel.pack(fill=X)
        top = tk.Frame(daily_panel, bg=COLORS["panel"])
        top.pack(fill=X, padx=22, pady=(18, 8))
        title_box = tk.Frame(top, bg=COLORS["panel"])
        title_box.pack(side=LEFT)
        tk.Label(
            title_box,
            text="每日代表局",
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="自动选择最好与最差表现，微信优先、Telegram 备用",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(4, 0))
        action_box = tk.Frame(top, bg=COLORS["panel"])
        action_box.pack(side=RIGHT)
        tk.Label(
            action_box, text="回看", bg=COLORS["panel"], fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9)
        ).pack(side=LEFT, padx=(0, 8))
        self.day_offset_var = IntVar(value=1)
        offset = ttk.Combobox(
            action_box,
            textvariable=self.day_offset_var,
            values=[0, 1, 2, 3, 7],
            width=4,
            state="readonly",
            style="Coach.TCombobox",
        )
        offset.pack(side=LEFT)
        tk.Label(
            action_box, text="天前", bg=COLORS["panel"], fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9)
        ).pack(side=LEFT, padx=(8, 14))
        self.run_daily_button = self._action_button(
            action_box, "运行每日复盘", self.run_daily, primary=False, width=14
        )
        self.run_daily_button.pack(side=LEFT)

        schedule_row = tk.Frame(daily_panel, bg=COLORS["panel_alt"])
        schedule_row.pack(fill=X, padx=22, pady=(6, 4))
        tk.Label(
            schedule_row,
            text="⏱",
            bg=COLORS["panel_alt"],
            fg=COLORS["gold_bright"],
            font=("Segoe UI Symbol", 13),
        ).pack(side=LEFT, padx=(12, 8), pady=8)
        self.schedule_status_label = tk.Label(
            schedule_row,
            text="正在检查 Windows 定时任务",
            bg=COLORS["panel_alt"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.schedule_status_label.pack(side=LEFT, pady=8)
        self._action_button(
            schedule_row,
            "设置 / 修改时间",
            self.configure_schedule,
            primary=False,
            width=15,
        ).pack(side=RIGHT, padx=8, pady=5)

        self.progress = ttk.Progressbar(
            daily_panel, mode="indeterminate", style="Coach.Horizontal.TProgressbar"
        )
        self.progress.pack(fill=X, padx=22, pady=(8, 16))

    def _build_hero_page(self) -> None:
        page = tk.Frame(self.content, bg=COLORS["sidebar"])
        self.pages["hero"] = page

        intro = self._panel(page)
        intro.pack(fill=X, pady=(0, 16))
        tk.Label(
            intro,
            text="选择英雄，建立可追踪的个人样本",
            bg=COLORS["panel"],
            fg=COLORS["gold_bright"],
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(anchor="w", padx=22, pady=(18, 6))
        tk.Label(
            intro,
            text=(
                "一次读取你使用该英雄的最近 3 / 5 / 10 局；可只分析个人趋势，"
                "也可加入最近 3 / 5 场职业比赛或高分路人局作为参考。AI 只调用一次完成综合复盘。"
            ),
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            wraplength=900,
            justify="left",
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", padx=22, pady=(0, 18))

        controls = self._panel(page)
        controls.pack(fill=X, pady=(0, 16))
        form = tk.Frame(controls, bg=COLORS["panel"])
        form.pack(fill=X, padx=22, pady=20)
        for column in range(4):
            form.grid_columnconfigure(column, weight=1 if column in (0, 2) else 0)

        hero_names = [name for name, _hero_id in self.hero_rows]
        self.hero_name_var = StringVar(value=hero_names[0] if hero_names else "")
        self.hero_history_count_var = IntVar(value=5)
        self.hero_compare_label_var = StringVar(value="仅分析个人近期趋势")
        self.hero_benchmark_count_var = IntVar(value=3)
        self.hero_ai_var = BooleanVar(value=True)

        def label(text: str, row: int, column: int) -> None:
            tk.Label(
                form,
                text=text,
                bg=COLORS["panel"],
                fg=COLORS["muted"],
                font=("Microsoft YaHei UI", 9, "bold"),
            ).grid(row=row, column=column, sticky="w", pady=(0, 6), padx=(0, 12))

        label("英雄  ▼", 0, 0)
        label("我的近期样本  ▼", 0, 2)
        ttk.Combobox(
            form,
            textvariable=self.hero_name_var,
            values=hero_names,
            state="readonly",
            style="Coach.TCombobox",
            width=28,
        ).grid(row=1, column=0, sticky="ew", padx=(0, 22))
        ttk.Combobox(
            form,
            textvariable=self.hero_history_count_var,
            values=list(core.HERO_HISTORY_COUNTS),
            state="readonly",
            style="Coach.TCombobox",
            width=8,
        ).grid(row=1, column=2, sticky="ew")

        label("对比方式  ▼", 2, 0)
        label("职业 / 高分样本  ▼", 2, 2)
        ttk.Combobox(
            form,
            textvariable=self.hero_compare_label_var,
            values=["仅分析个人近期趋势", "对比近期职业比赛", "对比近期高分路人局"],
            state="readonly",
            style="Coach.TCombobox",
            width=28,
        ).grid(row=3, column=0, sticky="ew", padx=(0, 22))
        ttk.Combobox(
            form,
            textvariable=self.hero_benchmark_count_var,
            values=list(core.HERO_BENCHMARK_COUNTS),
            state="readonly",
            style="Coach.TCombobox",
            width=8,
        ).grid(row=3, column=2, sticky="ew")

        storage = self._panel(page)
        storage.pack(fill=X)
        tk.Label(
            storage,
            text="复盘记录存放位置",
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(anchor="w", padx=22, pady=(18, 5))
        tk.Label(
            storage,
            text="选择后会保存设置；单场、每日和英雄专项复盘都会使用该目录。",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", padx=22)
        path_row = tk.Frame(storage, bg=COLORS["panel"])
        path_row.pack(fill=X, padx=22, pady=(14, 10))
        ttk.Entry(
            path_row,
            textvariable=self.output_root_var,
            style="Coach.TEntry",
            font=("Segoe UI", 10),
        ).pack(side=LEFT, fill=X, expand=True, padx=(0, 10))
        self._action_button(
            path_row, "选择文件夹", self.choose_output_root, primary=False, width=12
        ).pack(side=RIGHT)
        action_row = tk.Frame(storage, bg=COLORS["panel"])
        action_row.pack(fill=X, padx=22, pady=(0, 18))
        self._toggle_control(
            action_row, "启用付费 AI 综合复盘", self.hero_ai_var
        ).pack(side=LEFT)
        self.hero_run_button = self._action_button(
            action_row, "生成英雄专项复盘", self.run_hero_training_gui, primary=True, width=18
        )
        self.hero_run_button.pack(side=RIGHT)

    def _build_settings_page(self) -> None:
        page = tk.Frame(self.content, bg=COLORS["sidebar"])
        self.pages["settings"] = page
        grid = tk.Frame(page, bg=COLORS["sidebar"])
        grid.pack(fill=BOTH, expand=True)
        for column in range(2):
            grid.grid_columnconfigure(column, weight=1, uniform="settings")
        for row in range(2):
            grid.grid_rowconfigure(row, weight=1, uniform="settings")
        cards = [
            ("Dota / Steam", "绑定好友代码或 SteamID64，用于自动找到你的比赛。", self.configure_steam, COLORS["cyan"]),
            ("AI 教练", "选择 OpenAI 或 DeepSeek，自定义模型与推理强度。", self.configure_ai, COLORS["green"]),
            ("个人微信", "使用 Server酱 SendKey 接收每日最终复盘。", self.configure_wechat, COLORS["orange"]),
            ("Telegram 备用", "微信确认失败时，自动发送到 Telegram 私聊。", self.configure_telegram, COLORS["red"]),
        ]
        for index, (title, description, command, accent) in enumerate(cards):
            panel = self._panel(grid)
            panel.grid(row=index // 2, column=index % 2, sticky="nsew", padx=(0, 10) if index % 2 == 0 else (10, 0), pady=(0, 10) if index < 2 else (10, 0))
            tk.Frame(panel, bg=accent, height=4).pack(fill=X)
            tk.Label(
                panel, text=title, bg=COLORS["panel"], fg=COLORS["text"],
                font=("Microsoft YaHei UI", 14, "bold")
            ).pack(anchor="w", padx=20, pady=(20, 8))
            tk.Label(
                panel, text=description, bg=COLORS["panel"], fg=COLORS["muted"],
                justify="left", wraplength=330, font=("Microsoft YaHei UI", 9)
            ).pack(anchor="w", padx=20)
            self._action_button(panel, "打开设置", command, primary=False, width=12).pack(anchor="w", padx=20, pady=20)

    def _build_logs_page(self) -> None:
        page = tk.Frame(self.content, bg=COLORS["sidebar"])
        self.pages["logs"] = page
        toolbar = tk.Frame(page, bg=COLORS["sidebar"])
        toolbar.pack(fill=X, pady=(0, 12))
        self._action_button(toolbar, "打开报告目录", self.open_reports, primary=False, width=14).pack(side=LEFT)
        self._action_button(toolbar, "清空显示", self.clear_log, primary=False, width=11).pack(side=LEFT, padx=10)
        self.log_text = tk.Text(
            page,
            bg="#07101c",
            fg="#cde6f7",
            insertbackground=COLORS["text"],
            selectbackground=COLORS["border"],
            relief="flat",
            padx=18,
            pady=16,
            font=("Cascadia Mono", 10),
            wrap="word",
        )
        self.log_text.pack(fill=BOTH, expand=True)
        self._append_log(f"Dota 2 复盘教练 v{core.APP_VERSION} 已就绪。\n")

    def _action_button(
        self,
        parent: tk.Widget,
        text: str,
        command: object,
        *,
        primary: bool,
        width: int,
    ) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            width=width,
            bg=COLORS["green_dark"] if primary else COLORS["panel_alt"],
            fg=COLORS["text"],
            activebackground=COLORS["green_bright"] if primary else COLORS["panel_hover"],
            activeforeground=COLORS["text"],
            relief="flat",
            padx=12,
            pady=10,
            font=("Microsoft YaHei UI", 10, "bold"),
            cursor="hand2",
        )

    def _toggle_control(
        self,
        parent: tk.Widget,
        label: str,
        variable: BooleanVar,
    ) -> tk.Button:
        button = tk.Button(
            parent,
            relief="flat",
            bd=0,
            padx=10,
            pady=6,
            font=("Microsoft YaHei UI", 9, "bold"),
            cursor="hand2",
            anchor="w",
        )

        def refresh() -> None:
            enabled = bool(variable.get())
            button.configure(
                text=f"✔ {label}" if enabled else f"□ {label}",
                bg=COLORS["panel_alt"],
                fg=COLORS["green_bright"] if enabled else COLORS["muted"],
                activebackground=COLORS["panel_hover"],
                activeforeground=COLORS["green_bright"] if enabled else COLORS["text"],
            )

        def toggle() -> None:
            variable.set(not variable.get())
            refresh()

        button.configure(command=toggle)
        refresh()
        return button

    def _web_link(self, parent: tk.Widget, text: str, url: str) -> tk.Label:
        label = tk.Label(
            parent,
            text=text,
            bg=COLORS["panel"],
            fg=COLORS["cyan"],
            cursor="hand2",
            font=("Microsoft YaHei UI", 9, "underline"),
        )
        label.bind("<Button-1>", lambda _event: webbrowser.open(url))
        return label

    def show_page(self, name: str) -> None:
        for page in self.pages.values():
            page.pack_forget()
        self.pages[name].pack(fill=BOTH, expand=True)
        self.page_name = name
        titles = {
            "review": ("开始复盘", "从公开比赛数据中提炼下一局能执行的改进动作"),
            "hero": ("英雄专项训练", "复盘自己的近期样本，并与职业或高分同英雄对局进行对比"),
            "settings": ("连接与设置", "密钥仅保存在本机，不显示在日志与报告中"),
            "logs": ("运行日志", "查看解析、AI 复盘和推送进度"),
        }
        self.title_label.configure(text=titles[name][0])
        self.subtitle_label.configure(text=titles[name][1])
        for key, button in self.nav_buttons.items():
            button.configure(
                bg=COLORS["panel_alt"] if key == name else COLORS["nav"],
                fg=COLORS["gold_bright"] if key == name else COLORS["muted"],
            )

    def refresh_status(self) -> None:
        account = core.load_saved_account_id(self.app_dir / "settings.json")
        ai = core.load_ai_settings(self.app_dir / "ai_settings.json")
        wechat = core.load_serverchan_settings(self.app_dir / "serverchan_settings.json")
        values = {
            "steam": masked_status(account is not None, f"好友代码 {account}" if account else ""),
            "ai": masked_status(ai is not None, f"{core.ai_provider_name(ai or {})} · {(ai or {}).get('reasoning_effort', '')}" if ai else ""),
            "wechat": masked_status(wechat is not None, "Server酱主推送" if wechat else ""),
        }
        for key, (title, detail) in values.items():
            main_label, detail_label = self.status_cards[key]
            main_label.configure(text=title, fg=COLORS["green"] if title == "已连接" else COLORS["orange"])
            detail_label.configure(text=detail)
        self.refresh_schedule_status()

    def refresh_schedule_status(self) -> None:
        current = load_schedule_settings(self.app_dir / SCHEDULE_SETTINGS_FILE)
        enabled = bool(current.get("enabled"))
        run_time = str(current.get("time") or "06:15")
        self.schedule_status_label.configure(
            text=(
                f"✔ 已启用：每天 {run_time} 自动复盘前一天比赛"
                if enabled
                else f"□ 未启用自动复盘（建议时间 {run_time}）"
            ),
            fg=COLORS["green_bright"] if enabled else COLORS["muted"],
        )

    def _set_running(self, running: bool) -> None:
        self.running = running
        state = "disabled" if running else "normal"
        self.run_match_button.configure(state=state)
        self.run_daily_button.configure(state=state)
        self.hero_run_button.configure(state=state)
        if running:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _start_job(self, label: str, func: object) -> None:
        if self.running:
            messagebox.showinfo("任务运行中", "请等待当前任务完成。", parent=self.root)
            return
        self._set_running(True)
        self.show_page("logs")
        self._append_log(f"\n▶ {label}\n")

        def worker() -> None:
            writer = QueueWriter(self.events)
            try:
                with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                    result = func()  # type: ignore[operator]
                self.events.put(("done", int(result or 0)))
            except Exception:
                self.events.put(("log", traceback.format_exc()))
                self.events.put(("done", 1))

        threading.Thread(target=worker, daemon=True).start()

    def run_single(self) -> None:
        try:
            args = build_run_args(
                match_id=self.match_var.get(),
                enable_ai=self.enable_ai_var.get(),
                enable_push=self.enable_push_var.get(),
                output_root=self.output_root_var.get(),
            )
        except ValueError as exc:
            messagebox.showerror("Match ID 无效", str(exc), parent=self.root)
            return
        self._start_job("开始单场深度复盘", lambda: core.run(args))

    def run_daily(self) -> None:
        args = build_run_args(
            daily=True,
            day_offset=int(self.day_offset_var.get()),
            enable_ai=self.enable_ai_var.get(),
            enable_push=self.enable_push_var.get(),
            output_root=self.output_root_var.get(),
        )
        self._start_job("开始每日代表局复盘", lambda: core.run(args))

    def choose_output_root(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            title="选择复盘记录存放文件夹",
            initialdir=self.output_root_var.get() or str(self.app_dir),
            mustexist=False,
        )
        if not selected:
            return
        try:
            root = Path(selected).expanduser().resolve()
            root.mkdir(parents=True, exist_ok=True)
            save_pc_settings(self.app_dir / PC_SETTINGS_FILE, output_root=str(root))
            self.output_root_var.set(str(root))
        except OSError as exc:
            messagebox.showerror("保存目录失败", str(exc), parent=self.root)

    def run_hero_training_gui(self) -> None:
        hero_name_value = self.hero_name_var.get().strip()
        hero_id = self.hero_name_to_id.get(hero_name_value)
        if hero_id is None:
            messagebox.showerror("请选择英雄", "请从下拉列表中选择一个英雄。", parent=self.root)
            return
        source_map = {
            "仅分析个人近期趋势": "self",
            "对比近期职业比赛": "pro",
            "对比近期高分路人局": "high_rank",
        }
        try:
            output_root = self.output_root_var.get().strip()
            if output_root:
                Path(output_root).expanduser().resolve().mkdir(parents=True, exist_ok=True)
                save_pc_settings(self.app_dir / PC_SETTINGS_FILE, output_root=output_root)
            args = build_hero_run_args(
                hero_id=hero_id,
                history_count=int(self.hero_history_count_var.get()),
                compare_source=source_map[self.hero_compare_label_var.get()],
                benchmark_count=int(self.hero_benchmark_count_var.get()),
                output_root=output_root,
                enable_ai=self.hero_ai_var.get(),
            )
        except (KeyError, OSError, ValueError) as exc:
            messagebox.showerror("英雄专项设置无效", str(exc), parent=self.root)
            return
        self._start_job(f"开始 {hero_name_value} 英雄专项复盘", lambda: core.run(args))

    def configure_schedule(self) -> None:
        dialog, body = self._dialog("Windows 每日定时复盘", width=600, height=430)
        current = load_schedule_settings(self.app_dir / SCHEDULE_SETTINGS_FILE)
        current_time = str(current.get("time") or "06:15")
        current_hour, current_minute = current_time.split(":", 1)
        hour = StringVar(value=current_hour)
        minute = StringVar(value=current_minute)

        tk.Label(
            body,
            text="每日定时复盘",
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(anchor="w")
        tk.Label(
            body,
            text=(
                "选择每天运行时间；保存后可随时回来修改。定时任务会复盘前一个自然日，"
                "并沿用当前 AI、微信与 Telegram 设置。"
            ),
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            wraplength=520,
            justify="left",
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(5, 18))

        time_row = tk.Frame(body, bg=COLORS["panel"])
        time_row.pack(anchor="w")
        tk.Label(
            time_row,
            text="每天",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        ).pack(side=LEFT, padx=(0, 10))
        ttk.Combobox(
            time_row,
            textvariable=hour,
            values=[f"{value:02d}" for value in range(24)],
            width=5,
            state="readonly",
            style="Coach.TCombobox",
        ).pack(side=LEFT)
        tk.Label(
            time_row,
            text=":",
            bg=COLORS["panel"],
            fg=COLORS["gold_bright"],
            font=("Segoe UI", 14, "bold"),
        ).pack(side=LEFT, padx=6)
        ttk.Combobox(
            time_row,
            textvariable=minute,
            values=[f"{value:02d}" for value in range(60)],
            width=5,
            state="readonly",
            style="Coach.TCombobox",
        ).pack(side=LEFT)

        tk.Label(
            body,
            text=(
                "提示：请把 EXE 放在长期不移动的目录。电脑在计划时间需处于开机状态；"
                "修改时间会覆盖旧任务，不会重复创建。"
            ),
            bg=COLORS["panel_alt"],
            fg=COLORS["muted"],
            wraplength=500,
            justify="left",
            padx=14,
            pady=12,
            font=("Microsoft YaHei UI", 9),
        ).pack(fill=X, pady=(22, 12))

        action_row = tk.Frame(body, bg=COLORS["panel"])
        action_row.pack(fill=X, pady=(10, 0))

        def apply_schedule(enabled: bool) -> None:
            run_time = f"{hour.get()}:{minute.get()}"
            try:
                configure_windows_schedule(self.app_dir, run_time, enabled=enabled)
            except (OSError, ValueError) as exc:
                messagebox.showerror("定时任务设置失败", str(exc), parent=dialog)
                return
            dialog.destroy()
            self.refresh_schedule_status()
            messagebox.showinfo(
                "定时任务已更新",
                (
                    f"✔ 已设置为每天 {run_time} 自动复盘。"
                    if enabled
                    else "已停用 Windows 每日自动复盘。"
                ),
                parent=self.root,
            )

        self._action_button(
            action_row,
            "停用定时任务",
            lambda: apply_schedule(False),
            primary=False,
            width=14,
        ).pack(side=LEFT)
        self._action_button(
            action_row,
            "保存并启用",
            lambda: apply_schedule(True),
            primary=True,
            width=14,
        ).pack(side=RIGHT)

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "done":
                    self._set_running(False)
                    self.refresh_status()
                    code = int(payload)
                    self._append_log(f"\n■ 任务结束，退出代码 {code}。\n")
                    if code == 0:
                        messagebox.showinfo("复盘完成", "任务已完成，可在报告目录查看结果。", parent=self.root)
                    else:
                        messagebox.showerror("任务未完成", f"退出代码 {code}，请查看运行日志。", parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _append_log(self, text: str) -> None:
        self.log_text.insert(END, text)
        self.log_text.see(END)

    def clear_log(self) -> None:
        self.log_text.delete("1.0", END)

    def open_reports(self) -> None:
        reports = Path(self.output_root_var.get() or (self.app_dir / "reports")).expanduser().resolve()
        reports.mkdir(parents=True, exist_ok=True)
        webbrowser.open(reports.as_uri())

    def _dialog(self, title: str, width: int = 560, height: int = 430) -> tuple[Toplevel, tk.Frame]:
        dialog = Toplevel(self.root)
        dialog.withdraw()
        dialog.title(title)
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self.root)
        dialog.grab_set()
        body = tk.Frame(dialog, bg=COLORS["panel"], padx=24, pady=22)
        body.pack(fill=BOTH, expand=True, padx=18, pady=18)
        self.root.update_idletasks()
        dialog.geometry(
            centered_dialog_geometry(
                self.root.winfo_rootx(),
                self.root.winfo_rooty(),
                self.root.winfo_width(),
                self.root.winfo_height(),
                width,
                height,
                self.root.winfo_screenwidth(),
                self.root.winfo_screenheight(),
            )
        )
        dialog.deiconify()
        dialog.lift()
        dialog.focus_force()
        return dialog, body

    def _field(self, parent: tk.Widget, label: str, variable: StringVar, *, secret: bool = False) -> ttk.Entry:
        tk.Label(
            parent, text=label, bg=COLORS["panel"], fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9)
        ).pack(anchor="w", pady=(10, 5))
        entry = ttk.Entry(
            parent,
            textvariable=variable,
            show="●" if secret else "",
            style="Coach.TEntry",
            font=("Segoe UI", 10),
        )
        entry.pack(fill=X)
        return entry

    def _combo_field(
        self,
        parent: tk.Widget,
        label: str,
        variable: StringVar,
        values: tuple[str, ...] | list[str],
        *,
        readonly: bool,
    ) -> ttk.Combobox:
        tk.Label(
            parent,
            text=label,
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(10, 5))
        combo = ttk.Combobox(
            parent,
            textvariable=variable,
            values=list(values),
            state="readonly" if readonly else "normal",
            style="Coach.TCombobox",
            font=("Segoe UI", 10),
        )
        combo.pack(fill=X)
        return combo

    def configure_steam(self) -> None:
        dialog, body = self._dialog("绑定 Dota / Steam", height=280)
        current = core.load_saved_account_id(self.app_dir / "settings.json")
        value = StringVar(value=str(current or ""))
        tk.Label(
            body, text="绑定比赛账号", bg=COLORS["panel"], fg=COLORS["text"],
            font=("Microsoft YaHei UI", 15, "bold")
        ).pack(anchor="w")
        self._field(body, "好友代码、SteamID64 或数字资料链接", value)

        def save() -> None:
            try:
                account_id = core.resolve_steam_account_id(value.get())
                core.save_account_id(self.app_dir / "settings.json", account_id)
            except (ValueError, OSError) as exc:
                messagebox.showerror("保存失败", str(exc), parent=dialog)
                return
            dialog.destroy()
            self.refresh_status()

        self._action_button(body, "保存", save, primary=True, width=12).pack(anchor="e", pady=22)

    def configure_ai(self) -> None:
        dialog, body = self._dialog("AI 教练设置", height=650)
        current = core.load_ai_settings(self.app_dir / "ai_settings.json") or {}
        initial_provider = str(current.get("provider") or "deepseek").lower()
        if initial_provider not in core.AI_PROVIDER_DEFAULTS:
            initial_provider = "deepseek"
        provider = StringVar(value=initial_provider)
        model = StringVar(value=str(current.get("model") or core.AI_PROVIDER_DEFAULTS[initial_provider]["model"]))
        effort = StringVar(value=str(current.get("reasoning_effort") or "max"))
        api_key = StringVar()
        tk.Label(
            body, text="AI 教练设置", bg=COLORS["panel"], fg=COLORS["text"],
            font=("Microsoft YaHei UI", 15, "bold")
        ).pack(anchor="w")
        tk.Label(
            body, text="测试成功后才会保存。已有密钥不会显示。", bg=COLORS["panel"],
            fg=COLORS["muted"], font=("Microsoft YaHei UI", 9)
        ).pack(anchor="w", pady=(4, 8))
        provider_box = self._combo_field(
            body,
            "服务商  ▼",
            provider,
            ["openai", "deepseek"],
            readonly=True,
        )
        model_box = self._combo_field(
            body,
            "模型名称  ▼（可从列表选择，也可手动输入平台支持的模型）",
            model,
            model_options(initial_provider),
            readonly=False,
        )
        self._combo_field(
            body,
            "推理强度  ▼",
            effort,
            list(REASONING_EFFORT_OPTIONS),
            readonly=True,
        )
        self._field(body, "API Key（留空则保留现有密钥）", api_key, secret=True)

        link_row = tk.Frame(body, bg=COLORS["panel"])
        link_row.pack(fill=X, pady=(10, 0))
        self._web_link(
            link_row,
            "OpenAI API Key 页面 ↗",
            "https://platform.openai.com/api-keys",
        ).pack(side=LEFT)
        self._web_link(
            link_row,
            "DeepSeek API Key 页面 ↗",
            "https://platform.deepseek.com/api_keys",
        ).pack(side=LEFT, padx=22)

        def provider_changed(_event: object | None = None) -> None:
            selected = provider.get().strip().lower()
            options = model_options(selected)
            model_box.configure(values=list(options))
            if model.get().strip() not in options and options:
                model.set(core.AI_PROVIDER_DEFAULTS[selected]["model"])
            effort.set(core.AI_PROVIDER_DEFAULTS[selected]["reasoning_effort"])

        provider_box.bind("<<ComboboxSelected>>", provider_changed)

        def save() -> None:
            key = api_key.get().strip() or str(current.get("api_key") or "")
            config = {
                "provider": provider.get(),
                "model": model.get(),
                "reasoning_effort": effort.get(),
                "api_key": key,
            }
            try:
                clean = core.validate_ai_settings(config)
            except ValueError as exc:
                messagebox.showerror("设置错误", str(exc), parent=dialog)
                return

            def job() -> int:
                print("正在测试 AI 连接 …")
                result = core.request_ai_review(clean, "连接测试", test_mode=True)
                core.save_ai_settings(self.app_dir / "ai_settings.json", clean)
                print(f"AI 设置已保存：{core.ai_provider_name(clean)} / {clean['model']} / {clean['reasoning_effort']}；{result}")
                return 0

            dialog.destroy()
            self._start_job("测试并保存 AI 设置", job)

        self._action_button(body, "测试并保存", save, primary=True, width=14).pack(anchor="e", pady=22)

    def configure_wechat(self) -> None:
        dialog, body = self._dialog("个人微信推送", width=620, height=500)
        sendkey = StringVar()
        tk.Label(
            body, text="个人微信推送", bg=COLORS["panel"], fg=COLORS["text"],
            font=("Microsoft YaHei UI", 15, "bold")
        ).pack(anchor="w")
        tk.Label(
            body,
            text="使用 Server酱把最终复盘发送到个人微信；微信未确认成功时才使用 Telegram。",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            wraplength=530,
            justify="left",
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(4, 8))
        self._web_link(
            body,
            "打开 Server酱 SendKey 页面：https://sct.ftqq.com/  ↗",
            "https://sct.ftqq.com/",
        ).pack(anchor="w", pady=(2, 10))
        tk.Label(
            body,
            text=(
                "① 用微信扫码登录 Server酱\n"
                "② 在 SendKey 页面复制以 SCT 开头的密钥\n"
                "③ 粘贴到下方，点击“测试并保存”\n"
                "④ 微信收到测试消息后即配置完成"
            ),
            bg=COLORS["panel_alt"],
            fg=COLORS["text"],
            justify="left",
            anchor="w",
            padx=14,
            pady=12,
            font=("Microsoft YaHei UI", 9),
        ).pack(fill=X, pady=(0, 8))
        self._field(body, "SendKey", sendkey, secret=True)

        def save() -> None:
            try:
                key = core.validate_serverchan_sendkey(sendkey.get())
            except ValueError as exc:
                messagebox.showerror("设置错误", str(exc), parent=dialog)
                return

            def job() -> int:
                core.serverchan_send(
                    {"sendkey": key},
                    "Dota 2 复盘教练连接成功",
                    "Windows 图形界面的个人微信推送已连接。",
                )
                core.save_serverchan_settings(self.app_dir / "serverchan_settings.json", key)
                print("个人微信测试成功，SendKey 已隐藏保存。")
                return 0

            dialog.destroy()
            self._start_job("测试并保存个人微信推送", job)

        self._action_button(body, "测试并保存", save, primary=True, width=14).pack(anchor="e", pady=22)

    def configure_telegram(self) -> None:
        dialog, body = self._dialog("Telegram 备用推送", width=620, height=500)
        token = StringVar()
        tk.Label(
            body, text="Telegram 备用推送", bg=COLORS["panel"], fg=COLORS["text"],
            font=("Microsoft YaHei UI", 15, "bold")
        ).pack(anchor="w")
        tk.Label(
            body,
            text="先给机器人发送 /start，再点击连接。微信确认失败时才会使用此渠道。",
            bg=COLORS["panel"], fg=COLORS["muted"], wraplength=460,
            justify="left", font=("Microsoft YaHei UI", 9)
        ).pack(anchor="w", pady=(4, 8))
        self._web_link(
            body,
            "打开 Telegram 官方 BotFather：https://t.me/BotFather  ↗",
            "https://t.me/BotFather",
        ).pack(anchor="w", pady=(2, 10))
        tk.Label(
            body,
            text=(
                "① 在 BotFather 发送 /newbot 创建机器人\n"
                "② 复制机器人 Token 并粘贴到下方\n"
                "③ 先给新机器人发送 /start\n"
                "④ 点击“连接”，程序会自动识别你的私聊"
            ),
            bg=COLORS["panel_alt"],
            fg=COLORS["text"],
            justify="left",
            anchor="w",
            padx=14,
            pady=12,
            font=("Microsoft YaHei UI", 9),
        ).pack(fill=X, pady=(0, 8))
        self._field(body, "Bot Token", token, secret=True)

        def save() -> None:
            try:
                clean_token = core.validate_telegram_bot_token(token.get())
            except ValueError as exc:
                messagebox.showerror("设置错误", str(exc), parent=dialog)
                return

            def job() -> int:
                bot = core.telegram_api_request(clean_token, "getMe")
                updates = core.telegram_api_request(
                    clean_token,
                    "getUpdates",
                    fields={"timeout": 0, "limit": 100, "allowed_updates": '["message"]'},
                )
                latest = core.find_latest_private_chat(updates)
                if latest is None:
                    raise core.TelegramError("没有读取到私聊，请先给机器人发送 /start。")
                chat_id, display_name = latest
                username = str(bot.get("username") or "") if isinstance(bot, dict) else ""
                core.save_telegram_settings(
                    self.app_dir / "telegram_settings.json",
                    bot_token=clean_token,
                    chat_id=chat_id,
                    bot_username=username,
                )
                core.telegram_send_message(
                    core.load_telegram_settings(self.app_dir / "telegram_settings.json") or {},
                    "✅ Dota 2 复盘教练 Windows 推送已连接。",
                )
                print(f"Telegram 备用推送已连接：{display_name}")
                return 0

            dialog.destroy()
            self._start_job("连接 Telegram 备用推送", job)

        self._action_button(body, "连接", save, primary=True, width=12).pack(anchor="e", pady=22)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--run-daily", action="store_true")
    parser.add_argument("--day-offset", type=int, default=1)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.version:
        print(core.APP_VERSION)
        return 0
    if args.run_daily:
        log_dir = core.application_dir() / "daily_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "windows-scheduled.log"
        with log_path.open("a", encoding="utf-8") as stream:
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                print(f"\n[{datetime.now().isoformat(timespec='seconds')}] Windows 定时复盘开始")
                run_args = [
                    "--daily",
                    "--day-offset",
                    str(args.day_offset),
                    "--no-open-project",
                ]
                output_root = load_pc_settings(
                    core.application_dir() / PC_SETTINGS_FILE
                ).get("output_root")
                if output_root:
                    run_args += ["--output-root", output_root]
                result = core.run(run_args)
                print(f"Windows 定时复盘结束，退出代码 {result}")
                return result
    root = Tk()
    app = ReviewApp(root)
    if args.smoke_test:
        root.update_idletasks()
        root.update()
        assert app.pages["review"].winfo_exists()
        assert app.pages["hero"].winfo_exists()
        assert app.pages["settings"].winfo_exists()
        assert app.pages["logs"].winfo_exists()
        root.destroy()
        print(f"GUI smoke test passed: {core.APP_VERSION}")
        return 0
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
