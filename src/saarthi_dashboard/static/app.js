/**
 * app.js — Saarthi Dashboard WebSocket client
 */

"use strict";

// ---------------------------------------------------------------------------
// Config — key is loaded from .env via /config endpoint (never hardcoded)
// ---------------------------------------------------------------------------
let GEMINI_API_KEY = "";

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------
const statusText  = document.getElementById("status-text");
const statusPill  = document.getElementById("status-pill");
const speedText   = document.getElementById("speed-text");
const mapCanvas   = document.getElementById("map-canvas");
const mapOverlay  = document.getElementById("map-overlay-msg");
const stopBtn     = document.getElementById("stop-btn");
const voiceBtn    = document.getElementById("voice-btn");
const voiceHint   = document.getElementById("voice-hint");
const destButtons = document.querySelectorAll("[data-dest]");

const ctx = mapCanvas.getContext("2d");

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let ws        = null;
let lastState = null;
let mapImg    = null;
let lastMapPng = "";

const STATUS_LABELS = {
  ok:       "Ready",
  estop:    "E-Stop",
  cliff:    "Step ahead",
  watchdog: "Watchdog",
  obstacle: "Obstacle",
};

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------
function connectWS() {
  const url = "ws://localhost:8080/ws";
  ws = new WebSocket(url);

  ws.onopen = () => {
    console.log("WebSocket connected");
    setStatus("ok", "Connected");
  };

  ws.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === "state") handleState(msg);
    } catch (e) {
      console.error("WS parse error", e);
    }
  };

  ws.onerror = (e) => console.error("WS error", e);

  ws.onclose = () => {
    console.warn("WS closed — reconnecting in 2s");
    setStatus("estop", "Disconnected");
    setTimeout(connectWS, 2000);
  };
}

function wsSend(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
  } else {
    console.warn("WS not open — dropped", obj);
  }
}

// ---------------------------------------------------------------------------
// State handler
// ---------------------------------------------------------------------------
function handleState(msg) {
  lastState = msg;
  const label = STATUS_LABELS[msg.status] || msg.status;
  setStatus(msg.status, label);
  speedText.textContent = (Math.abs(msg.speed) * 100 | 0) / 100 + " m/s";

  if (msg.map_png && msg.map_png !== lastMapPng) {
    lastMapPng = msg.map_png;
    if (mapOverlay) mapOverlay.style.display = "none";
    const img = new Image();
    img.onload = () => { mapImg = img; drawMap(); };
    img.src = "data:image/png;base64," + msg.map_png;
  } else {
    drawMap();
  }
}

function setStatus(statusKey, label) {
  statusText.textContent = label;
  statusPill.classList.remove("status-ok", "status-error");
  statusPill.classList.add(statusKey === "ok" ? "status-ok" : "status-error");
}

// ---------------------------------------------------------------------------
// Map rendering
// ---------------------------------------------------------------------------
function drawMap() {
  const W = mapCanvas.width;
  const H = mapCanvas.height;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "#1a1f2e";
  ctx.fillRect(0, 0, W, H);

  if (mapImg) {
    ctx.drawImage(mapImg, 0, 0, W, H);
  } else {
    ctx.strokeStyle = "#2a3050";
    ctx.lineWidth = 1;
    for (let x = 0; x < W; x += 20) { ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,H); ctx.stroke(); }
    for (let y = 0; y < H; y += 20) { ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(W,y); ctx.stroke(); }
    ctx.fillStyle = "#555";
    ctx.font = "12px monospace";
    ctx.textAlign = "center";
    ctx.fillText("Waiting for map…", W/2, H/2);
  }

  if (!lastState) return;

  const meta = lastState.map_meta || {};
  const res  = meta.resolution || 0.05;
  const ox   = meta.origin_x   || 0;
  const oy   = meta.origin_y   || 0;
  const mw   = meta.width      || 200;
  const mh   = meta.height     || 200;
  const pose = lastState.pose  || { x:0, y:0, theta:0 };

  const px = ((pose.x - ox) / (mw * res)) * W;
  const py = H - ((pose.y - oy) / (mh * res)) * H;
  const r  = Math.max(6, W / 30);

  const arrowLen = r * 2.5;
  const ax = px + arrowLen * Math.cos(pose.theta);
  const ay = py - arrowLen * Math.sin(pose.theta);

  ctx.strokeStyle = "#00ccff";
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(ax, ay); ctx.stroke();

  const headLen = 8;
  const angle = Math.atan2(py - ay, ax - px);
  ctx.beginPath();
  ctx.moveTo(ax, ay);
  ctx.lineTo(ax - headLen * Math.cos(angle - 0.4), ay + headLen * Math.sin(angle - 0.4));
  ctx.lineTo(ax - headLen * Math.cos(angle + 0.4), ay + headLen * Math.sin(angle + 0.4));
  ctx.closePath();
  ctx.fillStyle = "#00ccff";
  ctx.fill();

  ctx.beginPath();
  ctx.arc(px, py, r, 0, 2 * Math.PI);
  ctx.fillStyle = "#1e90ff";
  ctx.fill();
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = 2;
  ctx.stroke();
}

