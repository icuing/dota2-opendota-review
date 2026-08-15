Dota 2 OpenDota MAX 复盘教练 v1.7.0（OpenWrt / iStoreOS 精简版）

此压缩包不包含 Windows GUI、图片素材或打包运行时，适合软路由长期运行。

更新现有安装：
1. 备份原目录中的 dota2_review.py。
2. 解压本包并覆盖程序、两个中文名称 JSON 和三个 shell 脚本。
3. 不要删除或覆盖 settings.json、ai_settings.json、serverchan_settings.json、telegram_settings.json、daily_state.json。
4. 执行：python3 dota2_review.py --version
5. 执行：python3 dota2_review.py --test-ai
6. 执行：python3 dota2_review.py --test-wechat

首次安装和定时任务配置请阅读仓库 README.md 的“iStoreOS / OpenWrt 完整更新与安装”。

英雄专项命令示例：
python3 dota2_review.py --hero-review 8 --history-count 5 --compare-source high_rank --benchmark-count 3
