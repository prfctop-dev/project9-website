// chat.js - the chat box: sends messages to the Python backend and shows the replies
"use strict";

// Where the backend lives. Empty means "the same address as this page".
// (If the website is hosted somewhere else later, put the backend's full address here.)
const API_BASE = "";
const MAX_LENGTH = 1000;            // same limit as the backend
const REQUEST_TIMEOUT_MS = 90000;   // the agent may need several AI calls, so be patient
const WELCOME =
  "Hi! I can help with order status and refund questions. " +
  "I will need your order number and the email used for the order. " +
  "(Demo tip: try ORD-1001 with ali@example.com)";

// The parts of the page we control
const launcher = document.getElementById("chat-launcher");
const panel = document.getElementById("chat-panel");
const closeButton = document.getElementById("chat-close");
const newChatButton = document.getElementById("chat-new");
const messagesBox = document.getElementById("chat-messages");
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const sendButton = document.getElementById("chat-send");

// Each browser tab gets its own random chat id. The backend uses it to remember the chat.
let sessionId = makeSessionId();
let isSending = false;

function makeSessionId() {
  if (window.crypto && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return "s-" + Date.now() + "-" + Math.random().toString(36).slice(2, 10);
}

// Add one message to the window.
// textContent (NOT innerHTML) means any HTML in a message is shown as plain text, never run.
function addMessage(text, kind) {
  const div = document.createElement("div");
  div.className = "message " + kind;
  div.textContent = text;
  messagesBox.appendChild(div);
  messagesBox.scrollTop = messagesBox.scrollHeight;
  return div;
}

// The animated "typing..." dots shown while we wait for the AI
function showTyping() {
  const div = document.createElement("div");
  div.className = "message bot typing";
  div.setAttribute("aria-label", "Assistant is typing");
  for (let i = 0; i < 3; i++) {
    div.appendChild(document.createElement("span"));
  }
  messagesBox.appendChild(div);
  messagesBox.scrollTop = messagesBox.scrollHeight;
  return div;
}

// Lock or unlock the text box while we wait (stops double-sending)
function setFormEnabled(enabled) {
  input.disabled = !enabled;
  sendButton.disabled = !enabled;
}

// Open and close the chat window
function openChat() {
  panel.hidden = false;
  launcher.setAttribute("aria-expanded", "true");
  if (messagesBox.children.length === 0) {
    addMessage(WELCOME, "bot");
  }
  input.focus();
}

function closeChat() {
  panel.hidden = true;
  launcher.setAttribute("aria-expanded", "false");
  launcher.focus();
}

// Safely read JSON from a response (returns null if it is not JSON)
async function readJson(response) {
  try {
    return await response.json();
  } catch (error) {
    return null;
  }
}

// Turn a server error into a friendly sentence
function errorText(status, data) {
  if (status === 429) {
    return data && typeof data.detail === "string"
      ? data.detail
      : "Too many messages. Please wait a minute.";
  }
  if (status === 422) {
    return "Please check your message and try again.";
  }
  return "Something went wrong. Please try again in a moment.";
}

// Send one message to the backend and show the answer
async function sendMessage(text) {
  isSending = true;
  setFormEnabled(false);
  addMessage(text, "user");
  const typing = showTyping();

  // Give up after a while instead of waiting forever
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(API_BASE + "/agent-chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: sessionId }),
      signal: controller.signal,
    });
    const data = await readJson(response);
    typing.remove();

    if (!response.ok) {
      addMessage(errorText(response.status, data), "error");
      return;
    }

    addMessage((data && data.reply) || "Sorry, I got an empty answer.", "bot");
    if (data && data.handed_off) {
      addMessage("Our team has been notified and will follow up.", "note");
    }
  } catch (error) {
    typing.remove();
    if (error.name === "AbortError") {
      addMessage("This is taking too long. Please try again.", "error");
    } else {
      addMessage("Could not reach the server. Check your connection and try again.", "error");
    }
  } finally {
    clearTimeout(timer);
    isSending = false;
    setFormEnabled(true);
    input.focus();
  }
}

// Start a fresh chat: new id, empty window, and tell the backend to forget the old one
async function newChat() {
  if (isSending) {
    return;
  }
  const oldId = sessionId;
  sessionId = makeSessionId();
  messagesBox.replaceChildren();
  addMessage(WELCOME, "bot");
  try {
    await fetch(API_BASE + "/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: oldId }),
    });
  } catch (error) {
    // Not important: the old chat is simply left behind
  }
}

// When the customer presses Send (or Enter)
form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || isSending) {
    return;
  }
  if (text.length > MAX_LENGTH) {
    addMessage("Please keep your message under " + MAX_LENGTH + " characters.", "error");
    return;
  }
  input.value = "";
  sendMessage(text);
});

// Buttons and keyboard
launcher.addEventListener("click", () => (panel.hidden ? openChat() : closeChat()));
closeButton.addEventListener("click", closeChat);
newChatButton.addEventListener("click", newChat);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !panel.hidden) {
    closeChat();
  }
});