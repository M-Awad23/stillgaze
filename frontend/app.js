const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const messagesEl = document.querySelector("#messages");
const sendButton = document.querySelector("#sendButton");
const statusPanel = document.querySelector(".status-panel");
const statusLabel = document.querySelector("#statusLabel");
const modelSelect = document.querySelector("#modelSelect");
const temperature = document.querySelector("#temperature");
const temperatureValue = document.querySelector("#temperatureValue");
const responseLimit = document.querySelector("#responseLimit");
const themeToggle = document.querySelector("#themeToggle");
const newChatButton = document.querySelector("#newChatButton");
const chatList = document.querySelector("#chatList");
const chatMenu = document.querySelector("#chatMenu");
const toolApproval = document.querySelector("#toolApproval");
const memoryForm = document.querySelector("#memoryForm");
const memoryInput = document.querySelector("#memoryInput");
const memoryList = document.querySelector("#memoryList");

const LEGACY_CHAT_STORE_KEY = "stillgaze-chats";
const ACTIVE_CHAT_KEY = "stillgaze-active-chat";
const SELECTED_MODEL_KEY = "stillgaze-selected-model";
const TITLE_STOPWORDS = new Set([
  "a", "an", "and", "are", "about", "can", "could", "do", "does", "for", "from",
  "help", "how", "i", "in", "is", "it", "me", "my", "of", "on", "please", "soon",
  "the", "this", "to", "with", "would", "what", "when", "where", "why", "should",
  "you", "your",
]);

const savedTheme = localStorage.getItem("stillgaze-theme");
const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
const useDarkTheme = savedTheme ? savedTheme === "dark" : prefersDark;
document.body.classList.toggle("dark", useDarkTheme);
themeToggle.checked = useDarkTheme;

let chats = [];
let memories = [];
let activeChatId = localStorage.getItem(ACTIVE_CHAT_KEY);
let menuChatId = null;
let pendingToolCall = null;

function visibleChats() {
  return chats
    .filter((chat) => !chat.archived)
    .sort((a, b) => Number(b.pinned) - Number(a.pinned) || new Date(b.updated_at) - new Date(a.updated_at));
}

function archivedChats() {
  return chats
    .filter((chat) => chat.archived)
    .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
}

function getActiveChat() {
  return chats.find((chat) => chat.id === activeChatId) || null;
}

function persistActiveChat() {
  if (activeChatId) {
    localStorage.setItem(ACTIVE_CHAT_KEY, activeChatId);
  } else {
    localStorage.removeItem(ACTIVE_CHAT_KEY);
  }
}

async function apiJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  if (response.status === 204) return null;

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Request failed");
  }
  return data;
}

async function loadChats() {
  chats = await apiJson("/api/chats");
  if (chats.length === 0) {
    await migrateLegacyChats();
    chats = await apiJson("/api/chats");
  }

  if (!chats.some((chat) => chat.id === activeChatId && !chat.archived)) {
    activeChatId = visibleChats()[0]?.id || null;
    persistActiveChat();
  }
  render();
}

async function loadMemories() {
  memories = await apiJson("/api/memory");
  renderMemories();
}

async function migrateLegacyChats() {
  let legacyChats = [];
  try {
    legacyChats = JSON.parse(localStorage.getItem(LEGACY_CHAT_STORE_KEY) || "[]");
  } catch {
    localStorage.removeItem(LEGACY_CHAT_STORE_KEY);
  }
  if (!Array.isArray(legacyChats) || legacyChats.length === 0) return;

  for (const legacy of legacyChats) {
    const chat = await createChat(legacy.title || "New chat", { setActive: false });
    await patchChat(chat.id, {
      title: legacy.title || "New chat",
      pinned: Boolean(legacy.pinned),
      archived: Boolean(legacy.archived),
      manual_title: Boolean(legacy.manualTitle || legacy.manual_title),
    });
    for (const message of legacy.messages || []) {
      if (!message.role || message.role.includes("pending")) continue;
      await createMessage(chat.id, message.role, message.content);
    }
  }
  localStorage.removeItem(LEGACY_CHAT_STORE_KEY);
}

