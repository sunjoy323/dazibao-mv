# dazibao-mv

Vertical kinetic **dazibao** lyric MV CLI — punchy CJK typography over a poster background, timed to your song.

中文 / English

---

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
```

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

1. **dazibao-ivory** — ivory fills, deep ink shadows, crimson hook box
2. **laodeng-brick** — warm amber / brick hooks
3. **mono-poster** — white/black high contrast

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
- Kinetic reveal: `REVEAL_FRAC=0.48`, `MAX_PER_CHAR=0.30`; punch applies to newest chunk only

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
