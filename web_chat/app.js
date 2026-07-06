const STORAGE_KEY = "breakout.webchat.v1";

const state = loadState();
const els = {
  messages: document.getElementById("messages"),
  form: document.getElementById("chatForm"),
  input: document.getElementById("messageInput"),
  send: document.getElementById("sendButton"),
  reset: document.getElementById("resetButton"),
  typing: document.getElementById("typingIndicator"),
  status: document.getElementById("connectionStatus"),
  summary: document.getElementById("summaryStrip"),
};

function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    return {
      sessionId: saved.sessionId || crypto.randomUUID(),
      messages: Array.isArray(saved.messages) ? saved.messages : [],
      lastPayload: saved.lastPayload || null,
    };
  } catch {
    return { sessionId: crypto.randomUUID(), messages: [], lastPayload: null };
  }
}

let sse = null;
let activeStreamBubble = null;

function connectRealtime() {
  if (sse) sse.close();
  sse = new EventSource(`/stream/${state.sessionId}`);
  
  sse.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.event === "transcript.chunk") {
        handleTranscriptChunk(data.payload);
      }
    } catch (err) {
      console.error("SSE parse error", err);
    }
  };
  sse.onerror = () => {
    console.warn("SSE connection error, reconnecting...");
  };
}

function handleTranscriptChunk(chunk) {
  if (chunk.speaker !== "assistant") return;
  
  if (!activeStreamBubble) {
    const row = document.createElement("article");
    row.className = `message-row agent streaming`;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    row.appendChild(bubble);
    els.messages.appendChild(row);
    activeStreamBubble = bubble;
    els.typing.hidden = true;
  }
  
  activeStreamBubble.innerHTML = linkify(chunk.text);
  scrollToBottom();
}

function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function nowLabel() {
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date());
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function linkify(text) {
  const escaped = escapeHtml(text);
  return escaped.replace(/https?:\/\/[^\s<]+/g, (url) => {
    return `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`;
  });
}

function setBusy(isBusy) {
  els.send.disabled = isBusy;
  els.input.disabled = isBusy;
  els.typing.hidden = !isBusy;
}

function setStatus(label, isError = false) {
  els.status.classList.toggle("is-error", isError);
  els.status.querySelector("span:last-child").textContent = label;
}

function appendMessage(message) {
  state.messages.push(message);
  saveState();
  render();
}

function render() {
  els.messages.innerHTML = "";
  for (const message of state.messages) {
    els.messages.appendChild(renderMessage(message));
  }
  renderSummary(state.lastPayload);
  scrollToBottom();
}

function renderMessage(message) {
  const row = document.createElement("article");
  row.className = `message-row ${message.role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = linkify(message.text);

  const cards = document.createElement("div");
  cards.className = "cards";
  if (message.payload) {
    const bookingCard = renderBookingCard(message.payload.booking);
    const paymentCard = renderPaymentCard(message.payload.payment);
    if (bookingCard) cards.appendChild(bookingCard);
    if (paymentCard) cards.appendChild(paymentCard);
    for (const item of message.payload.media || []) {
      const card = renderMediaCard(item);
      if (card) cards.appendChild(card);
    }
  }
  if (cards.children.length) bubble.appendChild(cards);

  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = message.time || nowLabel();
  bubble.appendChild(meta);
  row.appendChild(bubble);
  return row;
}

function renderBookingCard(booking = {}) {
  if (!booking.booking_id) return null;
  const card = document.createElement("section");
  card.className = "booking-card";
  card.innerHTML = `
    <p class="card-title">Booking</p>
    <div class="card-grid">
      <div><span>ID</span><br>${escapeHtml(booking.booking_id)}</div>
      <div><span>Status</span><br>${escapeHtml(booking.status || "Reserved")}</div>
      <div><span>Room</span><br>${escapeHtml(booking.room || "")}</div>
      <div><span>Location</span><br>${escapeHtml(booking.location || "")}</div>
      <div><span>Date</span><br>${escapeHtml(booking.date || "")}</div>
      <div><span>Time</span><br>${escapeHtml(booking.time || "")}</div>
    </div>
  `;
  return card;
}

function renderPaymentCard(payment = {}) {
  if (!payment.payment_url && !payment.status && !payment.payment_deadline) return null;
  const card = document.createElement("section");
  card.className = "payment-card";
  const link = payment.payment_url
    ? `<a class="action-link" href="${escapeHtml(payment.payment_url)}" target="_blank" rel="noopener noreferrer">Open payment link</a>`
    : "";
  card.innerHTML = `
    <p class="card-title">Payment</p>
    <div class="card-grid">
      <div><span>Status</span><br>${escapeHtml(payment.status || "Pending")}</div>
      <div><span>Booking</span><br>${escapeHtml(payment.booking_status || "")}</div>
      <div><span>Deadline</span><br>${escapeHtml(formatDeadline(payment.payment_deadline))}</div>
    </div>
    ${link}
  `;
  return card;
}

function renderMediaCard(item = {}) {
  if (!item.url) return null;
  const card = document.createElement("section");
  card.className = "media-card";
  const title = item.title ? `<p class="card-title">${escapeHtml(item.title)}</p>` : "";
  if (item.type === "image") {
    card.innerHTML = `${title}<img src="${escapeHtml(item.url)}" alt="${escapeHtml(item.title || "Room media")}" loading="lazy">`;
  } else if (item.type === "video") {
    card.innerHTML = `${title}<video src="${escapeHtml(item.url)}" controls preload="metadata"></video>`;
  } else {
    const label = item.type === "document" ? "Open document" : "Open link";
    card.innerHTML = `${title}<a class="action-link" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  }
  return card;
}