async function createChat(title = "New chat", options = { setActive: true }) {
  const chat = await apiJson("/api/chats", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
  chats.unshift(chat);
  if (options.setActive) {
    activeChatId = chat.id;
    persistActiveChat();
  }
  return chat;
}

async function patchChat(chatId, patch) {
  const updated = await apiJson(`/api/chats/${chatId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  upsertChat(updated);
  return updated;
}

async function deleteChatById(chatId) {
  await apiJson(`/api/chats/${chatId}`, { method: "DELETE" });
  chats = chats.filter((chat) => chat.id !== chatId);
  if (activeChatId === chatId) {
    activeChatId = visibleChats()[0]?.id || null;
    persistActiveChat();
  }
}

async function createMessage(chatId, role, content, metadata = {}) {
  const message = await apiJson(`/api/chats/${chatId}/messages`, {
    method: "POST",
    body: JSON.stringify({
      role,
      content,
      tools: metadata.tools || [],
      sources: metadata.sources || [],
    }),
  });
  const chat = chats.find((item) => item.id === chatId);
  if (chat) {
    chat.messages.push(message);
    chat.updated_at = message.created_at;
  }
  return message;
}

function upsertChat(updated) {
  const index = chats.findIndex((chat) => chat.id === updated.id);
  if (index === -1) {
    chats.unshift(updated);
  } else {
    chats[index] = updated;
  }
}

function summarizeTitle(message) {
  const normalized = message.toLowerCase().replace(/[^a-z0-9\s-]/g, " ");
  const words = normalized
    .split(/\s+/)
    .filter((word) => word && !TITLE_STOPWORDS.has(word));

  const hasFocusIntent = /\b(focus|study|prepare|review|learn|practice)\b/.test(normalized);
  const hasExam = /\b(exam|test|quiz|midterm|final)\b/.test(normalized);
  if (hasExam && hasFocusIntent) {
    const subjectIndex = words.findIndex((word) => ["exam", "test", "quiz", "midterm", "final"].includes(word));
    const subject = subjectIndex > 0 ? words[subjectIndex - 1] : "";
    return titleCase([subject, words[subjectIndex] || "exam", "focus"].filter(Boolean).join(" "));
  }

  const topicWords = (words.length ? words : message.split(/\s+/)).slice(0, 4);
  return titleCase(topicWords.join(" ").trim() || "New chat");
}

function titleCase(value) {
  return value.replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatChatDate(value) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function renderChatGroup(groupChats, label) {
  if (groupChats.length === 0) return;
  if (label) {
    const heading = document.createElement("div");
    heading.className = "chat-group-label";
    heading.textContent = label;
    chatList.appendChild(heading);
  }

  for (const chat of groupChats) {
    const item = document.createElement("div");
    item.className = `chat-item${chat.id === activeChatId ? " is-active" : ""}`;
    item.dataset.chatId = chat.id;
    item.setAttribute("aria-current", chat.id === activeChatId ? "true" : "false");

    const main = document.createElement("button");
    main.className = "chat-item-main";
    main.type = "button";
    main.dataset.chatOpen = chat.id;

    const title = document.createElement("span");
    title.className = "chat-title";
    title.textContent = `${chat.pinned ? "Pinned: " : ""}${chat.title}`;

    const meta = document.createElement("span");
    meta.className = "chat-meta";
    const userCount = chat.messages.filter((message) => message.role === "user").length;
    meta.textContent = `${userCount} prompt${userCount === 1 ? "" : "s"} | ${formatChatDate(chat.updated_at)}`;

    const menu = document.createElement("button");
    menu.className = "chat-menu-button";
    menu.type = "button";
    menu.dataset.chatMenu = chat.id;
    menu.setAttribute("aria-label", `Open menu for ${chat.title}`);
    menu.textContent = "...";

    main.append(title, meta);
    item.append(main, menu);
    chatList.appendChild(item);
  }
}

function renderChatList() {
  chatList.innerHTML = "";
  const active = visibleChats();
  const archived = archivedChats();
  if (active.length === 0 && archived.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-chat-list";
    empty.textContent = "No chats yet";
    chatList.appendChild(empty);
    return;
  }

  renderChatGroup(active, "");
  renderChatGroup(archived, "Archived");
}

function renderMessages() {
  const activeChat = getActiveChat();
  messagesEl.innerHTML = "";
  if (!activeChat || activeChat.messages.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `
      <h2>Start with a question.</h2>
      <p>Choose a model, write a prompt, and StillGaze will create a new chat from your first message.</p>
    `;
    messagesEl.appendChild(empty);
    return;
  }

  for (const message of activeChat.messages) {
    const bubble = document.createElement("article");
    bubble.className = `message ${message.role}`;
    const content = document.createElement("div");
    content.className = "message-content";
    if (message.role === "assistant" || message.role.includes("pending")) {
      content.innerHTML = renderMarkdown(message.content);
    } else {
      content.textContent = message.content;
    }
    bubble.appendChild(content);
    renderMessageTools(bubble, message.tools || []);
    renderMessageSources(bubble, message.sources || []);
    messagesEl.appendChild(bubble);
  }
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function render() {
  renderChatList();
  renderMessages();
  renderToolApproval();
}

function renderMemories() {
  memoryList.innerHTML = "";
  if (memories.length === 0) {
    const empty = document.createElement("div");
    empty.className = "memory-empty";
    empty.textContent = "No saved memories";
    memoryList.appendChild(empty);
    return;
  }

  for (const memory of memories) {
    const item = document.createElement("div");
    item.className = "memory-item";

    const text = document.createElement("span");
    text.textContent = memory.content;

    const remove = document.createElement("button");
    remove.type = "button";
    remove.dataset.memoryDelete = memory.id;
    remove.setAttribute("aria-label", "Delete memory");
    remove.textContent = "Remove";

    item.append(text, remove);
    memoryList.appendChild(item);
  }
}

function setStatus(kind, text) {
  statusPanel.classList.toggle("is-ready", kind === "ready");
  statusPanel.classList.toggle("is-error", kind === "error");
  statusLabel.textContent = text;
}

function syncTextareaHeight() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
}

async function loadModelStatus() {
  try {
    const response = await fetch("/api/chat/models");
    if (!response.ok) throw new Error("Model status failed");
    const data = await response.json();
    const savedModel = localStorage.getItem(SELECTED_MODEL_KEY);
    const selectedModel = data.models.includes(savedModel) ? savedModel : data.default_model;
    modelSelect.innerHTML = "";
    for (const model of data.models) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      option.selected = model === selectedModel;
      modelSelect.appendChild(option);
    }
    if (selectedModel) {
      localStorage.setItem(SELECTED_MODEL_KEY, selectedModel);
    }
    setStatus(data.available ? "ready" : "error", data.available ? "Local runtime connected" : "Local runtime offline");
  } catch {
    const savedModel = localStorage.getItem(SELECTED_MODEL_KEY);
    modelSelect.innerHTML = savedModel
      ? `<option value="${escapeHtml(savedModel)}">${escapeHtml(savedModel)}</option>`
      : '<option value="">No models loaded</option>';
    setStatus("error", "Local runtime unavailable");
  }
}

function closeChatMenu() {
  chatMenu.hidden = true;
  menuChatId = null;
}

function openChatMenu(chatId, anchor) {
  const chat = chats.find((item) => item.id === chatId);
  if (!chat) return;
  menuChatId = chatId;
  chatMenu.querySelector('[data-action="pin"]').textContent = chat.pinned ? "Unpin" : "Pin";
  chatMenu.querySelector('[data-action="archive"]').textContent = chat.archived ? "Unarchive" : "Archive";
  const rect = anchor.getBoundingClientRect();
  chatMenu.hidden = false;
  chatMenu.style.left = `${Math.min(rect.right + 6, window.innerWidth - 170)}px`;
  chatMenu.style.top = `${Math.min(rect.top, window.innerHeight - 250)}px`;
}

function selectChat(chatId) {
  activeChatId = chatId;
  pendingToolCall = null;
  sendButton.disabled = false;
  input.disabled = false;
  persistActiveChat();
  render();
  input.focus();
}

async function renameChat(chat) {
  const nextTitle = window.prompt("Rename chat", chat.title);
  if (!nextTitle?.trim()) return;
  await patchChat(chat.id, {
    title: nextTitle.trim().slice(0, 80),
    manual_title: true,
  });
  render();
}

async function deleteChat(chat) {
  if (!window.confirm(`Delete "${chat.title}"?`)) return;
  await deleteChatById(chat.id);
  render();
}

function exportChat(chat, format) {
  const date = new Date().toISOString();
  const transcript = chat.messages
    .map((message) => `${message.role.toUpperCase()}\n${message.content}`)
    .join("\n\n");
  const safeTitle = chat.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || "stillgaze-chat";
  const isHtml = format === "html";
  const content = isHtml
    ? `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(chat.title)}</title></head><body><h1>${escapeHtml(chat.title)}</h1><p>Exported ${date}</p><pre>${escapeHtml(transcript)}</pre></body></html>`
    : `${chat.title}\nExported ${date}\n\n${transcript}`;
  const blob = new Blob([content], { type: isHtml ? "text/html" : "text/plain" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${safeTitle}.${isHtml ? "html" : "txt"}`;
  link.click();
  URL.revokeObjectURL(link.href);
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function renderMarkdown(value = "") {
  const lines = escapeHtml(value).split("\n");
  const output = [];
  let codeLines = [];
  let inCode = false;
  let listItems = [];

  const flushList = () => {
    if (!listItems.length) return;
    output.push(`<ul>${listItems.map((item) => `<li>${formatInlineMarkdown(item)}</li>`).join("")}</ul>`);
    listItems = [];
  };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      flushList();
      if (inCode) {
        output.push(`<pre><code>${codeLines.join("\n")}</code></pre>`);
        codeLines = [];
      }
      inCode = !inCode;
      continue;
    }
    if (inCode) {
      codeLines.push(line);
      continue;
    }
    const listMatch = line.match(/^\s*[-*]\s+(.+)/);
    if (listMatch) {
      listItems.push(listMatch[1]);
      continue;
    }
    flushList();
    if (!line.trim()) {
      output.push("<br>");
    } else if (line.startsWith("### ")) {
      output.push(`<h4>${formatInlineMarkdown(line.slice(4))}</h4>`);
    } else if (line.startsWith("## ")) {
      output.push(`<h3>${formatInlineMarkdown(line.slice(3))}</h3>`);
    } else if (line.startsWith("# ")) {
      output.push(`<h2>${formatInlineMarkdown(line.slice(2))}</h2>`);
    } else {
      output.push(`<p>${formatInlineMarkdown(line)}</p>`);
    }
  }
  flushList();
  if (codeLines.length) {
    output.push(`<pre><code>${codeLines.join("\n")}</code></pre>`);
  }
  return output.join("");
}

