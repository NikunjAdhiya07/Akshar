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
  waitingUpload: false,
  converting: false,
  queue: [],
  activeItemId: null,
};

const $ = (id) => document.getElementById(id);
const ACCEPT = new Set(["application/pdf", "image/png", "image/jpeg", "image/jpg"]);
const EXT = /\.(pdf|png|jpe?g)$/i;

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

function uid() {
  return `f${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

function isAllowed(file) {
  return ACCEPT.has(file.type) || EXT.test(file.name);
}

function focusResult() {
  const busy = state.waitingUpload || state.converting || !!state.job;
  document.body.classList.toggle("has-job", busy);
  const stage = document.querySelector(".stage");
  if (stage && busy) stage.scrollIntoView({ behavior: "smooth", block: "start" });
}

function stopFakeProgress() {
  clearInterval(stopFakeProgress._timer);
  stopFakeProgress._timer = null;
}

function startFakeProgress(fileName) {
  stopFakeProgress();
  if (fileName) $("veil-file").textContent = fileName;
  const steps = [
    { pct: "18%", id: "analyze", title: "Opening document…" },
    { pct: "36%", id: "analyze", title: "Analyzing layout…" },
    { pct: "54%", id: "translate", title: "English → Gujarati…" },
    { pct: "72%", id: "translate", title: "Translating content…" },
    { pct: "88%", id: "format", title: "Rebuilding the page…" },
    { pct: "94%", id: "format", title: "Preparing preview…" },
  ];
  let index = 0;
  const apply = () => {
    const step = steps[Math.min(index, steps.length - 1)];
    const veil = $("veil");
    veil.dataset.stage = step.id;
    $("veil-title").textContent = step.title;
    $("veil-bar").style.width = step.pct;
    if (index < steps.length - 1) index += 1;
  };
  apply();
  stopFakeProgress._timer = setInterval(apply, 1500);
}

function showBusyVeil(title, fileName) {
  const veil = $("veil");
  clearTimeout(updateVeil._hide);
  veil.hidden = false;
  veil.classList.remove("leave");
  veil.dataset.stage = "analyze";
  $("veil-title").textContent = title || "Converting to Gujarati…";
  $("veil-file").textContent = fileName || "";
  $("veil-bar").style.width = "12%";
  startFakeProgress(fileName);
}

function addFiles(fileList) {
  const files = [...fileList].filter((file) => {
    if (isAllowed(file)) return true;
    toast(`${file.name} is not a supported file`);
    return false;
  });
  if (!files.length) return;
  for (const file of files) {
    const previewUrl = file.type.startsWith("image/") ? URL.createObjectURL(file) : null;
    state.queue.push({
      id: uid(),
      file,
      name: file.name,
      size: file.size,
      previewUrl,
      status: "waiting",
      job: null,
      error: null,
    });
  }
  renderQueue();
  focusResult();
}

function removeQueueItem(id) {
  const item = state.queue.find((entry) => entry.id === id);
  if (!item || item.status === "converting") return;
  if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
  state.queue = state.queue.filter((entry) => entry.id !== id);
  if (state.activeItemId === id) {
    state.activeItemId = null;
    state.job = null;
  }
  renderQueue();
  render();
}

function clearWaiting() {
  state.queue.filter((item) => item.status === "waiting").forEach((item) => {
    if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
  });
  state.queue = state.queue.filter((item) => item.status !== "waiting");
  renderQueue();
}

function resetForReupload() {
  state.job = null;
  state.page = 0;
  state.selected = null;
  state.reviewOpen = false;
  state.activeItemId = null;
  state.waitingUpload = false;
  document.body.classList.remove("has-job");
  $("file").value = "";
  $("file-bulk").value = "";
  render();
  renderQueue();
  $("drop")?.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function convertItem(item) {
  item.status = "converting";
  item.error = null;
  renderQueue();
  state.waitingUpload = true;
  state.converting = true;
  showBusyVeil("Converting to Gujarati…", item.name);
  focusResult();
  const body = new FormData();
  body.append("file", item.file);
  body.append("force_ocr", $("force").checked ? "true" : "false");
  try {
    const job = await api("/api/jobs", { method: "POST", body });
    item.job = job;
    if (job.status === "error") {
      item.status = "failed";
      item.error = job.error || "Conversion failed";
    } else {
      item.status = "completed";
      openCompleted(item.id, { silent: true });
    }
  } catch (error) {
    item.status = "failed";
    item.error = error.message;
  } finally {
    state.waitingUpload = false;
    const moreWaiting = state.queue.some((entry) => entry.status === "waiting");
    if (!(state.converting && moreWaiting)) {
      stopFakeProgress();
      updateVeil(false);
    }
    renderQueue();
    render();
  }
}

async function confirmConversion() {
  if (state.converting) return;
  const waiting = state.queue.filter((item) => item.status === "waiting");
  if (!waiting.length) {
    toast("Add a file first");
    return;
  }
  state.converting = true;
  $("confirm-convert").disabled = true;
  for (const item of waiting) {
    await convertItem(item);
  }
  state.converting = false;
  $("confirm-convert").disabled = false;
  const failed = waiting.filter((item) => item.status === "failed").length;
  const done = waiting.filter((item) => item.status === "completed").length;
  if (done && !failed) toast(done === 1 ? "Translation ready" : `${done} files converted`);
  else if (done && failed) toast(`${done} completed, ${failed} failed`);
  else if (failed) toast("Conversion failed");
  focusResult();
}

function openCompleted(id, { silent = false } = {}) {
  const item = state.queue.find((entry) => entry.id === id && entry.status === "completed" && entry.job);
  if (!item) return;
  state.activeItemId = id;
  state.job = item.job;
  state.page = 0;
  state.selected = null;
  state.reviewOpen = false;
  renderQueue();
  render();
  focusResult();
  if (!silent) toast(item.name);
  if (item.job.status === "processing" || item.job.status === "queued") poll();
}

async function poll() {
  if (state.polling || !state.job) return;
  state.polling = true;
  try {
    while (state.job && (state.job.status === "processing" || state.job.status === "queued" || state.job.export_status === "running")) {
      state.job = await api(`/api/jobs/${state.job.id}`);
      const item = state.queue.find((entry) => entry.id === state.activeItemId);
      if (item) item.job = state.job;
      render();
      await sleep(700);
    }
    render();
    if (state.job && state.job.status === "ready") focusResult();
  } catch (error) {
    toast(error.message);
  } finally {
    state.polling = false;
  }
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function statusLabel(status) {
  return ({
    waiting: "Waiting",
    converting: "Converting",
    completed: "Completed",
    failed: "Failed",
  })[status] || status;
}

function renderQueue() {
  const waiting = state.queue.filter((item) => item.status === "waiting");
  const active = state.queue.filter((item) => item.status === "converting");
  const done = state.queue.filter((item) => item.status === "completed" || item.status === "failed");

  $("queue-panel").hidden = !waiting.length;
  $("active-panel").hidden = !active.length;
  $("done-panel").hidden = !done.length;
  $("queue-confirm").hidden = !waiting.length || state.converting;
  $("clear-waiting").hidden = !waiting.length || state.converting;
  $("confirm-convert").textContent = waiting.length > 1 ? `Confirm All for Conversion (${waiting.length})` : "Confirm for Conversion";

  $("queue-waiting").innerHTML = waiting.map(queueCard).join("");
  $("queue-active").innerHTML = active.map(queueCard).join("");
  $("queue-done").innerHTML = done.map(queueCard).join("");

  document.querySelectorAll("[data-remove]").forEach((button) => {
    button.onclick = () => removeQueueItem(button.dataset.remove);
  });
  document.querySelectorAll("[data-open]").forEach((button) => {
    button.onclick = () => openCompleted(button.dataset.open);
  });
}

function queueCard(item) {
  const thumb = item.previewUrl
    ? `<img class="q-thumb" src="${item.previewUrl}" alt="">`
    : `<div class="q-thumb pdf" aria-hidden="true"><span>PDF</span></div>`;
  const actions = item.status === "waiting"
    ? `<button type="button" class="ghost danger tiny" data-remove="${item.id}">Remove</button>`
    : item.status === "completed"
      ? `<button type="button" class="primary tiny" data-open="${item.id}">Open</button>`
      : item.status === "failed"
        ? `<button type="button" class="ghost danger tiny" data-remove="${item.id}">Dismiss</button>`
        : "";
  const on = item.id === state.activeItemId ? " on" : "";
  return `<li class="queue-item${on}" data-status="${item.status}">
    ${thumb}
    <div class="q-meta">
      <strong title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</strong>
      <span>${formatBytes(item.size)} · ${statusLabel(item.status)}</span>
      ${item.error ? `<em class="q-error">${escapeHtml(item.error)}</em>` : ""}
    </div>
    <div class="q-actions">${actions}</div>
  </li>`;
}

function updateVeil(converting) {
  const veil = $("veil");
  if (converting || state.waitingUpload || state.converting) {
    clearTimeout(updateVeil._hide);
    veil.hidden = false;
    veil.classList.remove("leave");
    if (state.job && !state.waitingUpload) {
      stopFakeProgress();
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
    }
    return;
  }
  stopFakeProgress();
  if (veil.hidden || veil.classList.contains("leave")) return;
  veil.classList.add("leave");
  state.reveal = true;
  updateVeil._hide = setTimeout(() => {
    veil.hidden = true;
    veil.classList.remove("leave");
    $("veil-file").textContent = "";
  }, 620);
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

function render() {
  const job = state.job;
  const busy = state.waitingUpload || state.converting || (job && (job.status === "processing" || job.status === "queued"));
  updateVeil(!!busy);
  document.body.classList.toggle("has-job", !!job || state.waitingUpload || state.converting);
  $("stage-bar").hidden = !job || job.status !== "ready";
  $("export").disabled = !job || job.status !== "ready" || job.export_status === "running";
  $("add").disabled = !job || job.status !== "ready";
  $("summary").hidden = !job;
  const empty = $("empty");
  if (empty) empty.hidden = !!job || state.waitingUpload || state.converting;
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
  } else {
    $("log").innerHTML = "";
  }
  renderIssues();
  renderBlocks();
  renderEditor();
  renderCanvas();
}

function setSteps(job) {
  const names = ["Upload", "Detect", "Translate", "Preview", "Export"];
  let active = 0;
  if (state.queue.some((item) => item.status === "waiting")) active = 0;
  if (state.converting || state.waitingUpload) active = 1;
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

$("browse").onclick = () => $("file").click();
$("browse-bulk").onclick = () => $("file-bulk").click();
$("file").onchange = () => { if ($("file").files[0]) addFiles($("file").files); $("file").value = ""; };
$("file-bulk").onchange = () => { if ($("file-bulk").files.length) addFiles($("file-bulk").files); $("file-bulk").value = ""; };
$("confirm-convert").onclick = () => confirmConversion();
$("clear-waiting").onclick = () => clearWaiting();
$("reupload").onclick = () => resetForReupload();

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
  if (event.dataTransfer.files.length) addFiles(event.dataTransfer.files);
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
    showBusyVeil("Exporting…", state.job?.source_name);
    state.job = await api(`/api/jobs/${state.job.id}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format: $("format").value, dpi: $("dpi").value }),
    });
    const item = state.queue.find((entry) => entry.id === state.activeItemId);
    if (item) item.job = state.job;
    stopFakeProgress();
    render();
    poll();
  } catch (error) {
    stopFakeProgress();
    updateVeil(false);
    toast(error.message);
  }
};
$("retry").onclick = async () => {
  showBusyVeil("Retrying page…", state.job?.source_name);
  state.job = await api(`/api/jobs/${state.job.id}/pages/${state.page}/retry`, { method: "POST" });
  const item = state.queue.find((entry) => entry.id === state.activeItemId);
  if (item) item.job = state.job;
  stopFakeProgress();
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
  if (!job || !job.pages.length) return;
  if (job.status === "processing" || job.status === "queued" || state.waitingUpload || state.converting) {
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
  return String(value).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

renderQueue();
render();