function renderSummary(payload) {
  const booking = payload?.booking || {};
  const payment = payload?.payment || {};
  const chips = [];
  if (booking.booking_id) chips.push(`Booking ${escapeHtml(booking.booking_id)}`);
  if (booking.status) chips.push(`Status ${escapeHtml(booking.status)}`);
  if (payment.status) chips.push(`Payment ${escapeHtml(payment.status)}`);
  if (payment.payment_url) {
    chips.push(`<a href="${escapeHtml(payment.payment_url)}" target="_blank" rel="noopener noreferrer">Payment link</a>`);
  }
  els.summary.hidden = chips.length === 0;
  els.summary.innerHTML = chips.map((chip) => `<span class="summary-chip">${chip}</span>`).join("");
}

function formatDeadline(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    els.messages.scrollTop = els.messages.scrollHeight;
  });
}

async function sendMessage(text, retryOf = null) {
  const userMessage = retryOf || { role: "user", text, time: nowLabel() };
  if (!retryOf) appendMessage(userMessage);
  setBusy(true);
  setStatus("Thinking");
  try {
    const payload = await postChat(text);
    state.lastPayload = payload;
    
    if (activeStreamBubble) {
      activeStreamBubble.closest('.message-row').remove();
      activeStreamBubble = null;
    }

    appendMessage({
      role: "agent",
      text: payload.response || "",
      time: nowLabel(),
      payload,
    });
    setStatus("Connected");
  } catch (error) {
    setStatus("Retry available", true);
    appendMessage({
      role: "agent",
      text: `I couldn't reach the backend. ${error.message || "Please try again."}`,
      time: nowLabel(),
      payload: {
        media: [],
        booking: {},
        payment: {},
      },
      retryText: text,
    });
    addRetryButton(text);
  } finally {
    setBusy(false);
    els.input.focus();
  }
}

async function postChat(message) {
  const response = await fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: state.sessionId, message }),
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `HTTP ${response.status}`);
  }
  return response.json();
}

function addRetryButton(text) {
  const lastBubble = els.messages.querySelector(".message-row:last-child .bubble");
  if (!lastBubble) return;
  const button = document.createElement("button");
  button.className = "action-link";
  button.type = "button";
  button.textContent = "Retry";
  button.addEventListener("click", () => sendMessage(text));
  lastBubble.appendChild(button);
}

els.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = els.input.value.trim();
  if (!text) return;
  els.input.value = "";
  autoSize();
  sendMessage(text);
});

els.input.addEventListener("input", autoSize);
els.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    els.form.requestSubmit();
  }
});

els.reset.addEventListener("click", async () => {
  setBusy(true);
  try {
    await fetch("/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId }),
    });
  } finally {
    state.sessionId = crypto.randomUUID();
    state.messages = [];
    state.lastPayload = null;
    if (activeStreamBubble) {
      activeStreamBubble = null;
    }
    saveState();
    connectRealtime();
    setBusy(false);
    setStatus("Connected");
    render();
    welcome();
  }
});

function autoSize() {
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 140)}px`;
}

function welcome() {
  if (state.messages.length) return;
  appendMessage({
    role: "agent",
    text: "Hi, this is Breakout. Tell me what you are planning, and I’ll help from there.",
    time: nowLabel(),
    payload: { media: [], booking: {}, payment: {} },
  });
}

render();
connectRealtime();
welcome();
