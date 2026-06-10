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

const LEGACY_CHAT_STORE_KEY = "stillgaze-chats";
const ACTIVE_CHAT_KEY = "stillgaze-active-chat";
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
let activeChatId = localStorage.getItem(ACTIVE_CHAT_KEY);
let menuChatId = null;

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

async function createMessage(chatId, role, content) {
  const message = await apiJson(`/api/chats/${chatId}/messages`, {
    method: "POST",
    body: JSON.stringify({ role, content }),
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
    bubble.textContent = message.content;
    messagesEl.appendChild(bubble);
  }
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function render() {
  renderChatList();
  renderMessages();
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
    modelSelect.innerHTML = "";
    for (const model of data.models) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      option.selected = model === data.default_model;
      modelSelect.appendChild(option);
    }
    setStatus(data.available ? "ready" : "error", data.available ? "Ollama connected" : "Ollama offline");
  } catch {
    modelSelect.innerHTML = '<option value="">No models loaded</option>';
    setStatus("error", "Ollama unavailable");
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
  const pending = { id: "pending", role: "assistant pending", content: "Thinking...", created_at: userMessage.created_at };
  activeChat.messages.push(pending);
  render();

  sendButton.disabled = true;
  input.disabled = true;

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: activeChat.messages
          .filter((message) => !message.role.includes("pending"))
          .map((message) => ({
            role: message.role,
            content: message.content,
          })),
        temperature: Number(temperature.value),
        max_tokens: Number(responseLimit.value),
        model: modelSelect.value || undefined,
      }),
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "Chat request failed");
    }

    activeChat.messages = activeChat.messages.filter((message) => message.id !== "pending");
    await createMessage(activeChat.id, "assistant", data.message.content);
    setStatus("ready", "Ollama connected");
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
    sendButton.disabled = false;
    input.disabled = false;
    input.focus();
    render();
  }
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

document.addEventListener("click", (event) => {
  if (!chatMenu.hidden && !chatMenu.contains(event.target) && !event.target.closest("[data-chat-menu]")) {
    closeChatMenu();
  }
});

temperature.addEventListener("input", () => {
  temperatureValue.textContent = Number(temperature.value).toFixed(1);
});

themeToggle.addEventListener("change", () => {
  const nextTheme = themeToggle.checked ? "dark" : "light";
  document.body.classList.toggle("dark", themeToggle.checked);
  localStorage.setItem("stillgaze-theme", nextTheme);
});

render();
syncTextareaHeight();
loadModelStatus();
loadChats().catch((error) => {
  setStatus("error", error.message);
});
