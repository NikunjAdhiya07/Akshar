const state = {
  job: null,
  page: 0,
  mode: "gujarati",
  selected: null,
  drawing: false,
  draft: null,
  polling: false,
  reveal: false,
  reviewOpen: false,
};

const $ = (id) => document.getElementById(id);

function toast(message) {
  const node = $("toast");
  node.hidden = false;
  node.textContent = message;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { node.hidden = true; }, 4200);
}

async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Request failed");
  return data;
}

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

async function poll() {
  if (state.polling || !state.job) return;
  state.polling = true;
  try {
    while (state.job && (state.job.status === "processing" || state.job.status === "queued" || state.job.export_status === "running")) {
      state.job = await api(`/api/jobs/${state.job.id}`);
      render();
      await sleep(700);
    }
    render();
  } catch (error) {
    toast(error.message);
  } finally {
    state.polling = false;
  }
}

function startJob(job) {
  state.job = job;
  state.page = 0;
  state.selected = null;
  state.reviewOpen = false;
  render();
  poll();
}

async function upload(file) {
  const body = new FormData();
  body.append("file", file);
  body.append("force_ocr", $("force").checked ? "true" : "false");
  toast("Uploading…");
  startJob(await api("/api/jobs", { method: "POST", body }));
}

$("browse").onclick = () => $("file").click();
$("file").onchange = () => { if ($("file").files[0]) upload($("file").files[0]); };
const drop = $("drop");
["dragenter", "dragover"].forEach((name) => drop.addEventListener(name, (event) => {
  event.preventDefault();
  drop.classList.add("hot");
}));
["dragleave", "drop"].forEach((name) => drop.addEventListener(name, (event) => {
  event.preventDefault();
  drop.classList.remove("hot");
}));
drop.addEventListener("drop", (event) => {
  const file = event.dataTransfer.files[0];
  if (file) upload(file);
});
document.querySelectorAll("[data-sample]").forEach((button) => {
  button.onclick = async () => {
    try { startJob(await api(`/api/samples/${button.dataset.sample}`, { method: "POST" })); }
    catch (error) { toast(error.message); }
  };
});
document.querySelectorAll("[data-mode]").forEach((button) => {
  button.onclick = () => {
    state.mode = button.dataset.mode;
    document.querySelectorAll("[data-mode]").forEach((item) => item.classList.toggle("on", item === button));
    renderCanvas();
  };
});
$("zoom").oninput = () => renderCanvas();
$("export").onclick = async () => {
  try {
    state.job = await api(`/api/jobs/${state.job.id}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format: $("format").value, dpi: $("dpi").value }),
    });
    render();
    poll();
  } catch (error) { toast(error.message); }
};
$("retry").onclick = async () => {
  state.job = await api(`/api/jobs/${state.job.id}/pages/${state.page}/retry`, { method: "POST" });
  render();
  poll();
};
$("approve").onclick = async () => {
  const page = state.job.pages[state.page];
  state.job = await api(`/api/jobs/${state.job.id}/pages/${state.page}/approve?approved=${!page.approved}`, { method: "POST" });
  render();
};
$("add").onclick = () => {
  state.drawing = !state.drawing;
  $("add").textContent = state.drawing ? "Cancel drawing" : "Add text";
  $("manual").hidden = true;
  renderCanvas();
};
$("editor-form").onsubmit = async (event) => {
  event.preventDefault();
  const block = currentBlock();
  if (!block) return;
  state.job = await api(`/api/jobs/${state.job.id}/blocks/${block.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ translation: $("gujarati").value }),
  });
  toast("Translation saved");
  render();
};
$("retranslate").onclick = async () => {
  const block = currentBlock();
  if (!block) return;
  try {
    state.job = await api(`/api/jobs/${state.job.id}/blocks/${block.id}/retranslate`, { method: "POST" });
    render();
  } catch (error) { toast(error.message); }
};
$("remove").onclick = async () => {
  const block = currentBlock();
  if (!block) return;
  state.job = await api(`/api/jobs/${state.job.id}/blocks/${block.id}`, { method: "DELETE" });
  state.selected = null;
  state.reviewOpen = false;
  render();
};
$("manual").onsubmit = async (event) => {
  event.preventDefault();
  if (!state.draft) return;
  try {
    state.job = await api(`/api/jobs/${state.job.id}/pages/${state.page}/blocks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: $("manual-text").value, bbox: state.draft }),
    });
    state.drawing = false;
    state.draft = null;
    $("add").textContent = "Add text";
    $("manual-text").value = "";
    render();
  } catch (error) { toast(error.message); }
};
$("cancel-manual").onclick = () => {
  state.draft = null;
  state.drawing = false;
  $("add").textContent = "Add text";
  render();
};

function currentBlock() {
  return state.job?.blocks.find((block) => block.id === state.selected) || null;
}

function pageBlocks() {
  return (state.job?.blocks || []).filter((block) => block.page === state.page);
}

function conversionStage(job) {
  const page = job.pages?.[state.page] || job.pages?.[0];
  if (page && page.translation_status === "done") {
    return { id: "format", title: "Preserving original formatting…", progress: "84%" };
  }
  if (page && page.ocr_status === "done") {
    return { id: "translate", title: "Translating content…", progress: "58%" };
  }
  if (page && (page.ocr_status === "running" || page.ocr_status === "done")) {
    return { id: "analyze", title: "Analyzing document…", progress: "32%" };
  }
  return { id: "start", title: "Converting to Gujarati…", progress: "14%" };
}

function updateVeil(converting) {
  const veil = $("veil");
  if (converting) {
    clearTimeout(updateVeil._hide);
    veil.hidden = false;
    veil.classList.remove("leave");
    const stage = conversionStage(state.job);
    veil.dataset.stage = stage.id === "start" ? "analyze" : stage.id;
    const title = $("veil-title");
    if (title.textContent !== stage.title) {
      title.textContent = stage.title;
      title.classList.remove("swap");
      void title.offsetWidth;
      title.classList.add("swap");
    }
    $("veil-bar").style.width = stage.progress;
    return;
  }
  if (veil.hidden || veil.classList.contains("leave")) return;
  veil.classList.add("leave");
  state.reveal = true;
  updateVeil._hide = setTimeout(() => {
    veil.hidden = true;
    veil.classList.remove("leave");
  }, 620);
}

function render() {
  const job = state.job;
  const busy = job && (job.status === "processing" || job.status === "queued");
  updateVeil(!!busy);
  $("export").disabled = !job || job.status !== "ready" || job.export_status === "running";
  $("add").disabled = !job || job.status !== "ready";
  $("summary").hidden = !job;
  const empty = $("empty");
  if (empty) empty.hidden = !!job;
  const download = $("download");
  if (job && job.export_status === "ready" && job.export_file) {
    download.hidden = false;
    download.href = `/api/jobs/${job.id}/download?v=${job.revision}`;
    download.textContent = `Download ${job.export_name}`;
  } else download.hidden = true;
  setSteps(job);
  if (job) {
    const page = job.pages[state.page];
    const blocks = pageBlocks();
    const translated = blocks.filter((block) => block.translation && (block.preserved || /[\u0A80-\u0AFF]/.test(block.translation) || block.edited)).length;
    $("stats").innerHTML = [
      ["File", job.source_name],
      ["Pages", job.page_count],
      ["OCR", page ? page.ocr_status : "—"],
      ["Translation", page ? page.translation_status : "—"],
      ["Blocks", blocks.length],
      ["Translated", translated],
      ["Warnings", (job.issues || []).length],
    ].map(([key, value]) => `<dt>${key}</dt><dd>${escapeHtml(String(value))}</dd>`).join("");
    $("pages").innerHTML = job.pages.map((item) => `<button data-page="${item.index}" class="${item.index === state.page ? "on" : ""}">Page ${item.index + 1}${item.approved ? " · approved" : ""}${item.error ? " · needs attention" : ""}</button>`).join("");
    $("pages").querySelectorAll("button").forEach((button) => {
      button.onclick = () => { state.page = Number(button.dataset.page); state.selected = null; state.reviewOpen = false; render(); };
    });
    $("retry").hidden = !page || !page.error;
    $("approve").hidden = !page || job.status !== "ready";
    $("approve").textContent = page && page.approved ? "Approved" : "Approve page";
    $("log").innerHTML = (job.log || []).slice(-8).map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  }
  renderIssues();
  renderBlocks();
  renderEditor();
  renderCanvas();
}

function setSteps(job) {
  const names = ["Upload", "Detect", "Translate", "Preview", "Export"];
  let active = 0;
  if (job) {
    active = 1;
    const page = job.pages[state.page];
    if (page && page.ocr_status === "done") active = 2;
    if (page && page.translation_status === "done") active = 3;
    if (job.status === "ready") active = 3;
    if (job.export_status === "ready") active = 4;
  }
  $("steps").innerHTML = names.map((name, index) => `<li class="${index <= active ? "on" : ""}">${name}</li>`).join("");
}

$("review-toggle").onclick = () => {
  if (!state.selected) {
    state.reviewOpen = false;
    renderIssues();
    return;
  }
  state.reviewOpen = !state.reviewOpen;
  renderIssues();
};

function renderIssues() {
  const open = !!(state.reviewOpen && state.selected && currentBlock());
  const body = $("review-body");
  const toggle = $("review-toggle");
  toggle.classList.toggle("open", open);
  toggle.setAttribute("aria-expanded", open ? "true" : "false");
  body.hidden = !open;
  if (!open) {
    $("review-quote").textContent = "";
    $("review-source").textContent = "";
    $("issues").innerHTML = "";
    return;
  }
  const block = currentBlock();
  const gujarati = (block.translation || "").trim();
  const english = (block.text || "").trim();
  $("review-quote").textContent = gujarati || english;
  $("review-source").textContent = gujarati && gujarati !== english ? english : "";
  const issues = (state.job?.issues || []).filter((issue) => issue.block_id === block.id);
  $("issues").innerHTML = issues.length
    ? issues.map((issue) => `<li class="${issue.severity}">${escapeHtml(issue.message)}</li>`).join("")
    : "<li>No warnings for this text.</li>";
}

function renderBlocks() {
  const blocks = pageBlocks();
  $("blocks").innerHTML = blocks.map((block) => `<li><button data-block="${block.id}" class="${block.id === state.selected ? "on" : ""}"><strong>${escapeHtml(block.kind)}</strong><br>${escapeHtml(block.translation || block.text)}</button></li>`).join("");
  $("blocks").querySelectorAll("button").forEach((button) => {
    button.onclick = () => { state.selected = button.dataset.block; state.reviewOpen = false; render(); };
  });
}

function renderEditor() {
  const block = currentBlock();
  $("editor-empty").hidden = !!block || !!state.draft;
  $("editor-form").hidden = !block;
  $("manual").hidden = !state.draft;
  if (!block) return;
  $("kind").textContent = `${block.kind}${block.preserved ? " · kept as written" : ""}${block.edited ? " · edited" : ""}`;
  $("english").value = block.text;
  if (document.activeElement !== $("gujarati")) $("gujarati").value = block.translation || "";
}

function renderCanvas() {
  const canvas = $("canvas");
  const job = state.job;
  if (!job || !job.pages.length) {
    if (!$("empty")) return;
    return;
  }
  if (job.status === "processing" || job.status === "queued") {
    canvas.innerHTML = "";
    return;
  }
  const page = job.pages[state.page];
  if (!page) return;
  if (!page.width_px) {
    canvas.innerHTML = `<p class="empty">Reading page ${state.page + 1}…</p>`;
    return;
  }
  canvas.classList.toggle("drawing", state.drawing);
  canvas.classList.toggle("reveal", state.reveal);
  state.reveal = false;
  const zoom = Number($("zoom").value) / 100;
  const original = `/api/jobs/${job.id}/pages/${state.page}/original?v=${job.revision}`;
  const preview = `/api/jobs/${job.id}/pages/${state.page}/preview?v=${job.revision}`;
  const hasPreview = page.render_status === "done";
  canvas.style.setProperty("--zoom", zoom);
  if (state.mode === "split") {
    canvas.innerHTML = `<div class="split"><div>${sheet("Original", original, false)}</div><div>${sheet("Gujarati", hasPreview ? preview : original, true)}</div></div>`;
  } else if (state.mode === "overlay") {
    canvas.innerHTML = `<div class="sheet overlay" style="width:min(100%, calc(720px * ${zoom}))"><img src="${original}" alt="Original"><img class="top-image" src="${hasPreview ? preview : original}" alt="Gujarati" style="opacity:.55">${hits(page, true)}</div>`;
  } else if (state.mode === "original") {
    canvas.innerHTML = sheet("Original", original, false);
  } else {
    canvas.innerHTML = sheet("Gujarati", hasPreview ? preview : original, true);
  }
  bindHits();
  bindDraw();
}

function sheet(label, src, interactive) {
  const page = state.job.pages[state.page];
  const zoom = Number($("zoom").value) / 100;
  return `<div class="sheet" style="width:min(100%, calc(720px * ${zoom}))"><span class="caption">${label}</span><img src="${src}" alt="${label}">${interactive ? hits(page, true) : ""}</div>`;
}

function hits(page) {
  return pageBlocks().map((block) => {
    const box = block.draw_bbox || block.bbox;
    const style = `left:${pct(box[0], page.width_pt)};top:${pct(box[1], page.height_pt)};width:${pct(box[2] - box[0], page.width_pt)};height:${pct(box[3] - box[1], page.height_pt)};`;
    const cls = `hit${block.id === state.selected ? " on" : ""}`;
    return `<button class="${cls}" style="${style}" data-block="${block.id}" title="${escapeHtml(block.text)}"></button>`;
  }).join("");
}

function pct(value, total) { return `${(value / total) * 100}%`; }

function bindHits() {
  document.querySelectorAll(".hit").forEach((button) => {
    button.onclick = (event) => {
      event.stopPropagation();
      state.selected = button.dataset.block;
      state.reviewOpen = true;
      render();
    };
  });
}

function bindDraw() {
  const image = document.querySelector(".sheet img");
  if (!image || !state.drawing) return;
  let start = null;
  image.onmousedown = (event) => {
    const rect = image.getBoundingClientRect();
    start = point(event, rect);
  };
  window.onmouseup = (event) => {
    if (!start) return;
    const rect = image.getBoundingClientRect();
    const end = point(event, rect);
    const page = state.job.pages[state.page];
    const x0 = Math.min(start.x, end.x) * page.width_pt;
    const y0 = Math.min(start.y, end.y) * page.height_pt;
    const x1 = Math.max(start.x, end.x) * page.width_pt;
    const y1 = Math.max(start.y, end.y) * page.height_pt;
    start = null;
    if (x1 - x0 < 8 || y1 - y0 < 8) return;
    state.draft = [x0, y0, x1, y1];
    state.selected = null;
    state.reviewOpen = false;
    render();
  };
}

function point(event, rect) {
  return {
    x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
    y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
  };
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

render();