function formatInlineMarkdown(value) {
  return value
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

function renderMessageTools(container, tools) {
  if (!Array.isArray(tools) || !tools.length) return;
  const section = document.createElement("div");
  section.className = "message-tools";
  for (const tool of tools) {
    const row = document.createElement("div");
    row.className = `tool-row is-${tool.status || "completed"}`;
    const name = document.createElement("span");
    name.className = "tool-name";
    name.textContent = tool.name || "tool";
    const summary = document.createElement("span");
    summary.textContent = tool.summary || tool.status || "completed";
    row.append(name, summary);
    section.appendChild(row);
  }
  container.appendChild(section);
}

function renderMessageSources(container, sources) {
  if (!Array.isArray(sources) || !sources.length) return;
  const section = document.createElement("div");
  section.className = "message-sources";
  const label = document.createElement("span");
  label.textContent = "Sources";
  section.appendChild(label);
  for (const source of sources) {
    if (!/^https?:\/\//i.test(source.url || "")) continue;
    const link = document.createElement("a");
    link.href = source.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = source.title || source.url;
    section.appendChild(link);
  }
  container.appendChild(section);
}

function renderToolApproval() {
  if (!pendingToolCall) {
    toolApproval.hidden = true;
    toolApproval.innerHTML = "";
    return;
  }

  const isFileWrite = pendingToolCall.name === "file.write";
  const arguments_ = pendingToolCall.arguments || {};
  const title = isFileWrite ? "Confirm local file edit" : "Confirm local command";
  const primary = isFileWrite ? arguments_.path || "" : arguments_.command || "";
  const detail = isFileWrite
    ? `${arguments_.mode || "replace"}\n${(arguments_.content || "").slice(0, 500)}`
    : "Runs on this machine from the StillGaze project folder.";
  const actionLabel = isFileWrite ? "Apply change" : "Run command";
  toolApproval.hidden = false;
  toolApproval.innerHTML = `
    <div>
      <strong>${title}</strong>
      <code>${escapeHtml(primary)}</code>
      <span>${escapeHtml(detail)}</span>
    </div>
    <div class="tool-actions">
      <button type="button" data-tool-action="run">${actionLabel}</button>
      <button type="button" data-tool-action="cancel">Cancel</button>
    </div>
  `;
}

async function handleMenuAction(action) {
  const chat = chats.find((item) => item.id === menuChatId);
  if (!chat) return;

  if (action === "rename") await renameChat(chat);
  if (action === "pin") await patchChat(chat.id, { pinned: !chat.pinned });
  if (action === "archive") {
    await patchChat(chat.id, { archived: !chat.archived });
    if (activeChatId === chat.id && !chat.archived) {
      activeChatId = visibleChats().find((item) => item.id !== chat.id)?.id || null;
      persistActiveChat();
    }
  }
  if (action === "export-txt") exportChat(chat, "txt");
  if (action === "export-html") exportChat(chat, "html");
  if (action === "delete") await deleteChat(chat);

  render();
}

async function sendMessage(content) {
  let activeChat = getActiveChat();
  if (!activeChat) {
    activeChat = await createChat("New chat");
  }

  const isFirstUserMessage = !activeChat.messages.some((message) => message.role === "user");
  if (isFirstUserMessage && !activeChat.manual_title) {
    activeChat = await patchChat(activeChat.id, { title: summarizeTitle(content) });
  }

  const userMessage = await createMessage(activeChat.id, "user", content);
  activeChat = getActiveChat();
  const pending = {
    id: "pending",
    role: "assistant pending",
    content: "Thinking...",
    tools: [],
    sources: [],
    created_at: userMessage.created_at,
  };
  activeChat.messages.push(pending);
  render();
  await streamAssistantResponse(activeChat);
}

async function runApprovedTool() {
  const activeChat = getActiveChat();
  if (!activeChat || !pendingToolCall) return;

  const approvedToolCall = pendingToolCall;
  pendingToolCall = null;
  const pending = {
    id: "pending",
    role: "assistant pending",
    content: "Running local tool...",
    tools: [],
    sources: [],
    created_at: new Date().toISOString(),
  };
  activeChat.messages.push(pending);
  render();

  await streamAssistantResponse(activeChat, approvedToolCall);
}

async function streamAssistantResponse(activeChat, approvedToolCall = null) {
  sendButton.disabled = true;
  input.disabled = true;
  setStatus("ready", "Generating response");

  try {
    const outboundMessages = activeChat.messages
      .filter((message) => !message.role.includes("pending"))
      .map((message) => ({
        role: message.role,
        content: message.content,
      }));

    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: outboundMessages,
        temperature: Number(temperature.value),
        max_tokens: Number(responseLimit.value),
        model: modelSelect.value || undefined,
        approved_tool_call: approvedToolCall || undefined,
      }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || "Chat request failed");
    }

    const pending = activeChat.messages.find((message) => message.id === "pending");
    const streamState = { content: "", tools: [], sources: [] };
    await consumeChatStream(response, streamState, pending);

    activeChat.messages = activeChat.messages.filter((message) => message.id !== "pending");
    const finalContent = streamState.content.trim() || "The local agent completed without a text response.";
    await createMessage(activeChat.id, "assistant", finalContent, {
      tools: streamState.tools,
      sources: streamState.sources,
    });
    if (streamState.tools.some((tool) => tool.name === "memory.write" && tool.status === "completed")) {
      await loadMemories();
    }
    setStatus("ready", "Local runtime connected");
  } catch (error) {
    activeChat.messages = activeChat.messages.filter((message) => message.id !== "pending");
    activeChat.messages.push({
      id: "error",
      chat_id: activeChat.id,
      role: "assistant",
      content: error.message,
      created_at: new Date().toISOString(),
    });
    setStatus("error", "Request failed");
  } finally {
    const awaitingApproval = Boolean(pendingToolCall);
    sendButton.disabled = awaitingApproval;
    input.disabled = awaitingApproval;
    if (!awaitingApproval) input.focus();
    render();
  }
}