// ---------------------------------------------------------------------------
// Destination buttons
// ---------------------------------------------------------------------------
destButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const dest = btn.dataset.dest;
    wsSend({ type: "goal", destination: dest });
    const label = btn.querySelector(".dest-label")?.textContent || dest;
    setStatus("ok", "Going to " + dest);
    speak("Okay! Taking you to the " + dest + " now.");
  });
});

// ---------------------------------------------------------------------------
// Stop button
// ---------------------------------------------------------------------------
stopBtn.addEventListener("click", () => {
  wsSend({ type: "stop" });
  setStatus("estop", "Stopped");
  speak("Stopped");
});

// ---------------------------------------------------------------------------
// Voice + Gemini speech-to-speech
// ---------------------------------------------------------------------------

const DEST_LABELS = {
  kitchen: "Kitchen", classroom: "Classroom",
  bedroom: "Bedroom", bathroom:  "Bathroom",
};
const DEST_NAMES = Object.keys(DEST_LABELS);

const GEMINI_SYSTEM = `You are the voice assistant for Saarthi, an autonomous wheelchair for children with disabilities.
Available rooms: kitchen, classroom, bedroom, bathroom.
The child speaks naturally — map their intent to one of these actions:
  NAVIGATE:<room>   — go to that room
  STOP              — halt immediately
  STATUS            — tell them where the wheelchair is heading
  EMERGENCY         — child is in distress, hurt, scared, or needs urgent help
  UNKNOWN           — you couldn't understand

Examples:
  "I'm hungry"            → NAVIGATE:kitchen
  "take me to eat"        → NAVIGATE:kitchen
  "I want to study"       → NAVIGATE:classroom
  "I need to sleep"       → NAVIGATE:bedroom
  "bathroom please"       → NAVIGATE:bathroom
  "stop stop stop"        → STOP
  "wait here"             → STOP
  "where are we going"    → STATUS
  "help me"               → EMERGENCY
  "I fell"                → EMERGENCY
  "it hurts"              → EMERGENCY
  "I'm scared"            → EMERGENCY
  "I need help"           → EMERGENCY
  "call someone"          → EMERGENCY
  "blah blah"             → UNKNOWN

Reply with ONLY the action code — nothing else. No explanation.`;

async function geminiIntent(transcript) {
  if (!GEMINI_API_KEY) return null;
  try {
    const res = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=${GEMINI_API_KEY}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          system_instruction: { parts: [{ text: GEMINI_SYSTEM }] },
          contents: [{ parts: [{ text: transcript }] }],
          generationConfig: { maxOutputTokens: 20, temperature: 0 },
        }),
      }
    );
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    return data.candidates?.[0]?.content?.parts?.[0]?.text?.trim() || null;
  } catch (e) {
    console.warn("Gemini error:", e);
    return null;
  }
}

function keywordFallback(transcript) {
  if (/help|hurt|fell|scared|pain|emergency/i.test(transcript)) return "EMERGENCY";
  if (/stop|wait|halt/i.test(transcript))                        return "STOP";
  if (/where|status|going/i.test(transcript))                    return "STATUS";
  const hit = DEST_NAMES.find(d => transcript.includes(d));
  return hit ? "NAVIGATE:" + hit : "UNKNOWN";
}

function triggerEmergency() {
  wsSend({ type: "stop" });
  setStatus("estop", "HELP NEEDED");
  speak("Don't worry, I am stopping right now. Someone will come to help you very soon. You are safe.");
  showVoiceHint("🆘 Stopping — help is coming");

  // Flash screen red to attract caregiver attention
  let flashes = 0;
  const originalBg = document.body.style.background;
  const flash = setInterval(() => {
    document.body.style.background = flashes % 2 === 0 ? "#7f1d1d" : "#0f172a";
    if (++flashes >= 8) {
      clearInterval(flash);
      document.body.style.background = originalBg;
    }
  }, 300);
}

