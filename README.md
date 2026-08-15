# Dota 2 OpenDota 复盘摘要工具（Windows / OpenWrt / iStoreOS）

输入一场 Dota 2 的 Match ID，或绑定自己的公开 Steam/Dota 标识后从最近比赛中选择，工具会读取 OpenDota 公开 API，并生成中文 Markdown 复盘摘要。无需登录 Steam、完美世界电竞或 OpenDota 账号。

报告包含：

- 双方阵容、胜负、比分与比赛时长
- Valve 官方简体中文英雄名称（未知新英雄自动回退为英文）
- 全员 KDA、GPM、XPM、补刀与终局经济
- 团队经济曲线的 5 分钟节点、最大经济差、最大单分钟摆动与领先易手
- 终局出装及关键物品购买时间
- 全场死亡时间线
- 数据缺失时自动 Request Parse，每 10 分钟检查一次；只有经济曲线、购买和死亡时间线全部完整才下载并发送
- 生成可直接交给 ChatGPT“Dota2复盘”项目的完整数据包
- 每天从指定日期全部比赛中自动选出表现最好和最差各一场
- 通过 Telegram Bot 自动把每日概览、两场复盘摘要和完整数据包发送到手机

死亡时间线以 OpenDota 返回的日志数组为准。自杀、中立生物击杀和反补等情况可能导致“总死亡数”大于英雄击杀日志条数，这属于正常数据差异，不会再被误判为尚未解析。

## 最简单的用法

