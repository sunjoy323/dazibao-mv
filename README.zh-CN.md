# dazibao-mv（大字报 MV）

竖屏动能**大字报**歌词 MV 工具：冲击力 CJK 排版叠在海报背景上，并与歌曲时间轴对齐。

[English](README.md) | **中文说明**

---

## 风格小样（30 秒）

同一首歌、纯色底，内置风格预览（逐字 / 双字轰出）。仓库内路径可直接打开：

| 风格 | 预览图 | 视频 |
|------|--------|------|
| **dazibao-ivory** — 象牙字 + 深红钩 | [preview](examples/samples/preview-dazibao-ivory.jpg) | [30s mp4](examples/samples/style-dazibao-ivory-30s.mp4) |
| **laodeng-brick** — 暖金字 + 砖红钩 | [preview](examples/samples/preview-laodeng-brick.jpg) | [30s mp4](examples/samples/style-laodeng-brick-30s.mp4) |
| **mono-poster** — 黑白海报 / 反相钩 | [preview](examples/samples/preview-mono-poster.jpg) | [30s mp4](examples/samples/style-mono-poster-30s.mp4) |
| **poster-wall** — 大字报铺满 / 交替色板 | — | — |

GitHub 直链（需仓库权限）：

- Ivory: https://github.com/sunjoy323/dazibao-mv/blob/main/examples/samples/style-dazibao-ivory-30s.mp4
- Brick: https://github.com/sunjoy323/dazibao-mv/blob/main/examples/samples/style-laodeng-brick-30s.mp4
- Mono: https://github.com/sunjoy323/dazibao-mv/blob/main/examples/samples/style-mono-poster-30s.mp4

---

## 依赖

| 依赖 | 说明 |
|------|------|
| **Python** ≥ 3.10 | |
| **ffmpeg** 在 `PATH` 中 | 编码 / 拼接 / 混流必需 |
| 字体 | 优先 `NotoSansCJK-Bold.ttc`，否则回退 DejaVu |
| 可选 ASR | `pip install 'dazibao-mv[align]'` 安装 `faster-whisper` |
| 可选 Web | `pip install 'dazibao-mv[web]'` 安装 FastAPI / Uvicorn |

```bash
# Debian / Ubuntu
sudo apt install ffmpeg fonts-noto-cjk
```

首次使用 Whisper 会下载模型（缓存于 `~/.cache`）。

---

## 安装

```bash
git clone https://github.com/sunjoy323/dazibao-mv.git
cd dazibao-mv

# 仅 CLI（可用 --srt，不装 Whisper）
pip install -e .

# + Whisper 对齐
pip install -e '.[align]'

# + Web 界面
pip install -e '.[web]'

# 开发 / 测试（含 web + align）
pip install -e '.[dev]'

# 本地 Web + ASR 一站式
pip install -e '.[web,align,dev]'
```

---

## 命令行用法

### `dazibao-mv styles`

列出内置风格及简介。

### `dazibao-mv align`

将歌词文本对齐到时间轴（SRT 或 Whisper）：

```bash
dazibao-mv align --audio song.mp3 --lyrics lyrics.txt --out aligned.json
dazibao-mv align --lyrics lyrics.txt --srt song.srt --out aligned.json
dazibao-mv align --audio song.mp3 --lyrics lyrics.txt --out aligned.json \
  --whisper-model medium --initial-prompt '歌名 作者' --max-line-sec 5.5
```

| 参数 | 默认 | 含义 |
|------|------|------|
| `--audio` | — | 无 `--srt` 时用 Whisper |
| `--lyrics` | 必需 | 纯文本歌词（一行一句） |
| `--out` | 必需 | 输出 `aligned.json` |
| `--srt` | — | 跳过 Whisper，使用该 SRT |
| `--max-chars` | `9` | 过长行拆分上限 |
| `--whisper-model` | `medium` | faster-whisper 型号 |
| `--initial-prompt` | 自动 | 可选 ASR 提示词 |
| `--max-line-sec` | `5.5` | 单行 ASR 时长软上限 |

说明：

- 同时写出 `aligned.json` 与同名 `.srt`，方便之后用 `--srt` 复现。  
- Whisper 默认：`medium`、**关闭 VAD**、开启词级时间戳。  
- 首句特殊处理：在前奏区搜强匹配；过长前奏渗血会做末端锚定 / 词起点修正。

### `dazibao-mv render`

完整竖屏 MV 流水线（对齐 + 动能帧 + ffmpeg 混流）：

```bash
dazibao-mv render \
  --audio song.mp3 \
  --lyrics lyrics.txt \
  --srt song.srt \
  --out out/mv.mp4 \
  --title "歌名" --author "作者" \
  --style dazibao-ivory \
  --bg-color '#141210' \
  --gap-mode hold \
  --lite
```

#### 背景模式

| 模式 | 参数 | 说明 |
|------|------|------|
| 纯色 | `--bg-color #RRGGBB`（默认 `#141210`） | 无需图片 |
| 图片 | `--bg PATH` | 适配到宽×高 |
| AI 生成 | `--bg-generate --bg-config config.yaml` | OpenAI 兼容图像 API |

示例配置见 `examples/config.imagegen.yaml`。

#### 内置风格

1. **dazibao-ivory** — 象牙字、青灰阴影、深红钩击  
2. **laodeng-brick** — 暖金字、砖红钩击  
3. **mono-poster** — 强烈黑白；白条 / 反相钩  
4. **poster-wall** — 铺满屏大字报：硬块阴影、装饰与印章「大字报」、**按行交替纯色色板**。忽略 `--bg*`。

自定义风格：

```bash
dazibao-mv render ... --style-file examples/style_custom.yaml
```

