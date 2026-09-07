# dazibao-mv

Vertical kinetic **dazibao** lyric MV — punchy CJK typography over a poster background, timed to your song.

**English** | [中文说明](README.zh-CN.md)

---

## Style demos（30s）

Same song, solid background. **8 builtin styles** — each with a **distinct motion language** (layouts + punch), not just colors. Glyph / digraph punch. Paths in-repo:

| Style | Preview | Video |
|-------|---------|-------|
| **dazibao-ivory** — ivory + crimson hook | [preview](examples/samples/preview-dazibao-ivory.jpg) | [30s mp4](examples/samples/style-dazibao-ivory-30s.mp4) |
| **laodeng-brick** — warm gold + brick hook | [preview](examples/samples/preview-laodeng-brick.jpg) | [30s mp4](examples/samples/style-laodeng-brick-30s.mp4) |
| **mono-poster** — B/W poster / inverted hook | [preview](examples/samples/preview-mono-poster.jpg) | [30s mp4](examples/samples/style-mono-poster-30s.mp4) |
| **poster-wall** — full-screen dazibao / alternating palettes | — | — |
| **neon-cyber** — strobe edge-columns + glitch punch (cyan/magenta neon) | [preview](examples/samples/preview-neon-cyber.jpg) | [30s mp4](examples/samples/style-neon-cyber-30s.mp4) |
| **blueprint** — drafting-grid measured reveal (titleblock / H·V rules) | [preview](examples/samples/preview-blueprint.jpg) | [30s mp4](examples/samples/style-blueprint-30s.mp4) |
| **pop-comic** — panel-smash / diagonal banner / slam burst | [preview](examples/samples/preview-pop-comic.jpg) | [30s mp4](examples/samples/style-pop-comic-30s.mp4) |
| **ink-wash** — vertical calligraphy scroll + soft grow / dissolve | [preview](examples/samples/preview-ink-wash.jpg) | [30s mp4](examples/samples/style-ink-wash-30s.mp4) |

GitHub blob links (needs repo access):

- Ivory: https://github.com/sunjoy323/dazibao-mv/blob/main/examples/samples/style-dazibao-ivory-30s.mp4
- Brick: https://github.com/sunjoy323/dazibao-mv/blob/main/examples/samples/style-laodeng-brick-30s.mp4
- Mono: https://github.com/sunjoy323/dazibao-mv/blob/main/examples/samples/style-mono-poster-30s.mp4
- Neon: https://github.com/sunjoy323/dazibao-mv/blob/main/examples/samples/style-neon-cyber-30s.mp4
- Blueprint: https://github.com/sunjoy323/dazibao-mv/blob/main/examples/samples/style-blueprint-30s.mp4
- Comic: https://github.com/sunjoy323/dazibao-mv/blob/main/examples/samples/style-pop-comic-30s.mp4
- Ink: https://github.com/sunjoy323/dazibao-mv/blob/main/examples/samples/style-ink-wash-30s.mp4
- poster-wall: sample TBD

---

## Dependencies

| Requirement | Notes |
|-------------|--------|
| **Python** ≥ 3.10 | |
| **ffmpeg** on `PATH` | Required for encode / concat / mux |
| Fonts | Prefers `NotoSansCJK-Bold.ttc`, falls back to DejaVu |
| Optional ASR | `faster-whisper` via `pip install 'dazibao-mv[align]'` |
| Optional Web UI | FastAPI / Uvicorn via `pip install 'dazibao-mv[web]'` |

```bash
# Debian/Ubuntu
sudo apt install ffmpeg fonts-noto-cjk
```

Whisper models download on first use (cached under `~/.cache`).

---

## Install

```bash
git clone https://github.com/sunjoy323/dazibao-mv.git
cd dazibao-mv

# CLI only (render with --srt, no Whisper)
pip install -e .

# + Whisper alignment
pip install -e '.[align]'

# + Web UI
pip install -e '.[web]'

# Dev / tests (includes web + align extras)
pip install -e '.[dev]'

# Everything for local web + ASR
pip install -e '.[web,align,dev]'
```

---

## CLI usage

### `dazibao-mv styles`

List builtin styles and short descriptions.

### `dazibao-mv clean-lyrics`

Strip section tags (`[Intro]`, `[Verse 1]`, `[Chorus]`, `[Fade Out]`, …) and bracketed English production notes (`[Slow acoustic guitar…]`). Chinese lyric lines are kept. `load_lyrics_file` / web paste+upload also auto-clean by default.

