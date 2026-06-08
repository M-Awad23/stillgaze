const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const messagesEl = document.querySelector("#messages");
const sendButton = document.querySelector("#sendButton");
const statusPanel = document.querySelector(".status-panel");
const statusLabel = document.querySelector("#statusLabel");
const modelLabel = document.querySelector("#modelLabel");
const temperature = document.querySelector("#temperature");
const temperatureValue = document.querySelector("#temperatureValue");
const responseLimit = document.querySelector("#responseLimit");
const themeToggle = document.querySelector("#themeToggle");
const newChatButton = document.querySelector("#newChatButton");
const chatList = document.querySelector("#chatList");
const chatMenu = document.querySelector("#chatMenu");

const CHAT_STORE_KEY = "stillgaze-chats";
const ACTIVE_CHAT_KEY = "stillgaze-active-chat";
const WELCOME_MESSAGE = {
  role: "assistant",
  content: "StillGaze is awake. What should we look at first?",
};
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

let chats = loadChats();
let activeChatId = localStorage.getItem(ACTIVE_CHAT_KEY);
let menuChatId = null;
if (!chats.some((chat) => chat.id === activeChatId && !chat.archived)) {
  activeChatId = visibleChats()[0]?.id || createChat().id;
}

function createId() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function createChat() {
  const now = new Date().toISOString();
  const chat = {
    id: createId(),
    title: "New chat",
    createdAt: now,
    updatedAt: now,
    pinned: false,
    archived: false,
    messages: [{ ...WELCOME_MESSAGE }],
  };
  chats.unshift(chat);
  activeChatId = chat.id;
  persistChats();
  return chat;
}

function loadChats() {
  try {
    const stored = JSON.parse(localStorage.getItem(CHAT_STORE_KEY) || "[]");
    if (Array.isArray(stored) && stored.length > 0) {
      return stored.map((chat) => ({
        ...chat,
        pinned: Boolean(chat.pinned),
        archived: Boolean(chat.archived),
        messages: chat.messages.filter((message) => !message.role.includes("pending")),
      }));
    }
  } catch {
    localStorage.removeItem(CHAT_STORE_KEY);
  }
  return [];
}

function persistChats() {
  localStorage.setItem(CHAT_STORE_KEY, JSON.stringify(chats));
  localStorage.setItem(ACTIVE_CHAT_KEY, activeChatId);
}

function visibleChats() {
  return chats
    .filter((chat) => !chat.archived)
    .sort((a, b) => Number(b.pinned) - Number(a.pinned) || new Date(b.updatedAt) - new Date(a.updatedAt));
}

function archivedChats() {
  return chats
    .filter((chat) => chat.archived)
    .sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt));
}

function getActiveChat() {
  return chats.find((chat) => chat.id === activeChatId) || visibleChats()[0] || chats[0];
}

function summarizeTitle(message) {
  const normalized = message
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, " ");
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
  const title = topicWords
    .join(" ")
    .trim();
  return titleCase(title || "New chat");
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
    meta.textContent = `${userCount} prompt${userCount === 1 ? "" : "s"} | ${formatChatDate(chat.updatedAt)}`;

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
  migrateGeneratedTitles();
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

function migrateGeneratedTitles() {
  let changed = false;
  for (const chat of chats) {
    if (chat.manualTitle) continue;
    const firstUserMessage = chat.messages.find((message) => message.role === "user");
    if (!firstUserMessage) continue;
    const nextTitle = summarizeTitle(firstUserMessage.content);
    if (chat.title !== nextTitle) {
      chat.title = nextTitle;
      changed = true;
    }
  }
  if (changed) persistChats();
}

function renderMessages() {
  const activeChat = getActiveChat();
  messagesEl.innerHTML = "";
  if (!activeChat) return;
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
    const response = await fetch("/api/chat/model");
    if (!response.ok) throw new Error("Model status failed");
    const data = await response.json();
    modelLabel.textContent = `Model: ${data.model}`;
    setStatus("ready", "Local model ready");
  } catch {
    modelLabel.textContent = "Model: offline";
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
  persistChats();
  render();
  input.focus();
}

function ensureActiveVisibleChat() {
  const active = chats.find((chat) => chat.id === activeChatId);
  if (!active || active.archived) {
    activeChatId = visibleChats()[0]?.id || createChat().id;
  }
}

function renameChat(chat) {
  const nextTitle = window.prompt("Rename chat", chat.title);
  if (!nextTitle?.trim()) return;
  chat.title = nextTitle.trim().slice(0, 80);
  chat.manualTitle = true;
  chat.updatedAt = new Date().toISOString();
  persistChats();
  render();
}

function deleteChat(chat) {
  if (!window.confirm(`Delete "${chat.title}"?`)) return;
  chats = chats.filter((item) => item.id !== chat.id);
  ensureActiveVisibleChat();
  persistChats();
  render();
}

function exportChat(chat, format) {
  const date = new Date().toISOString();
  const transcript = chat.messages
    .map((message) => `${message.role.replace("assistant pending", "assistant").toUpperCase()}\n${message.content}`)
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

function handleMenuAction(action) {
  const chat = chats.find((item) => item.id === menuChatId);
  if (!chat) return;

  if (action === "rename") renameChat(chat);
  if (action === "pin") chat.pinned = !chat.pinned;
  if (action === "archive") {
    chat.archived = !chat.archived;
    if (chat.archived) chat.pinned = false;
    ensureActiveVisibleChat();
  }
  if (action === "export-txt") exportChat(chat, "txt");
  if (action === "export-html") exportChat(chat, "html");
  if (action === "delete") deleteChat(chat);

  chat.updatedAt = new Date().toISOString();
  persistChats();
  render();
}

async function sendMessage(content) {
  const activeChat = getActiveChat();
  const isFirstUserMessage = !activeChat.messages.some((message) => message.role === "user");
  activeChat.messages.push({ role: "user", content });
  activeChat.updatedAt = new Date().toISOString();
  if (isFirstUserMessage) activeChat.title = summarizeTitle(content);

  const pending = { role: "assistant pending", content: "Thinking..." };
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
      }),
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "Chat request failed");
    }

    pending.role = "assistant";
    pending.content = data.message.content;
    activeChat.updatedAt = new Date().toISOString();
    setStatus("ready", "Local model ready");
  } catch (error) {
    pending.role = "assistant";
    pending.content = error.message;
    activeChat.updatedAt = new Date().toISOString();
    setStatus("error", "Request failed");
  } finally {
    persistChats();
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

newChatButton.addEventListener("click", () => {
  createChat();
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

chatMenu.addEventListener("click", (event) => {
  const actionButton = event.target.closest("[data-action]");
  if (!actionButton) return;
  handleMenuAction(actionButton.dataset.action);
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