每个风格 YAML 定义 `verse` / `chorus` / `hook` 的 RGBA、`font`、`hook_keywords`、`chorus_keywords`。poster-wall 另含 `mode: poster_fill` 与 `palettes`。

#### 常用渲染参数

| 参数 | 默认 | 含义 |
|------|------|------|
| `--title` / `--author` | 空 | 片头标题 / 作者 |
| `--title-dur` | 自动 | 强制片头秒数；省略则自动 |
| `--title-before-lyric` | `1.0` | 自动片头在首句前多少秒结束 |
| `--title-fade` | `0.8` | 片头淡出时长 |
| `--srt` | — | 跳过 Whisper |
| `--max-chars` | `9` | 单屏行字数上限 |
| `--lead` | `0.12` | 提前轰出；钳制保证片段不重叠 |
| `--gap-mode` | `hold` | `hold` 保持上一画面，或 `black` 黑场 |
| `--lite` | 关 | 额外输出 `*-lite.mp4`（约 1600k 视频） |
| `--width` / `--height` / `--fps` | `1080` / `1920` / `24` | 输出几何 |
| `--whisper-model` | `medium` | 无 `--srt` 时使用 |

#### poster-wall / `mode: poster_fill`

```yaml
mode: poster_fill
decor: true
shadow_offset: [18, 18]
palettes:
  - {bg: [10,10,10], fill: [242,237,228], shadow: [196,30,58], ...}
  - {bg: [242,232,216], fill: [196,30,58], shadow: [17,17,17], ...}
  - {bg: [196,30,58], fill: [242,237,228], shadow: [17,17,17], ...}
```

- 布局：`poster_fill_h`（短句）/ `poster_fill_v`（长句），中等长度轮换。  
- 每句用 `palettes[i % n]` 作纯色底，再画装饰与硬阴影大字（约占画面 88–94%）。  
- 片头使用色板 B（米色纸）+ 同套装饰与淡出。

---

## Web 模式（浏览器）

上传 MP3 + 歌词 → 后端对齐并渲染 → 预览 / 下载。界面为中文标签。

### 本地启动

```bash
pip install -e '.[web,align]'
dazibao-mv serve                          # http://127.0.0.1:8765/
dazibao-mv serve --host 0.0.0.0 --port 8765
```

浏览器打开 **http://127.0.0.1:8765/**。

### 使用流程

1. 选择音频（MP3 / WAV 等）。  
2. 粘贴歌词，或上传 `.txt` / `.lrc` / `.srt`。  
3. 选择风格（象牙 / 砖红 / 黑白 / 海报墙），可选自定义颜色、字体、标题/作者、间隙模式、Whisper 型号、lite、分辨率。  
4. 提交 → 轮询任务状态 → 预览视频 → 下载 master（及可选 lite）。

### 带时间轴的歌词 → 跳过对齐

若歌词是 **SRT**（`00:00:01,000 --> …`）或 **LRC**（`[mm:ss.xx]…`），后端会**跳过 Whisper**，直接使用时间戳。界面显示徽章：**将跳过对齐**。

纯文本歌词仍走 Whisper（需安装 `[align]`）。

### 任务目录

默认 `~/.cache/dazibao-mv/jobs/{id}`，可用环境变量 `DAZIBAO_DATA` 覆盖。

主要 API：

| 方法 | 路径 | 作用 |
|------|------|------|
| `GET` | `/api/styles` | 内置风格与配色 / 色板 |
| `GET` | `/api/fonts` | 发现系统 Noto CJK / DejaVu 等字体 |
| `POST` | `/api/jobs` | multipart：音频、歌词文件/文本、JSON 选项 |
| `GET` | `/api/jobs/{id}` | 状态：queued \| running \| done \| error |
| `GET` | `/api/jobs/{id}/download` | master；`?lite=1` 为 lite |

### Docker Compose

```bash
docker compose up --build
# 浏览器打开 http://localhost:8765
```

- 镜像安装 `ffmpeg`、`fonts-noto-cjk`，并以 `[web,align]` 安装本包。  
- 容器内绑定 `0.0.0.0:8765`，compose 映射 `8765:8765`。  
- 卷：任务数据（`DAZIBAO_DATA=/data`）与 Whisper / Hugging Face 缓存。

结束：`Ctrl+C` 或 `docker compose down`。

---

## 硬规则

- ffmpeg concat 中的行片段**永不重叠**（`t0 = max(prev_t1, start - LEAD)`）。  
- 副歌/钩子与主歌的布局索引**分别**循环。  
- 动能揭示用 `glyph_chunks`（单字 / 1–2 字组），不是整句；`REVEAL_FRAC=0.48`，`MAX_PER_CHAR=0.30`；只轰最新一块。  
- 布局自动缩小，保证整句落在单个 9:16 画面内。  
- 句间间隙默认 **hold** 上一画面（`--gap-mode hold`）；`black` 恢复黑场。  
- 有 `--title` 时，片头持续到**首句前 1 秒**，再 **fade**（`--title-fade`，默认 0.8s）。  
- **poster-wall** / `poster_fill`：交替纯色底 + 硬块阴影 + 铺满布局（`poster_fill_h` / `poster_fill_v`）。

---

## 开发

```bash
pip install -e '.[dev]'
pytest -q
dazibao-mv --help
dazibao-mv styles
dazibao-mv serve --host 127.0.0.1 --port 8765
```

Web 测试使用 FastAPI `TestClient`，必要时 mock 重量级 `render_mv`。

### 局限

- Web 任务默认**单 worker 后台线程**，长任务会排队。  
- 首次 Whisper 模型下载体积大、耗时长。  
- 整曲动能渲染偏吃 CPU（PIL 出帧 + ffmpeg）。

---

## 许可证

MIT