```bash
dazibao-mv clean-lyrics --in lyrics_raw.txt --out lyrics.txt
```

### `dazibao-mv align`

Align a lyrics text file to timings (SRT or Whisper):

```bash
dazibao-mv align --audio song.mp3 --lyrics lyrics.txt --out aligned.json
dazibao-mv align --lyrics lyrics.txt --srt song.srt --out aligned.json
dazibao-mv align --audio song.mp3 --lyrics lyrics.txt --out aligned.json \
  --whisper-model medium --initial-prompt '歌名 作者' --max-line-sec 5.5
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--audio` | — | Audio for Whisper when no `--srt` (mp3/wav/webm/…; ffmpeg-readable) |
| `--lyrics` | required | Plain lyrics text (one line per lyric; section tags auto-stripped on load) |
| `--out` | required | Output `aligned.json` |
| `--srt` | — | Skip Whisper; use this SRT |
| `--max-chars` | `9` | Split long lines for display |
| `--whisper-model` | `medium` | faster-whisper size |
| `--initial-prompt` | auto | Optional ASR prompt |
| `--max-line-sec` | `5.5` | Cap single-line ASR span |

Notes:

- Writes `aligned.json` **and** a sibling `.srt` for reproducible `--srt` renders.
- Whisper defaults: model `medium`, **VAD off**, word timestamps on.
- First lyric: search early ASR cues; overlong intro bleed is end-anchored / word-onset corrected.

### `dazibao-mv render`

Full vertical MV pipeline (align + kinetic frames + ffmpeg mux):

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

#### Background modes

| Mode | Flags | Notes |
|------|-------|-------|
| Solid color | `--bg-color #RRGGBB` (default `#141210`) | No image needed |
| Image file | `--bg PATH` | Fitted to `--width`×`--height` |
| AI generate | `--bg-generate --bg-config config.yaml` | OpenAI-compatible Images API |

See `examples/config.imagegen.yaml` for an image-gen config example.

#### Styles

1. **dazibao-ivory** — ivory type, teal-gray shadow, crimson hook smash  
2. **laodeng-brick** — warm gold type, brick-red smash  
3. **mono-poster** — stark B/W; white bars / inverted hook  
4. **poster-wall** — poster-fill: glyphs auto-fit the screen, hard block shadow, decor + stamp「大字报」, **alternating solid palettes** per line. `--bg*` flags are ignored.  
5. **neon-cyber** — edge neon columns / glitch punch; screen-filling; gaps hold previous line  
6. **blueprint** — drafting titleblock / H·V rules / corner; slide reveal (no bounce); hold at low opacity  
7. **pop-comic** — comic panel / slash / stack-burst slam; screen-filling; gaps hold  
8. **ink-wash** — vertical calligraphy / two-col / seal; soft grow; paper margins; hold dissolve  

Motion styles (**neon-cyber**, **blueprint**, **pop-comic**, **ink-wash**) declare their own `layouts` / `punch` / `gap_mode` in YAML. Adjacent lines never reuse the same layout (max 2 only when one lyric is split); never 3 identical in a row; mid gaps hold (no black flash by default).

Custom style file:

```bash
dazibao-mv render ... --style-file examples/style_custom.yaml
```

Each style YAML defines `verse` / `chorus` / `hook` RGBA, `font`, `hook_keywords`, `chorus_keywords`. Custom styles may also include `layouts`, `punch`, `gap_mode`, `fill`, `reveal_frac`, etc. **poster-wall** still uses `mode: poster_fill` + `palettes`.

#### Important render flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--title` / `--author` | empty | Title card text |
| `--title-dur` | auto | Override title seconds; omit for auto |
| `--title-before-lyric` | `1.0` | Auto title ends this many seconds before first lyric |
| `--title-fade` | `0.8` | Title fade-out duration |
| `--srt` | — | Skip Whisper |
| `--max-chars` | `9` | Max chars per display line |
| `--lead` | `0.12` | Early punch; clamped so clips never overlap |
| `--gap-mode` | `auto` | `auto` (style default), `hold`, `black`/`cut`, or `flash` |
| `--punch-mode` | `uniform` | `uniform` (default even reveal) or `rhythm` (Whisper word starts + per-chunk intensity 0.7–1.4) |
| `--aligned` | — | Skip align; load timed JSON (keeps `words` for rhythm mode) |
| `--lite` | off | Also write `*-lite.mp4` if smaller (CRF 26, maxrate 800k); skipped when master is already smaller |
| `--width` / `--height` / `--fps` | `1080` / `1920` / `24` | Output geometry |
| `--whisper-model` | `medium` | Used when no `--srt` |