async function consumeChatStream(response, state, pending) {
  if (!response.body) throw new Error("Streaming response was unavailable.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.trim()) continue;
      handleChatEvent(JSON.parse(line), state, pending);
    }
    if (done) break;
  }
  if (buffer.trim()) {
    handleChatEvent(JSON.parse(buffer), state, pending);
  }
}

function handleChatEvent(event, state, pending) {
  if (event.type === "token") {
    state.content += event.content || "";
  } else if (event.type === "tool" && event.tool) {
    state.tools.push(event.tool);
  } else if (event.type === "sources" && Array.isArray(event.sources)) {
    state.sources = event.sources;
  } else if (event.type === "approval" && event.tool) {
    pendingToolCall = event.tool;
  } else if (event.type === "continuation") {
    setStatus("ready", "Continuing response");
  } else if (event.type === "truncated") {
    state.content += "\n\nResponse limit reached. Choose Long or ask me to continue.";
  } else if (event.type === "error") {
    throw new Error(event.message || "Streaming request failed");
  }

  if (pending) {
    pending.content = state.content || "Thinking...";
    pending.tools = state.tools;
    pending.sources = state.sources;
    renderMessages();
    renderToolApproval();
  }
}

async function createMemory(content) {
  const memory = await apiJson("/api/memory", {
    method: "POST",
    body: JSON.stringify({ content, source: "user" }),
  });
  memories.unshift(memory);
  renderMemories();
}