1. 安装 [Python 3.10 或更高版本](https://www.python.org/downloads/windows/)，安装时勾选 `Add Python to PATH`。
2. 双击 `run.bat`。
3. 选择使用方式：
   - 输入 Match ID，例如 `8943397976`；或
   - 选择“查看我的 Steam 最近比赛”。
4. 首次查看自己的比赛时，输入 Dota 2 好友代码、SteamID64，或 `steamcommunity.com/profiles/数字` 形式的个人资料链接。
5. 从最近比赛列表中输入序号。数据不完整时，工具会自动申请解析并等待，默认最长 60 分钟，每 10 分钟检查一次。
6. 首次运行结束时，粘贴 ChatGPT“Dota2复盘”项目的页面链接。
7. 工具会同时打开项目页面和复盘数据包位置；把文件名以 `_GPT复盘包.md` 结尾的文件拖入项目聊天并发送即可。

工具可以在本机记住好友代码和 ChatGPT 项目页面链接，之后不用重复输入。它不会打开 Steam 登录页，也不会要求密码、Cookie 或 Steam Web API Key。

受 ChatGPT 项目的权限限制，本地脚本不能代替你点击“上传并发送”。工具会自动完成解析、下载、数据包生成以及打开项目页面，最后只需手动把文件拖入聊天框。

程序只使用 Python 标准库，不需要运行 `pip install`。

在 OpenWrt/iStoreOS 的精简 Python 环境中，桌面浏览器模块可能不存在；工具会自动跳过打开 ChatGPT 页面，不影响抓取、复盘、cron 或 Telegram 推送。

## 每日自动双局复盘

首次设置：

1. 先双击 `run.bat`，绑定 Dota 好友代码，并在一次复盘结束时保存“Dota2复盘”项目链接。
2. 双击 `install_daily_task.bat`。
3. 输入每天执行时间；直接回车采用 `06:15`，为 OpenDota 留出回放解析时间。

默认任务在早晨处理完整的前一个自然日。例如 8 月 15 日 06:15 会统计 8 月 14 日的全部比赛。电脑在计划时间关闭时，Windows 会在下次可运行时补做任务。

筛选综合考虑胜负、KDA、GPM、XPM、英雄伤害和建筑伤害，选取：

- 表现最好的一场；
- 表现最差的一场。

工具会等待两场代表局都完整解析。任意一场仍缺少经济曲线、购买记录或死亡时间线时，本次不写入、不发送残缺数据；下次运行会重新检查。两场完整后才生成 `daily_<日期>_chatgpt_bundle.md`。

每日选出的每一场比赛都会单独建立文件夹，最好和最差两局不会混放。

**如果目标日期没有比赛，任务会直接结束，只写运行日志；不会调用旧 Match ID，不会复制、重开或重新生成以前的复盘。** 如果当天只有一场，则只复盘这一场。

手动测试今天的比赛：

```powershell
py dota2_review.py --daily --day-offset 0
```

每日任务日志位于 `daily_logs`。取消定时任务时双击 `uninstall_daily_task.bat`。

## R5S / iStoreOS / OpenWrt 常驻运行

软路由通常全天开机，很适合代替 Windows 电脑执行每日检查。建议把整个工具文件夹放到 U 盘、SSD 或其他持久化挂载目录，例如 `/mnt/sda1/dota2-review`，不要放在空间有限的固件临时目录。

先通过 SSH 登录软路由并安装 Python（不同 iStoreOS 版本的软件源可能略有差异）：

```sh
opkg update
opkg install python3 python3-urllib python3-openssl ca-bundle
```

把本压缩包解压并上传到持久化目录后执行（以下路径与你的 iStoreOS 磁盘一致）：

```sh
cd /mnt/data_sda3/dota2-review
python3 dota2_review.py --set-steam 你的Dota好友代码
chmod +x run_daily_review.sh install_openwrt_cron.sh uninstall_openwrt_cron.sh
./install_openwrt_cron.sh 06:15 30
```

这会在 root 的 OpenWrt cron 中创建一项每天 `06:15` 执行的任务，统计前一个自然日。每场申请解析后最长等待 60 分钟，每 10 分钟检查一次。请先在 iStoreOS 系统设置中确认时区正确，例如中国大陆使用 `Asia/Shanghai`。

检查状态：

```sh
grep -A 2 DOTA2_DAILY_REVIEW /etc/crontabs/root
tail -n 100 daily_logs/openwrt-latest.log
```

手动试运行昨天的比赛：

```sh
./run_daily_review.sh
```

取消任务（不影响当前尚未清理的本地文件）：

```sh
./uninstall_openwrt_cron.sh
```

重新运行安装脚本只会更新原有 cron 项，不会重复添加。若目标日期没有比赛，只会记录“没有比赛”并结束，不会取用旧 Match ID 或重新生成历史复盘。路由器重启后 cron 会继续存在；但路由器本身仍需保持供电、联网，且挂载磁盘可写。

## 自动清理存储空间

Telegram 确认所有消息和附件都发送成功后，工具会先写入很小的 `daily_state.json` 防重复记录，然后立即删除该日期目录中的摘要、原始 JSON、单局 GPT 数据包和每日合并数据包。发送失败时不会删除，方便下次排查或重发。日志固定使用 `daily_logs/openwrt-latest.log`，每次运行覆盖，不累积历史日志。

若未配置 Telegram，或发送失败，原有保留期限清理仍会删除过期的大文件。工具不会删除 `settings.json`、`telegram_settings.json` 和 `daily_state.json`。

先预览保留 30 天时预计会清理什么，不实际删除：

```sh
python3 dota2_review.py --cleanup-only --cleanup-dry-run --retention-days 30
```

确认后立即执行一次清理：

```sh
python3 dota2_review.py --cleanup-only --retention-days 30
```

立即删除当前所有历史生成数据、日志和常量缓存（Telegram 中已收到的副本不受影响）：

```sh
python3 dota2_review.py --purge-generated-data
```

此命令只清空工具目录中的 `reports`、`daily_logs` 和 `.cache`，保留 Steam、Telegram 配置与防重复发送记录。

软路由可以自动解析、下载，并通过 Telegram 发送本工具生成的中文自动摘要和 `_GPT复盘包.md`，但无法直接代替你操作 ChatGPT 项目页面。若还希望无人值守生成更深入的 GPT 教练分析，需要另外接入 OpenAI API；OpenAI API 需要单独的 API Key 和计费。不要把 API Key、机器人 Token 或邮箱密码直接写进公开脚本或复盘文件。

## Telegram 自动发送到手机

Telegram 推送不需要登录你的 Telegram 账号，也不需要提供账号密码。使用官方 [`@BotFather`](https://t.me/BotFather) 创建一个只属于你的机器人即可：

1. 在 Telegram 搜索并打开带官方认证标记的 `@BotFather`。
2. 发送 `/newbot`，按提示填写机器人名称和以 `bot` 结尾的用户名。
3. 复制 BotFather 返回的 Bot Token；Token 相当于机器人密码，不要发给别人。
4. 在软路由工具目录执行：

```sh
cd /mnt/data_sda3/dota2-review
python3 dota2_review.py --setup-telegram
```

5. Token 输入过程不会显示字符，粘贴后直接回车即可。
6. 程序显示机器人用户名后，打开该机器人并发送 `/start`，再回到 SSH 按回车。
7. 手机收到“推送已连接”，即配置完成。

测试推送：

```sh
python3 dota2_review.py --test-telegram
```

Telegram 设置完成后，OpenWrt 每日 cron 会自动发送：

- 当日比赛数量及最好、最差代表局概览；
- 最好和最差两局各自的中文自动复盘摘要；
- `daily_<日期>_chatgpt_bundle.md` 完整数据包。

Telegram 中会显示实际中文文件名，不再统一显示为 `review.md`。每个附件说明会明确标注日期、最好/最差、英雄、KDA、Match ID，以及它是“基础数据复盘”还是“待 GPT 分析的完整数据包”。完整数据包不是已经由 GPT 分析完成的最终教练复盘。

只有两场代表局的经济曲线、购买记录和死亡时间线均完整时才会发送；全部附件成功后，本地当天文件会立即删除。`daily_state.json` 会保留日期和 Match ID，防止删除本地文件后再次发送同一天复盘。

目标日期无比赛时只发送“今日无比赛”，不会读取或发送以前的比赛文件。手动复盘某个 Match ID 并立即发送：

```sh
python3 dota2_review.py 8943397976 --send-telegram --parse-timeout 60 --no-open-project
```

单场发送也会强制等待完整数据，Telegram 成功接收摘要和完整数据包后会删除该场本地摘要、原始 JSON 和 GPT 数据包。

删除 Telegram 配置：

```sh
python3 dota2_review.py --forget-telegram
```

Bot Token 单独保存在工具目录的 `telegram_settings.json`，在 OpenWrt/Linux 上权限会设置为仅 root 可读写。若出现“无法连接 Telegram”，请检查软路由本机是否能访问 `api.telegram.org`，以及 OpenClash 是否为路由器自身流量正确应用 Telegram 代理规则。

## 命令行用法

在项目文件夹打开终端：

```powershell
py dota2_review.py 8943397976
```

首次绑定并查看自己的最近比赛：

```powershell
py dota2_review.py --steam 你的Dota好友代码
```

只保存标识、不立即查询（适合软路由）：

```powershell
py dota2_review.py --set-steam 你的Dota好友代码
```

以后直接查看已绑定账号的最近比赛：

```powershell
py dota2_review.py --my-matches
```

清除本机保存的标识：

```powershell
py dota2_review.py --forget-steam
```

提前保存 ChatGPT 项目链接：

```powershell
py dota2_review.py 8943397976 --project-url "你的项目页面链接"
```

清除保存的项目链接：

```powershell
py dota2_review.py --forget-project
```

也可以把好友代码替换为 SteamID64，或数字版链接，例如：

```text
https://steamcommunity.com/profiles/7656119xxxxxxxxxx
```

`/id/自定义名称` 形式的链接无法在不使用 Steam API Key 的情况下直接换算；请在 Dota 2 的“添加好友”页面复制好友代码。

自定义输出位置：

```powershell
py dota2_review.py 8943397976 -o "D:\Dota复盘\8943397976.md"
```

列出包括消耗品在内的全部购买记录：

```powershell
py dota2_review.py 8943397976 --all-purchases
```

自动解析默认开启。临时不申请解析：

```powershell
py dota2_review.py 8943397976 --no-request-parse
```

修改解析等待时间，例如等待 30 分钟：

```powershell
py dota2_review.py 8943397976 --parse-timeout 30
```

工具只会在数据不完整时提交 Request Parse；OpenDota 官方说明该请求会按 10 次请求计入限流。之后每 10 分钟检查一次，默认最多等待 60 分钟。回放已过期、比赛未公开或 OpenDota 无法取得回放时，解析仍可能失败；稍后重新运行即可再次下载最新结果。

每场比赛会在 `reports` 中建立一个独立文件夹。文件夹和文件均按“比赛日期＋英雄＋KDA＋Match ID”命名，例如：

```text
reports/
└─ 2026-08-13_术士_4-4-25_Match_8943641957/
   ├─ 2026-08-13_术士_4-4-25_Match_8943641957_复盘摘要.md
   ├─ 2026-08-13_术士_4-4-25_Match_8943641957_OpenDota原始数据.json
   └─ 2026-08-13_术士_4-4-25_Match_8943641957_GPT复盘包.md
```

其中：

- `_复盘摘要.md`：中文自动摘要；
- `_OpenDota原始数据.json`：完整 OpenDota 原始数据；
- `_GPT复盘包.md`：摘要、复盘提示及完整 JSON 合并的数据包，推荐上传这个文件。

Match ID 用于避免同一天使用同一英雄并打出相同 KDA 时发生文件覆盖。

## 常见错误

- `没有找到这场比赛`：检查 Match ID 是否正确，并确认 Dota 2 的“公开比赛数据”已开启。刚结束的比赛也可能尚未同步。
- `没有取得最近比赛`：检查好友代码是否正确，并在 Dota 2 设置中开启“公开比赛数据”。
- `请求过于频繁`：稍后再试；不要连续重复提交 Request Parse。
- `无法连接 OpenDota`：检查 Windows 代理、防火墙和网络。如果浏览器也打不开 OpenDota，需要先解决网络访问问题。
- 报告有 KDA 但没有时间线：通常是 OpenDota 尚未解析完成；稍后重新运行即可。
- 完美世界电竞 APP 显示“已解析”，不代表 OpenDota 也已解析；它们是两套独立的数据服务。本工具以 OpenDota 返回的 `version` 和时间线字段为准。
- 中文显示乱码：报告采用带 BOM 的 UTF-8，建议使用 VS Code、Typora 或现代浏览器打开。

## 隐私与接口

- 比赛详情：`GET https://api.opendota.com/api/matches/{match_id}`
- 我的最近比赛：`GET https://api.opendota.com/api/players/{account_id}/recentMatches`
- 英雄/物品名称：`GET https://api.opendota.com/api/constants/{resource}`
- 自动解析请求：`POST https://api.opendota.com/api/request/{match_id}`
- 绑定时只在工具目录的 `settings.json` 保存公开的 Dota 好友代码和 ChatGPT 项目页面链接，不保存 SteamID64、账号、密码或 Cookie。
- 不读取 ChatGPT 登录信息，也不会自动上传或发送消息；项目页面由系统默认浏览器打开。
- Telegram 推送只调用官方 Bot API；Bot Token 与接收私聊 ID 保存在独立的 `telegram_settings.json`，不会写入复盘文件。
- 最近比赛接口通常返回最近 20 场服务器已记录的比赛；更早的比赛仍可直接输入 Match ID 查询。
- 不读取本机 Dota 2 文件，不要求账号、密码、Cookie 或 API Key。

OpenDota 是社区维护的第三方服务；报告质量取决于它能取得和解析的公开比赛数据。