---

## Web mode

Friendly browser UI: upload MP3 / WAV / WebM (browser recordings) + lyrics → backend align + render → preview / download.

### Run locally

```bash
pip install -e '.[web,align]'
dazibao-mv serve                          # http://127.0.0.1:8765/
dazibao-mv serve --host 0.0.0.0 --port 8765
```

Open **http://127.0.0.1:8765/**.

### Upload flow

1. Choose an audio file (MP3 / WAV / WebM / … — WebM is common for browser `MediaRecorder` clips; container duration may be missing, and the tool decodes/packet-probes when needed).  
2. Paste lyrics **or** upload `.txt` / `.lrc` / `.srt`.  
3. Pick a style (ivory / brick / mono / poster-wall / neon-cyber / blueprint / pop-comic / ink-wash), optional color overrides, font, title/author, gap-mode, Whisper model, lite, geometry.  
4. Submit → poll job status → watch preview → download master (and lite if enabled).

### Timestamped lyrics → skip Whisper

If lyrics are **SRT** (`00:00:01,000 --> …`) or **LRC** (`[mm:ss.xx]…`), the backend **skips Whisper** and uses those timings. The UI shows a badge: **将跳过对齐**.

Plain text lyrics still go through Whisper (`faster-whisper`; install `[align]`).

### Job storage

Work dirs default to `~/.cache/dazibao-mv/jobs/{id}` (override with env `DAZIBAO_DATA`).

API sketch:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/styles` | Builtin styles + colors / palettes |
| `GET` | `/api/fonts` | Discover Noto CJK / DejaVu / other TTF·OTF |
| `POST` | `/api/jobs` | Multipart: audio, lyrics file/text, JSON options |
| `GET` | `/api/jobs/{id}` | Status: queued \| running \| done \| error |
| `GET` | `/api/jobs/{id}/download` | master mp4; `?lite=1` for lite |

### Docker Compose

```bash
docker compose up --build
# open http://localhost:8765
```

- Image installs `ffmpeg`, `fonts-noto-cjk`, and the package with `[web,align]`.  
- Container binds `0.0.0.0:8765`; compose maps `8765:8765`.  
- Volumes: job data (`DAZIBAO_DATA=/data`) and Hugging Face / Whisper cache.

Stop with `Ctrl+C` or `docker compose down`.

---

## Hard rules

- Line clips in the ffmpeg concat **never overlap** (`t0 = max(prev_t1, start - LEAD)`).  
- Layout index cycles **separately** for chorus/hook vs verse.  
- Kinetic reveal uses `glyph_chunks` (per-char / 1–2 glyph pairs), not whole sentences; `REVEAL_FRAC=0.48`, `MAX_PER_CHAR=0.30`; punch newest only.  
- Lyrics auto-clean drops `[Section]` tags and English production notes (CLI `clean-lyrics`, `load_lyrics_file`, web upload).  
- `--punch-mode rhythm` maps chunk reveals to Whisper word timestamps with heavier/lighter punch by syllable length/gap; falls back to uniform per line without words.  
- Layouts auto-shrink so each full lyric fits one 9:16 frame.  
- Gaps **hold** the previous lyric/title by default (`--gap-mode hold`); `black` restores solid matte.  
- Title card (when `--title` is set) lasts until **1s before first lyric**, then **fades** (`--title-fade`, default 0.8s).  
- `poster_fill` / **poster-wall**: alternating solid palette backgrounds; hard block shadows; screen-fill layouts (`poster_fill_h` / `poster_fill_v`).  
- Builtin motion styles (**neon-cyber**, **blueprint**, **pop-comic**, **ink-wash**) each use a private layout pool + punch curve; anti-repeat assignment (no consecutive same layout unless `split_group`; never 3 in a row).  
- Those styles default to `--gap-mode hold` (no mid-song black); CLI can still force `black`/`flash`.  
- Neon / blueprint / comic aim ~90%+ screen fill; ink-wash keeps calmer paper margins.

---

## Development

```bash
pip install -e '.[dev]'
pytest -q
dazibao-mv --help
dazibao-mv styles
dazibao-mv serve --host 127.0.0.1 --port 8765
```

Web API tests use FastAPI `TestClient` and mock heavy `render_mv` where needed.

### Limitations

- Web jobs run in a **single background worker** thread by default — long renders queue behind each other.  
- First Whisper model download can be large and slow.  
- Full-song kinetic renders are CPU-heavy (PIL frames + ffmpeg).

---

## License

MIT
