# dazibao-mv

Vertical kinetic **dazibao** lyric MV CLI — punchy CJK typography over a poster background, timed to your song.

中文 / English

---

## 功能展示 Style demos（30s）

同一首歌、同一纯色底，三种内置风格小样（逐字/双字轰出）。仓库内路径可直接点开：

| Style | Preview | Video |
|-------|---------|-------|
| **dazibao-ivory** — 象牙字 + 深红钩 | [preview](examples/samples/preview-dazibao-ivory.jpg) | [30s mp4](examples/samples/style-dazibao-ivory-30s.mp4) |
| **laodeng-brick** — 暖金字 + 砖红钩 | [preview](examples/samples/preview-laodeng-brick.jpg) | [30s mp4](examples/samples/style-laodeng-brick-30s.mp4) |
| **mono-poster** — 黑白海报 / 反相钩 | [preview](examples/samples/preview-mono-poster.jpg) | [30s mp4](examples/samples/style-mono-poster-30s.mp4) |

GitHub（需有仓库权限）直链：

- Ivory: https://github.com/sunjoy323/dazibao-mv/blob/main/examples/samples/style-dazibao-ivory-30s.mp4
- Brick: https://github.com/sunjoy323/dazibao-mv/blob/main/examples/samples/style-laodeng-brick-30s.mp4
- Mono: https://github.com/sunjoy323/dazibao-mv/blob/main/examples/samples/style-mono-poster-30s.mp4


## 依赖 Requirements

- **Python** ≥ 3.10
- **ffmpeg** on `PATH`（必须 / required）
- Optional ASR: `faster-whisper` via `pip install 'dazibao-mv[align]'`
- Fonts: prefers `NotoSansCJK-Bold.ttc`, falls back to `DejaVuSans-Bold`

```bash
# Debian/Ubuntu
sudo apt install ffmpeg fonts-noto-cjk
```

---

## 安装 Install

```bash
git clone https://github.com/sunjoy323/dazibao-mv.git
cd dazibao-mv
pip install -e '.[dev]'          # includes pytest
# or with whisper alignment:
pip install -e '.[align,dev]'
```

---

## 命令 Commands

### `dazibao-mv styles`

List builtin styles.

### `dazibao-mv align`

Align a lyrics text file to timings (SRT or whisper):

```bash
dazibao-mv align --audio song.mp3 --lyrics lyrics.txt --out aligned.json
dazibao-mv align --lyrics lyrics.txt --srt song.srt --out aligned.json
dazibao-mv align --audio song.mp3 --lyrics lyrics.txt --out aligned.json \
  --whisper-model medium --initial-prompt '歌名 作者' --max-line-sec 5.5
```

Notes:
- Writes `aligned.json` **and** a sibling `.srt` (same stem) for reproducible `--srt` renders.
- Whisper defaults: model `medium`, **VAD off** (`vad_filter=False`), word timestamps on.
- Overlong ASR blobs are soft-capped (~`--max-line-sec`, default `5.5`) so full-song align never keeps multi-dozen-second lyric lines.
- Same `--whisper-model` / `--initial-prompt` / `--max-line-sec` flags are available on `render` when no `--srt` is given.

### `dazibao-mv render`

Render the full vertical MV:

```bash
dazibao-mv render \
  --audio song.mp3 \
  --lyrics lyrics.txt \
  --srt song.srt \
  --out out/mv.mp4 \
  --title "歌名" --author "作者" \
  --style dazibao-ivory \
  --bg-color '#141210' \
  --lite
```

#### Background modes 背景

| Mode | Flags | Notes |
|------|-------|-------|
| Solid color | `--bg-color #RRGGBB` (default `#141210`) | No image needed |
| Image file | `--bg PATH` | Fitted to `--width`×`--height` |
| AI generate | `--bg-generate --bg-config config.yaml` | OpenAI-compatible Images API |

Example image-gen config (`examples/config.imagegen.yaml`):

```yaml
base_url: https://api.openai.com/v1
api_key_env: OPENAI_API_KEY
model: dall-e-3
prompt: Dark vertical poster background, muted ivory and ink, no text, 9:16
size: "1024x1792"
```

#### Styles 样式

Builtins:

1. **dazibao-ivory** — ivory type, teal-gray shadow, crimson hook smash
2. **laodeng-brick** — warm gold type, brick-red smash
3. **mono-poster** — stark B/W; white bars / inverted hook

Custom:

```bash
dazibao-mv render ... --style-file examples/style_custom.yaml
```

Each style YAML defines `verse` / `chorus` / `hook` RGBA colors, `font`, `hook_keywords`, `chorus_keywords`.

#### Other options

| Flag | Default | Meaning |
|------|---------|---------|
| `--title-dur` | `2.0` | Title card seconds |
| `--max-chars` | `9` | Max chars per display line (orphan merge ≤ max+1) |
| `--lead` | `0.12` | Early punch; clamped so clips **never overlap** |
| `--lite` | off | Also write `*-lite.mp4` (~1600k video) |
| `--width/--height/--fps` | `1080/1920/24` | Output geometry |

---

## 硬规则 Hard rules

- Line clips in the ffmpeg concat **never overlap** (`t0 = max(prev_t1, start - LEAD)`)
- Layout index cycles **separately** for chorus/hook vs verse
- Kinetic reveal uses `glyph_chunks` (per-char / 1–2 glyph pairs), not whole sentences; `REVEAL_FRAC=0.48`, `MAX_PER_CHAR=0.30`; punch newest only
- Layouts auto-shrink so each full lyric line fits on one 9:16 frame

---

## 开发 Dev

```bash
pip install -e '.[dev]'
pytest -q
dazibao-mv --help
dazibao-mv styles
```

---

## License

MIT
