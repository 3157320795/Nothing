# Nothing

Nothing 是一个为“碎片化办公时间”打造的多功能桌面工具，主打 **上班摸鱼神器** 体验：你可以在一个界面里低调地搜索并阅读小说、查看基金动态、刷 B 站热门与检索视频，不用频繁切换网页和应用，在忙碌与放松之间快速切换节奏。项目提供 GUI 与命令行双入口，开箱即用、轻量无负担，适合想在工作间隙高效“补能量”的你。

当前主要包含：

- **首页**：小说搜索、选择结果后从指定章节起连续下载（支持停止/日志输出/保存到本地）
- **阅读**：本地小说库浏览、章节阅读、上一章/下一章、**沉浸模式**（置顶+全屏阅读，Esc 进出，←/→ 跳章）
- **基金**：基金代码 + 份额管理；净值与实时估值展示；历史净值折线图（点选查看数据）；本地持久化保存列表
- **视频**：B 站热门/搜索；可解析链接；可选本地 `ffmpeg` 进行内嵌播放（不同平台依赖情况略有差异）
- **音乐**：网易云热歌榜/搜索；优先解析可播放直链，不可用时提供网页兜底
## 环境依赖

### 运行环境

- Python 3.10 及以上（推荐 3.11+）
- Tkinter（用于 GUI，通常随 Python 一起安装）

### Python 包依赖

- aiohttp

推荐使用 `requirements.txt` 安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

### 可选依赖

- ffmpeg（用于 GUI 中视频播放相关能力）

macOS 示例（Homebrew）：

```bash
brew install ffmpeg
```

## 创建环境并下载依赖

```bash
# 1) 创建虚拟环境
python3 -m venv .venv

# 2) 激活虚拟环境（macOS / Linux）
source .venv/bin/activate

# 3) 升级 pip（可选但推荐）
python -m pip install --upgrade pip

# 4) 安装项目依赖
python -m pip install -r requirements.txt
```

## 项目启动


### 默认启动（GUI）

```bash
python3 -m src.app_main
```

### 指定启动 GUI

```bash
python3 -m src.app_main --gui
```

### 终端交互模式

```bash
python3 -m src.app_main --terminal
```

### 命令行抓取（非交互）

```bash
python3 -m src.app_main "校花的贴身高手" --index 1 --chapterid 10
```

## 输出目录

- 小说默认保存目录：`out/novel`
- 日志目录：`log`

## 开源与合规声明

本项目以开源方式提供，目标是用于 Python 编程、GUI 开发、网络请求与数据处理等技术学习和交流。

- 项目中的爬虫/抓取相关能力仅用于学习研究与个人技术验证。
- 使用前请自行确认目标站点的服务条款（ToS）、`robots.txt`、版权政策及当地法律法规要求。
- 请勿将本项目用于未授权的数据采集、批量抓取、商业牟利或其他可能侵犯他人权益的场景。
- 因不当使用本项目产生的法律风险、账号风险或其他损失，由使用者自行承担。

## 后续扩展方向
1、 增加快捷键（老板键）
2、 休闲小游戏
3、 漫画
4、 短剧
5、 长视频