async function deleteMemory(memoryId) {
  await apiJson(`/api/memory/${memoryId}`, { method: "DELETE" });
  memories = memories.filter((memory) => memory.id !== memoryId);
  renderMemories();
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const content = input.value.trim();
  if (!content) return;
  input.value = "";
  syncTextareaHeight();
  sendMessage(content);
});

input.addEventListener("input", syncTextareaHeight);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

newChatButton.addEventListener("click", async () => {
  await createChat();
  pendingToolCall = null;
  sendButton.disabled = false;
  input.disabled = false;
  closeChatMenu();
  render();
  input.focus();
});

chatList.addEventListener("click", (event) => {
  const menuButton = event.target.closest("[data-chat-menu]");
  if (menuButton) {
    event.stopPropagation();
    openChatMenu(menuButton.dataset.chatMenu, menuButton);
    return;
  }

  const openButton = event.target.closest("[data-chat-open]");
  if (!openButton) return;
  closeChatMenu();
  selectChat(openButton.dataset.chatOpen);
});

chatMenu.addEventListener("click", async (event) => {
  const actionButton = event.target.closest("[data-action]");
  if (!actionButton) return;
  await handleMenuAction(actionButton.dataset.action);
  closeChatMenu();
});

toolApproval.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-tool-action]");
  if (!button) return;
  if (button.dataset.toolAction === "cancel") {
    pendingToolCall = null;
    sendButton.disabled = false;
    input.disabled = false;
    renderToolApproval();
    input.focus();
    return;
  }
  await runApprovedTool();
});

memoryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = memoryInput.value.trim();
  if (!content) return;
  memoryInput.value = "";
  await createMemory(content);
});

memoryList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-memory-delete]");
  if (!button) return;
  await deleteMemory(button.dataset.memoryDelete);
});

document.addEventListener("click", (event) => {
  if (!chatMenu.hidden && !chatMenu.contains(event.target) && !event.target.closest("[data-chat-menu]")) {
    closeChatMenu();
  }
});

temperature.addEventListener("input", () => {
  temperatureValue.textContent = Number(temperature.value).toFixed(1);
});

modelSelect.addEventListener("change", () => {
  if (modelSelect.value) {
    localStorage.setItem(SELECTED_MODEL_KEY, modelSelect.value);
  }
});

themeToggle.addEventListener("change", () => {
  const nextTheme = themeToggle.checked ? "dark" : "light";
  document.body.classList.toggle("dark", themeToggle.checked);
  localStorage.setItem("stillgaze-theme", nextTheme);
});

render();
syncTextareaHeight();
loadModelStatus();
loadMemories().catch((error) => {
  memories = [];
  renderMemories();
  setStatus("error", error.message);
});
loadChats().catch((error) => {
  setStatus("error", error.message);
});