async function handleTranscript(transcript) {
  console.log("Transcript:", transcript);
  showVoiceHint("Thinking…");

  let action = await geminiIntent(transcript);
  if (!action) action = keywordFallback(transcript);
  console.log("Intent:", action);

  if (action.startsWith("NAVIGATE:")) {
    const dest = action.split(":")[1].toLowerCase();
    if (DEST_NAMES.includes(dest)) {
      wsSend({ type: "goal", destination: dest });
      const label = DEST_LABELS[dest];
      setStatus("ok", "Going to " + label);
      speak("Okay! Taking you to the " + label + " now.");
      showVoiceHint("Going to " + label);
    } else {
      respondUnknown();
    }

  } else if (action === "STOP") {
    wsSend({ type: "stop" });
    setStatus("estop", "Stopped");
    speak("Stopping now. You are safe.");
    showVoiceHint("Stopped");

  } else if (action === "EMERGENCY") {
    triggerEmergency();

  } else if (action === "STATUS") {
    const dest = lastState?.status === "ok" ? "on my way" : "stopped";
    speak("I am " + dest + ". Just say a room name and I will take you there.");
    showVoiceHint("Ready for a destination");

  } else {
    respondUnknown();
  }
}

function respondUnknown() {
  speak("Sorry, I didn't understand. You can say kitchen, classroom, bedroom, or bathroom.");
  showVoiceHint("Say a room name");
}

// ---------------------------------------------------------------------------
// Speech recognition
// ---------------------------------------------------------------------------
let recognition = null;

function initSpeech() {
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRec) {
    voiceBtn.title = "Speech recognition not supported in this browser";
    voiceBtn.style.opacity = "0.4";
    return;
  }
  recognition = new SpeechRec();
  recognition.continuous     = false;
  recognition.interimResults = false;
  recognition.lang           = "en-IN";

  recognition.onresult = (evt) => {
    const transcript = evt.results[0][0].transcript.toLowerCase().trim();
    handleTranscript(transcript);
  };

  recognition.onerror = (evt) => {
    console.error("Speech error:", evt.error);
    showVoiceHint(evt.error === "no-speech" ? "Nothing heard — try again" : "Mic error: " + evt.error);
  };

  recognition.onend = () => {
    voiceBtn.classList.remove("recording");
  };
}

function startListening() {
  if (!recognition) return;
  try {
    recognition.start();
    voiceBtn.classList.add("recording");
    showVoiceHint("Listening…");
  } catch (e) {
    console.warn("recognition.start() error:", e);
  }
}

function stopListening() {
  if (!recognition) return;
  try { recognition.stop(); } catch (e) { console.warn(e); }
  voiceBtn.classList.remove("recording");
}

voiceBtn.addEventListener("mousedown",  startListening);
voiceBtn.addEventListener("touchstart", startListening, { passive: true });
voiceBtn.addEventListener("mouseup",    stopListening);
voiceBtn.addEventListener("touchend",   stopListening,  { passive: true });

function showVoiceHint(text) {
  voiceHint.textContent = text;
  voiceHint.style.opacity = "1";
  clearTimeout(showVoiceHint._timer);
  showVoiceHint._timer = setTimeout(() => { voiceHint.style.opacity = "0"; }, 3000);
}

// ---------------------------------------------------------------------------
// Speech synthesis
// ---------------------------------------------------------------------------
function speak(text) {
  if (!window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const utt = new SpeechSynthesisUtterance(text);
  utt.rate  = 1.0;
  utt.pitch = 1.1;
  window.speechSynthesis.speak(utt);
}

// ---------------------------------------------------------------------------
// Canvas resize
// ---------------------------------------------------------------------------
function resizeCanvas() {
  const container = mapCanvas.parentElement;
  mapCanvas.width  = container.clientWidth;
  mapCanvas.height = container.clientHeight;
  drawMap();
}

window.addEventListener("resize", resizeCanvas);

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
initSpeech();
resizeCanvas();

// Fetch Gemini key from server, then connect
fetch("/config")
  .then(r => r.json())
  .then(cfg => { if (cfg.gemini_key) GEMINI_API_KEY = cfg.gemini_key; })
  .catch(() => {})
  .finally(() => connectWS());
