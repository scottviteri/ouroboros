/* ouroboros generation explorer */
"use strict";

const state = {
  nodes: [],        // timeline entries: commits oldest-first, maybe + worktree
  idx: 0,
  detail: null,     // detail for the selected node
  chat: [],         // [{role, content}] for the API
  asking: false,
  runPoll: null,
};

const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------------ fetch

async function api(path, opts) {
  const resp = await fetch(path, opts);
  let body;
  try { body = await resp.json(); }
  catch { body = { error: `bad response (${resp.status})` }; }
  if (!resp.ok) throw new Error(body.error || `HTTP ${resp.status}`);
  return body;
}

async function post(path, payload) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

// ----------------------------------------------------------------- escape

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ------------------------------------------------------------ highlighting

const EL_KEYWORDS = new Set([
  "defun", "defvar", "defconst", "defmacro", "setq", "let", "let*", "lambda",
  "if", "when", "unless", "cond", "progn", "while", "dolist", "dotimes",
  "condition-case", "error", "provide", "require", "interactive", "quote",
  "and", "or", "not",
]);

function highlightElisp(src) {
  let out = "", i = 0;
  const n = src.length;
  while (i < n) {
    const c = src[i];
    if (c === ";") {
      let j = src.indexOf("\n", i);
      if (j < 0) j = n;
      out += `<span class="el-comment">${esc(src.slice(i, j))}</span>`;
      i = j;
    } else if (c === '"') {
      let j = i + 1;
      while (j < n && src[j] !== '"') { if (src[j] === "\\") j++; j++; }
      j = Math.min(j + 1, n);
      out += `<span class="el-string">${esc(src.slice(i, j))}</span>`;
      i = j;
    } else if (c === "(" || c === ")") {
      out += `<span class="el-paren">${c}</span>`;
      i++;
    } else if (/[A-Za-z*-]/.test(c)) {
      let j = i;
      while (j < n && /[^\s()";]/.test(src[j])) j++;
      const word = src.slice(i, j);
      if (EL_KEYWORDS.has(word)) {
        out += `<span class="el-keyword">${esc(word)}</span>`;
      } else if (word.startsWith("organism-")) {
        out += `<span class="el-symbol">${esc(word)}</span>`;
      } else {
        out += esc(word);
      }
      i = j;
    } else {
      out += esc(c);
      i++;
    }
  }
  return out;
}

function highlightDiff(diff) {
  return diff.split("\n").map((line) => {
    const e = esc(line);
    if (line.startsWith("+++") || line.startsWith("---") ||
        line.startsWith("diff ") || line.startsWith("index ") ||
        line.startsWith("new file")) {
      return `<span class="d-meta">${e}</span>`;
    }
    if (line.startsWith("@@")) return `<span class="d-hunk">${e}</span>`;
    if (line.startsWith("+")) return `<span class="d-add">${e}</span>`;
    if (line.startsWith("-")) return `<span class="d-del">${e}</span>`;
    return e;
  }).join("\n");
}

// Minimal markdown for chat replies: fenced code, inline code, bold.
function renderChatBody(text) {
  const parts = text.split(/```/);
  let html = "";
  for (let k = 0; k < parts.length; k++) {
    if (k % 2 === 1) {
      const body = parts[k].replace(/^[a-zA-Z-]*\n/, "");
      html += `<pre>${esc(body)}</pre>`;
    } else {
      html += esc(parts[k])
        .replace(/`([^`\n]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    }
  }
  return html;
}

// ----------------------------------------------------------------- loading

async function loadGenerations(selectSha) {
  const data = await api("/api/generations");
  state.nodes = data.generations;
  if (data.worktree_dirty) {
    state.nodes = state.nodes.concat([{
      sha: "WORKTREE", short: "worktree", date: null,
      subject: "uncommitted working tree", organism_changed: true,
      generation: null, lines: 0,
    }]);
  }
  let idx = state.nodes.length - 1;
  if (selectSha) {
    const found = state.nodes.findIndex((g) => g.sha === selectSha);
    if (found >= 0) idx = found;
  }
  buildTimeline();
  $("gen-slider").max = String(state.nodes.length - 1);
  await select(idx);
}

async function select(idx) {
  idx = Math.max(0, Math.min(idx, state.nodes.length - 1));
  state.idx = idx;
  const node = state.nodes[idx];
  $("gen-slider").value = String(idx);
  renderStepper();
  renderTimelineSelection();
  try {
    state.detail = await api(`/api/generation/${node.sha}`);
  } catch (e) {
    toast(e.message, true);
    return;
  }
  renderDetail();
}

// --------------------------------------------------------------- rendering

function nodeName(node) {
  if (node.sha === "WORKTREE") return "working tree";
  if (node.generation !== null && node.organism_changed) {
    return `generation ${node.generation}`;
  }
  return node.subject.length > 34 ? node.subject.slice(0, 33) + "…" : node.subject;
}

function buildTimeline() {
  const tl = $("timeline");
  tl.innerHTML = "";
  state.nodes.forEach((node, i) => {
    const btn = document.createElement("button");
    btn.className = "tl-node" +
      (node.organism_changed ? " changed" : "") +
      (node.sha === "WORKTREE" ? " worktree" : "");
    btn.title = `${node.short} — ${node.subject}`;
    btn.innerHTML = `<span class="tl-dot"></span><span class="tl-label"></span>`;
    btn.querySelector(".tl-label").textContent =
      node.sha === "WORKTREE" ? "now" :
      (node.generation !== null && node.organism_changed)
        ? `gen ${node.generation}` : node.short;
    btn.addEventListener("click", () => select(i));
    tl.appendChild(btn);
  });
}

function renderTimelineSelection() {
  document.querySelectorAll(".tl-node").forEach((el, i) => {
    el.classList.toggle("selected", i === state.idx);
  });
}

function renderStepper() {
  const node = state.nodes[state.idx];
  const title = $("gen-title");
  title.innerHTML = "";
  const genSpan = document.createElement("span");
  genSpan.className = "gen-num";
  genSpan.textContent = nodeName(node);
  const shaSpan = document.createElement("span");
  shaSpan.className = "sha";
  shaSpan.textContent =
    `  ${node.short}  ·  ${state.idx + 1}/${state.nodes.length}`;
  title.append(genSpan, shaSpan);

  $("btn-first").disabled = $("btn-prev").disabled = state.idx === 0;
  $("btn-next").disabled = $("btn-last").disabled =
    state.idx === state.nodes.length - 1;
  $("btn-restore").disabled = node.sha === "WORKTREE";
  $("chat-context").textContent = `context: ${nodeName(node)} (${node.short})`;
}

function renderDetail() {
  const d = state.detail;
  const meta = $("commit-meta");
  meta.innerHTML = "";
  const subject = document.createElement("span");
  subject.className = "subject";
  subject.textContent = d.subject;
  meta.appendChild(subject);
  if (d.date) {
    const when = document.createElement("span");
    when.className = "mono";
    when.textContent = d.date.replace("T", " ");
    meta.appendChild(when);
  }
  if (d.parsed.generation !== null) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = `organism-generation ${d.parsed.generation}`;
    meta.appendChild(badge);
  }
  if (d.parsed.model) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = d.parsed.model;
    meta.appendChild(badge);
  }
  if (d.sha === "WORKTREE") {
    const badge = document.createElement("span");
    badge.className = "badge warn";
    badge.textContent = "uncommitted";
    meta.appendChild(badge);
  }

  $("source-pre").innerHTML = d.organism
    ? highlightElisp(d.organism)
    : `<span class="empty-msg">organism.el does not exist at this commit.</span>`;

  $("diff-pre").innerHTML = d.diff.trim()
    ? highlightDiff(d.diff)
    : `<span class="empty-msg">this commit did not touch organism.el.</span>`;

  setText("prompt-pre", d.parsed.prompt,
    "no organism-prompt found — the organism may have restructured itself.");
  setText("journal-pre", d.journal,
    "the journal does not exist yet at this generation.");
  $("log-pre").innerHTML = d.parsed.log
    ? highlightElisp(d.parsed.log)
    : `<span class="empty-msg">no organism-log at this generation (it appeared at generation 2).</span>`;
  setText("note-pre", d.parsed.note && d.parsed.note.trim() ? d.parsed.note : null,
    "organism-note is empty at this generation.");
}

function setText(id, value, emptyMsg) {
  const el = $(id);
  if (value) {
    el.textContent = value;
  } else {
    el.innerHTML = `<span class="empty-msg">${esc(emptyMsg)}</span>`;
  }
}

// --------------------------------------------------------------------- chat

function addMsg(role, content, cls) {
  const empty = document.querySelector(".chat-empty");
  if (empty) empty.remove();
  const div = document.createElement("div");
  div.className = `msg ${cls || role}`;
  const roleEl = document.createElement("div");
  roleEl.className = "msg-role";
  roleEl.textContent = role === "user" ? "you" : "claude";
  const body = document.createElement("div");
  body.className = "msg-body";
  if (role === "assistant" && !cls) body.innerHTML = renderChatBody(content);
  else body.textContent = content;
  div.append(roleEl, body);
  $("chat-log").appendChild(div);
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
  return div;
}

async function ask(question) {
  if (state.asking || !question.trim()) return;
  state.asking = true;
  $("chat-send").disabled = true;
  const node = state.nodes[state.idx];
  addMsg("user", question);
  const thinking = addMsg("assistant", "thinking", "assistant thinking");
  thinking.querySelector(".msg-body").textContent = "";
  try {
    const resp = await post("/api/ask", {
      question,
      sha: node.sha,
      history: state.chat,
    });
    thinking.remove();
    addMsg("assistant", resp.reply);
    const last = $("chat-log").lastChild;
    const metaEl = document.createElement("div");
    metaEl.className = "msg-meta";
    metaEl.textContent = `${resp.model} · about ${nodeName(node)}`;
    last.appendChild(metaEl);
    state.chat.push({ role: "user", content: question });
    state.chat.push({ role: "assistant", content: resp.reply });
  } catch (e) {
    thinking.remove();
    addMsg("assistant", e.message, "error");
  } finally {
    state.asking = false;
    $("chat-send").disabled = false;
  }
}

// ------------------------------------------------------------ kernel runs

async function startRun() {
  try {
    await post("/api/run", { steps: 1 });
  } catch (e) {
    toast(e.message, true);
    return;
  }
  $("console").classList.remove("hidden");
  $("btn-run").disabled = true;
  pollRun();
}

async function pollRun() {
  clearTimeout(state.runPoll);
  let status;
  try {
    status = await api("/api/run/status");
  } catch {
    state.runPoll = setTimeout(pollRun, 1500);
    return;
  }
  const pre = $("console-pre");
  const stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
  pre.textContent = status.output || "(waiting for kernel output…)";
  if (stick) pre.scrollTop = pre.scrollHeight;
  const st = $("console-status");
  if (status.running) {
    st.textContent = "running…";
    st.className = "console-status running";
    state.runPoll = setTimeout(pollRun, 1000);
  } else {
    const failed = status.exit_code !== 0;
    st.textContent = status.started
      ? `finished (exit ${status.exit_code})` : "idle";
    st.className = "console-status" + (failed ? " failed" : "");
    $("btn-run").disabled = false;
    if (status.started) {
      loadGenerations(null).catch((e) => toast(e.message, true));
    }
  }
}

async function restore() {
  const node = state.nodes[state.idx];
  if (node.sha === "WORKTREE") return;
  const name = nodeName(node);
  if (!window.confirm(
    `Restore organism.el from ${name} (${node.short}) into the working tree?\n\n` +
    `This runs: git checkout ${node.short} -- sandbox/organism.el\n` +
    `Nothing is committed; the kernel commits on its next run.`)) return;
  try {
    await post("/api/restore", { sha: node.sha });
    toast(`organism.el restored from ${name}`);
    await loadGenerations("WORKTREE");
  } catch (e) {
    toast(e.message, true);
  }
}

// ------------------------------------------------------------------- toast

let toastTimer;
function toast(message, isError) {
  const el = $("toast");
  el.textContent = message;
  el.className = "toast" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 3500);
}

// ------------------------------------------------------------------ wiring

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) =>
      t.classList.toggle("active", t === tab));
    document.querySelectorAll(".pane").forEach((p) =>
      p.classList.toggle("active", p.id === `pane-${tab.dataset.tab}`));
  });
});

$("btn-first").addEventListener("click", () => select(0));
$("btn-prev").addEventListener("click", () => select(state.idx - 1));
$("btn-next").addEventListener("click", () => select(state.idx + 1));
$("btn-last").addEventListener("click", () => select(state.nodes.length - 1));
$("gen-slider").addEventListener("input", (e) => select(Number(e.target.value)));
$("btn-run").addEventListener("click", startRun);
$("btn-restore").addEventListener("click", restore);
$("btn-console-close").addEventListener("click", () =>
  $("console").classList.add("hidden"));

$("btn-clear-chat").addEventListener("click", () => {
  state.chat = [];
  $("chat-log").innerHTML =
    `<div class="chat-empty">conversation cleared — context resets to whatever generation is selected.</div>`;
});

$("chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("chat-input");
  const q = input.value;
  input.value = "";
  ask(q);
});

$("chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("chat-form").requestSubmit();
  }
});

document.addEventListener("click", (e) => {
  if (e.target.classList.contains("suggestion")) ask(e.target.textContent);
});

document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") return;
  if (e.key === "ArrowLeft") select(state.idx - 1);
  else if (e.key === "ArrowRight") select(state.idx + 1);
  else if (e.key === "Home") select(0);
  else if (e.key === "End") select(state.nodes.length - 1);
});

// ------------------------------------------------------------------- start

loadGenerations(null).catch((e) => toast(e.message, true));
pollRun();
