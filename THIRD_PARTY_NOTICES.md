# 第三方项目与 AI 素材声明

本文件说明项目参考、调用或用于构建的第三方项目，以及仓库内 AI 生成视觉素材的来源。项目没有复制 Dota 2 客户端界面截图、Valve 官方英雄立绘或官方 UI 资源。

## 运行时与构建工具

| 项目 | 用途 | 是否随程序源代码修改后再发布 | 上游地址 |
| --- | --- | --- | --- |
| CPython / Python 标准库 | 命令行、Tkinter GUI、网络请求、JSON、线程和 Windows 任务调用 | 否；项目直接调用标准库 API | <https://github.com/python/cpython> |
| Tk / Tkinter | Windows 图形界面 | 否；随 Python 运行环境或 PyInstaller 包提供 | <https://www.tcl.tk/> |
| PyInstaller | 把 Windows GUI 构建成单文件 EXE；仅用于构建 | 否 | <https://github.com/pyinstaller/pyinstaller> |

项目运行时代码没有依赖 Requests、CustomTkinter、Qt、Electron 或其他第三方 Python GUI/HTTP 库。

## 数据与外部服务

| 服务 | 用途 | 地址 |
| --- | --- | --- |
| OpenDota | 公开比赛数据与 Parse 请求；项目的主要数据源 | <https://github.com/odota/core> / <https://docs.opendota.com/> |
| OpenAI API | 用户可选的 AI 教练复盘服务 | <https://platform.openai.com/docs/> |
| DeepSeek API | 用户可选的 AI 教练复盘服务 | <https://api-docs.deepseek.com/> |
| Server酱 | 将最终复盘推送到个人微信 | <https://sct.ftqq.com/> |
| Telegram Bot API | 微信未确认成功时的备用推送 | <https://core.telegram.org/bots/api> |

这些服务不作为代码库的一部分分发。用户需自行申请密钥并遵守相应服务条款；项目不会把密钥提交到 GitHub。

## AI 生成视觉素材

以下仓库文件通过 Codex 中的 OpenAI 图像生成能力制作，属于本项目的原创 AI 辅助同人风格素材：

- `assets/dark-arena-background.png`：黑色石材、锻铁结构、低饱和余烬裂纹的暗黑竞技场背景；无文字、无商标、无官方 Dota 2 Logo。
- `assets/drow-mascot-cutout.png`：蓝紫冰霜弓手主题的原创女性看板娘透明剪影。
- `assets/windranger-mascot-cutout.png`：绿色森林弓手主题的原创女性看板娘透明剪影。

生成提示的核心要求是“原创暗黑奇幻 MOBA 氛围、角色完整露出、无文字、无 Logo、不得复制官方角色立绘”。生成后的角色图又进行了背景网格识别和透明通道清理，以便在 GUI 中作为两侧装饰。

用户提供的现版 Dota 2 主页面截图只用于观察黑金烟雾、石墨导航、暗棕内容面板和绿色主按钮的配色关系；该截图没有进入仓库、EXE 或发布压缩包，界面布局也没有逐像素复制。

## 商标与非官方声明

Dota、Dota 2、相关英雄名称和商标属于 Valve Corporation 或其相应权利人。本项目是非官方社区工具，与 Valve、OpenDota、OpenAI、DeepSeek、Server酱或 Telegram 没有隶属或背书关系。
