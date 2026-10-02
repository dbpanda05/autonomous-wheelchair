/**
 * app.js — Saarthi Dashboard WebSocket client
 *
 * Connects to ws://localhost:8080/ws
 * Handles: state updates, destination goals, stop, voice commands
 */

"use strict";

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------
const statusText   = document.getElementById("status-text");
const statusPill   = document.getElementById("status-pill");
const speedText    = document.getElementById("speed-text");
const mapCanvas    = document.getElementById("map-canvas");
const mapOverlay   = document.getElementById("map-overlay-msg");
const stopBtn      = document.getElementById("stop-btn");
const voiceBtn     = document.getElementById("voice-btn");
const voiceHint    = document.getElementById("voice-hint");
const destButtons  = document.querySelectorAll("[data-dest]");

const ctx = mapCanvas.getContext("2d");

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let ws = null;
let lastState = null;
let mapImg = null;        // cached Image object for the map PNG
let lastMapPng = "";

const STATUS_LABELS = {
  ok:        "Ready",
  estop:     "E-Stop",
  cliff:     "Step ahead",
  watchdog:  "Watchdog",
  obstacle:  "Obstacle",
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

  ws.onerror = (e) => {
    console.error("WS error", e);
  };

  ws.onclose = () => {
    console.warn("WS closed — reconnecting in 2 s");
    setStatus("estop", "Disconnected — reconnecting…");
    setTimeout(connectWS, 2000);
  };
}

function wsSend(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
  } else {
    console.warn("WS not open — message dropped", obj);
  }
}

// ---------------------------------------------------------------------------
// State handler
// ---------------------------------------------------------------------------
function handleState(msg) {
  lastState = msg;

  // Status bar
  const label = STATUS_LABELS[msg.status] || msg.status;
  setStatus(msg.status, label);
  speedText.textContent = (Math.abs(msg.speed) * 100 | 0) / 100 + " m/s";

  // Map
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

  // Background
  ctx.fillStyle = "#1a1f2e";
  ctx.fillRect(0, 0, W, H);

  if (mapImg) {
    ctx.drawImage(mapImg, 0, 0, W, H);
  } else {
    // Placeholder grid
    ctx.strokeStyle = "#2a3050";
    ctx.lineWidth = 1;
    for (let x = 0; x < W; x += 20) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
    }
    for (let y = 0; y < H; y += 20) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    }
    ctx.fillStyle = "#555";
    ctx.font = "12px monospace";
    ctx.textAlign = "center";
    ctx.fillText("Waiting for map…", W / 2, H / 2);
  }

  if (!lastState) return;

  const meta = lastState.map_meta || {};
  const res  = meta.resolution || 0.05;
  const ox   = meta.origin_x || 0;
  const oy   = meta.origin_y || 0;
  const mw   = meta.width  || 200;
  const mh   = meta.height || 200;

  const pose = lastState.pose || { x: 0, y: 0, theta: 0 };

  // World → pixel
  // Map origin is bottom-left; canvas 0,0 is top-left
  const px = ((pose.x - ox) / (mw * res)) * W;
  const py = H - ((pose.y - oy) / (mh * res)) * H;
  const r  = Math.max(6, W / 30);

  // Heading arrow endpoint
  const arrowLen = r * 2.5;
  const ax = px + arrowLen * Math.cos(pose.theta);
  const ay = py - arrowLen * Math.sin(pose.theta); // canvas y is flipped

  // Draw arrow
  ctx.strokeStyle = "#00ccff";
  ctx.lineWidth   = 2;
  ctx.beginPath();
  ctx.moveTo(px, py);
  ctx.lineTo(ax, ay);
  ctx.stroke();

  // Arrow head
  const headLen = 8;
  const angle   = Math.atan2(py - ay, ax - px);
  ctx.beginPath();
  ctx.moveTo(ax, ay);
  ctx.lineTo(ax - headLen * Math.cos(angle - 0.4), ay + headLen * Math.sin(angle - 0.4));
  ctx.lineTo(ax - headLen * Math.cos(angle + 0.4), ay + headLen * Math.sin(angle + 0.4));
  ctx.closePath();
  ctx.fillStyle = "#00ccff";
  ctx.fill();

  // Robot dot
  ctx.beginPath();
  ctx.arc(px, py, r, 0, 2 * Math.PI);
  ctx.fillStyle = "#1e90ff";
  ctx.fill();
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth   = 2;
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
    setStatus("ok", "Navigating to " + label);
    speak("Navigating to " + label);
  });
});

// ---------------------------------------------------------------------------
// Stop button
// ---------------------------------------------------------------------------
stopBtn.addEventListener("click", () => {
  wsSend({ type: "stop" });
  setStatus("estop", "STOPPED");
  speak("Stopped");
});

// ---------------------------------------------------------------------------
// Voice recognition
// ---------------------------------------------------------------------------
const DESTINATIONS = ["kitchen", "classroom", "bedroom", "bathroom"];

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
  recognition.lang           = "en-US";

  recognition.onresult = (evt) => {
    const transcript = evt.results[0][0].transcript.toLowerCase().trim();
    console.log("Voice transcript:", transcript);
    const matched = DESTINATIONS.find((d) => transcript.includes(d));
    if (matched) {
      wsSend({ type: "goal", destination: matched });
      const label = matched.charAt(0).toUpperCase() + matched.slice(1);
      setStatus("ok", "Navigating to " + label);
      speak("Navigating to " + label);
      showVoiceHint("Going to " + label);
    } else {
      showVoiceHint("Didn't catch that — try again");
      speak("Didn't catch that, try again");
    }
  };

  recognition.onerror = (evt) => {
    console.error("Speech error:", evt.error);
    showVoiceHint("Couldn't hear — try again");
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
  try {
    recognition.stop();
  } catch (e) {
    console.warn("recognition.stop() error:", e);
  }
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
  showVoiceHint._timer = setTimeout(() => {
    voiceHint.style.opacity = "0";
  }, 3000);
}

// ---------------------------------------------------------------------------
// Speech synthesis
// ---------------------------------------------------------------------------
function speak(text) {
  if (!window.speechSynthesis) return;
  const utt = new SpeechSynthesisUtterance(text);
  utt.rate  = 1.1;
  utt.pitch = 1;
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
connectWS();
