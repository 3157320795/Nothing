# Nothing

一个以 Python 编写的轻量工具集合，当前主要包含：

- 小说搜索与章节抓取（支持保存到本地）
- 基金相关数据获取
- B 站视频信息查询与播放相关能力（部分功能依赖本地 ffmpeg）
- 图形界面（GUI）与命令行两种使用方式

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
