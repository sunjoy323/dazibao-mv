/* dazibao-mv web SPA */
(() => {
  const $ = (id) => document.getElementById(id);

  const ROLES = ["verse", "chorus", "hook"];
  const CHANNELS = ["fill", "shadow", "accent"];
  const PALETTE_KEYS = ["bg", "fill", "shadow", "accent"];

  let styles = [];
  let pollTimer = null;

  function rgbaToHex(arr) {
    if (!arr || !arr.length) return "#ffffff";
    const [r, g, b] = arr;
    return (
      "#" +
      [r, g, b]
        .map((x) => Math.max(0, Math.min(255, Number(x) | 0)).toString(16).padStart(2, "0"))
        .join("")
    );
  }

  function currentStyle() {
    const name = $("style").value;
    return styles.find((s) => s.name === name) || styles[0];
  }

  function buildRoleEditors(style) {
    const root = $("roleColors");
    root.innerHTML = "";
    for (const role of ROLES) {
      const box = document.createElement("div");
      box.className = "role-box";
      box.innerHTML = `<h3>${role}</h3>`;
      const block = (style && style[role]) || {};
      for (const ch of CHANNELS) {
        const row = document.createElement("div");
        row.className = "swatch-row";
        const label = document.createElement("span");
        label.textContent = ch;
        const input = document.createElement("input");
        input.type = "color";
        input.dataset.role = role;
        input.dataset.channel = ch;
        input.value = rgbaToHex(block[ch]);
        row.appendChild(label);
        row.appendChild(input);
        box.appendChild(row);
      }
      root.appendChild(box);
    }
  }

  function buildPaletteEditors(style) {
    const wrap = $("paletteEditors");
    const note = $("paletteNote");
    wrap.innerHTML = "";
    if (!style || !style.poster_fill || !(style.palettes || []).length) {
      note.classList.add("hidden");
      wrap.classList.add("hidden");
      return;
    }
    note.classList.remove("hidden");
    wrap.classList.remove("hidden");
    (style.palettes || []).forEach((pal, idx) => {
      const card = document.createElement("div");
      card.className = "palette-card";
      card.innerHTML = `<h4>色板 ${idx + 1}</h4>`;
      for (const key of PALETTE_KEYS) {
        const row = document.createElement("div");
        row.className = "swatch-row";
        const label = document.createElement("span");
        label.textContent = key;
        const input = document.createElement("input");
        input.type = "color";
        input.dataset.palette = String(idx);
        input.dataset.pkey = key;
        input.value = rgbaToHex(pal[key]);
        row.appendChild(label);
        row.appendChild(input);
        card.appendChild(row);
      }
      wrap.appendChild(card);
    });
  }

  function onStyleChange() {
    const s = currentStyle();
    $("styleDesc").textContent = s
      ? `${s.description || ""}${s.poster_fill ? " · poster-wall 色板模式" : ""}`
      : "";
    buildRoleEditors(s);
    buildPaletteEditors(s);
    syncColorPanel();
  }

  function syncColorPanel() {
    const useDef = $("useDefaults").checked;
    $("colorPanel").classList.toggle("disabled", useDef);
  }

  function collectOverrides() {
    if ($("useDefaults").checked) {
      const font = $("font").value;
      return font ? { font } : {};
    }
    const overrides = { font: $("font").value || undefined };
    for (const role of ROLES) {
      overrides[role] = {};
      for (const ch of CHANNELS) {
        const el = document.querySelector(
          `input[type=color][data-role="${role}"][data-channel="${ch}"]`
        );
        if (el) overrides[role][ch] = el.value;
      }
    }
    const palInputs = document.querySelectorAll("input[data-palette]");
    if (palInputs.length) {
      const pals = [];
      palInputs.forEach((el) => {
        const i = Number(el.dataset.palette);
        if (!pals[i]) pals[i] = {};
        pals[i][el.dataset.pkey] = el.value;
      });
      overrides.palettes = pals.filter(Boolean);
    }
    return overrides;
  }

  async function refreshDetect() {
    const fd = new FormData();
    const file = $("lyricsFile").files[0];
    const text = $("lyricsText").value || "";
    if (file) fd.append("lyrics_file", file);
    else fd.append("lyrics_text", text);
    if (!file && !text.trim()) {
      $("tsBadge").classList.add("hidden");
      return;
    }
    try {
      const res = await fetch("/api/detect-lyrics", { method: "POST", body: fd });
      const data = await res.json();
      if (data.align_skipped) {
        $("tsBadge").textContent = `检测到 ${String(data.kind).toUpperCase()} 时间轴 → 将跳过对齐`;
        $("tsBadge").classList.remove("hidden");
      } else {
        $("tsBadge").classList.add("hidden");
      }
    } catch (_) {
      /* ignore */
    }
  }

  function setStatus(text, cls) {
    const el = $("jobStatus");
    el.textContent = text;
    el.className = "status " + (cls || "idle");
  }

  function resetResult() {
    $("preview").classList.add("hidden");
    $("preview").removeAttribute("src");
    $("dlMaster").classList.add("hidden");
    $("dlLite").classList.add("hidden");
    $("jobProgress").classList.add("hidden");
  }

  async function pollJob(id) {
    const res = await fetch(`/api/jobs/${id}`);
    if (!res.ok) throw new Error("查询任务失败");
    const job = await res.json();
    const skip = job.align_skipped ? "（已跳过 Whisper 对齐）" : "";
    setStatus(
      `任务 ${job.id}\n状态: ${job.status}\n进度: ${job.message || "-"}${skip}` +
        (job.error ? `\n错误: ${job.error}` : ""),
      job.status
    );

    if (job.status === "queued" || job.status === "running") {
      $("jobProgress").classList.remove("hidden");
      $("jobProgress").removeAttribute("value"); // indeterminate
      return false;
    }
    $("jobProgress").classList.add("hidden");
    if (job.status === "done") {
      const masterUrl = `/api/jobs/${id}/download`;
      $("preview").src = masterUrl;
      $("preview").classList.remove("hidden");
      $("dlMaster").href = masterUrl;
      $("dlMaster").classList.remove("hidden");
      if (job.has_lite) {
        $("dlLite").href = `/api/jobs/${id}/download?lite=1`;
        $("dlLite").classList.remove("hidden");
      }
      return true;
    }
    return true; // error — stop
  }

  async function onSubmit() {
    $("formError").textContent = "";
    resetResult();
    const audio = $("audio").files[0];
    if (!audio) {
      $("formError").textContent = "请选择音频文件";
      return;
    }
    const lyricsFile = $("lyricsFile").files[0];
    const lyricsText = $("lyricsText").value || "";
    if (!lyricsFile && !lyricsText.trim()) {
      $("formError").textContent = "请提供歌词文件或粘贴歌词";
      return;
    }

    const options = {
      style: $("style").value,
      style_overrides: collectOverrides(),
      title: $("title").value || "",
      author: $("author").value || "",
      gap_mode: $("gapMode").value,
      whisper_model: $("whisperModel").value,
      title_before_lyric: Number($("titleBefore").value),
      title_fade: Number($("titleFade").value),
      max_chars: Number($("maxChars").value),
      width: Number($("width").value),
      height: Number($("height").value),
      fps: Number($("fps").value),
      lite: $("lite").checked,
      bg_color: $("bgColor").value,
    };

    const fd = new FormData();
    fd.append("audio", audio);
    if (lyricsFile) fd.append("lyrics_file", lyricsFile);
    if (lyricsText.trim()) fd.append("lyrics_text", lyricsText);
    fd.append("options", JSON.stringify(options));

    $("submit").disabled = true;
    setStatus("提交中…", "running");
    try {
      const res = await fetch("/api/jobs", { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || res.statusText || "创建失败");
      }
      const id = data.id;
      setStatus(`任务 ${id}\n状态: ${data.status}`, data.status);
      if (pollTimer) clearInterval(pollTimer);
      const tick = async () => {
        try {
          const done = await pollJob(id);
          if (done && pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
            $("submit").disabled = false;
          }
        } catch (e) {
          setStatus(String(e.message || e), "error");
          if (pollTimer) clearInterval(pollTimer);
          pollTimer = null;
          $("submit").disabled = false;
        }
      };
      await tick();
      pollTimer = setInterval(tick, 1500);
    } catch (e) {
      $("formError").textContent = String(e.message || e);
      setStatus("提交失败", "error");
      $("submit").disabled = false;
    }
  }

  async function init() {
    const [stRes, fontRes] = await Promise.all([
      fetch("/api/styles"),
      fetch("/api/fonts"),
    ]);
    const stData = await stRes.json();
    styles = stData.styles || [];
    const sel = $("style");
    sel.innerHTML = "";
    styles.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s.name;
      opt.textContent = s.name;
      sel.appendChild(opt);
    });
    if (styles.some((s) => s.name === "dazibao-ivory")) {
      sel.value = "dazibao-ivory";
    }

    const fonts = (await fontRes.json()).fonts || [];
    const fsel = $("font");
    fsel.innerHTML = "";
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "（风格默认字体）";
    fsel.appendChild(empty);
    fonts.forEach((f) => {
      const opt = document.createElement("option");
      opt.value = f.path;
      opt.textContent = `${f.name} [${f.family_hint}]`;
      fsel.appendChild(opt);
    });

    onStyleChange();
    $("style").addEventListener("change", onStyleChange);
    $("useDefaults").addEventListener("change", syncColorPanel);
    $("lyricsText").addEventListener("input", refreshDetect);
    $("lyricsFile").addEventListener("change", refreshDetect);
    $("submit").addEventListener("click", onSubmit);
  }

  init().catch((e) => {
    $("formError").textContent = "初始化失败: " + e;
  });
})();
