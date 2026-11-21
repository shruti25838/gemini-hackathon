import { savedRoom } from "./app-nav.js";

const byId = (id) => document.getElementById(id);
const FRAME_MS = 1000;
const PHASES = {
  measurements: "Measurements", planning_brief: "Preferences", planning: "Planning", review: "Review", placed: "Placed",
};
// Converts microphone audio to 16-bit PCM in 100 ms chunks at the context's 16 kHz rate.
const CAPTURE_WORKLET = `
class Capture extends AudioWorkletProcessor {
  constructor() { super(); this.buffer = new Int16Array(1600); this.length = 0; }
  process(inputs) {
    const channel = inputs[0][0];
    if (channel) for (let i = 0; i < channel.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, channel[i]));
      this.buffer[this.length++] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      if (this.length === this.buffer.length) {
        const chunk = this.buffer.slice();
        this.port.postMessage(chunk.buffer, [chunk.buffer]);
        this.length = 0;
      }
    }
    return true;
  }
}
registerProcessor("capture", Capture);`;

const roomId = new URL(location.href).searchParams.get("room_id") ?? savedRoom();
let socket = null;
let media = null;          // {stream, video, agora: {client, tracks}, remoteVideo}
let capture = null;        // {context, node, source}
let playback = null;       // {context, next, sources, destination, agoraTrack}
let frameTimer = null;
let micMuted = false;
let cameraHidden = true;  // camera is optional; voice works without it
let lastCaption = null;

function line(state, ...parts) {
  const element = byId("voice-line");
  element.hidden = !state;
  element.dataset.state = state ?? "";
  element.replaceChildren(...parts.map((part) => {
    if (part === "spinner") return Object.assign(document.createElement("span"), {className: "spinner"});
    const node = document.createElement(typeof part === "string" ? "span" : "code");
    node.textContent = typeof part === "string" ? part : part.model;
    return node;
  }));
}

function transport(text) {
  byId("transport").lastChild.textContent = " " + text;
}

const toBase64 = (buffer) => {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
};

function send(payload) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload));
}

// ---------- Media: Agora RTC when configured, otherwise this device's camera and microphone ----------

async function openMedia() {
  const channel = `ethyneon-${(roomId ?? "demo").slice(0, 24)}`;
  const response = await fetch(`/studio/voice/agora?channel=${channel}`).catch(() => null);
  if (response?.ok && window.AgoraRTC) {
    const token = await response.json();
    const client = AgoraRTC.createClient({mode: "rtc", codec: "vp8"});
    await client.join(token.appId, channel, token.rtcToken, token.uid);
    const mic = await AgoraRTC.createMicrophoneAudioTrack({AEC: true, ANS: true});
    await client.publish([mic]);
    const result = {
      stream: new MediaStream([mic.getMediaStreamTrack()]),
      agora: {client, tracks: [mic], channel}, remoteVideo: null,
    };
    // A second device (for example a phone) that joins the channel becomes Gemini's eyes and is heard too.
    client.on("user-published", async (user, kind) => {
      await client.subscribe(user, kind);
      if (kind === "video") {
        result.remoteVideo = user.videoTrack;
        byId("remote-preview").hidden = false;
        user.videoTrack.play(byId("remote-preview"));
      }
      if (kind === "audio") {
        user.audioTrack.play();
        capture?.context.createMediaStreamSource(new MediaStream([user.audioTrack.getMediaStreamTrack()])).connect(capture.node);
      }
    });
    client.on("user-unpublished", (user, kind) => {
      if (kind === "video") {
        result.remoteVideo = null;
        byId("remote-preview").hidden = true;
      }
    });
    transport(`Agora channel ${channel}`);
    return result;
  }
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {echoCancellation: true, noiseSuppression: true, channelCount: 1},
  });
  transport(response?.status === 503 ? "This device · Agora not configured" : "This device");
  return {stream, agora: null, remoteVideo: null};
}

async function startCapture(stream) {
  const context = new AudioContext({sampleRate: 16000});
  const url = URL.createObjectURL(new Blob([CAPTURE_WORKLET], {type: "application/javascript"}));
  await context.audioWorklet.addModule(url);
  URL.revokeObjectURL(url);
  const node = new AudioWorkletNode(context, "capture");
  const source = context.createMediaStreamSource(new MediaStream(stream.getAudioTracks()));
  source.connect(node);
  node.port.onmessage = ({data}) => {
    const samples = new Int16Array(data);
    let sum = 0;
    for (const sample of samples) sum += (sample / 0x8000) ** 2;
    byId("level").style.width = `${Math.min(100, Math.sqrt(sum / samples.length) * 400)}%`;
    if (!micMuted) send({type: "audio", data: toBase64(data)});
  };
  return {context, node, source};
}

function startPlayback() {
  const context = new AudioContext({sampleRate: 24000});
  const destination = context.createMediaStreamDestination();
  return {context, next: 0, sources: new Set(), destination, agoraTrack: null};
}

function play(base64) {
  const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
  const samples = new Int16Array(bytes.buffer, 0, Math.floor(bytes.length / 2));
  const buffer = playback.context.createBuffer(1, samples.length, 24000);
  const channel = buffer.getChannelData(0);
  for (let i = 0; i < samples.length; i += 1) channel[i] = samples[i] / 0x8000;
  const source = playback.context.createBufferSource();
  source.buffer = buffer;
  source.connect(playback.context.destination);
  source.connect(playback.destination);
  const start = Math.max(playback.context.currentTime + 0.02, playback.next);
  source.start(start);
  playback.next = start + buffer.duration;
  playback.sources.add(source);
  source.onended = () => playback.sources.delete(source);
}

function stopSpeaking() {
  for (const source of playback?.sources ?? []) source.stop();
  playback?.sources.clear();
  if (playback) playback.next = 0;
}

function sendFrames(video) {
  const canvas = document.createElement("canvas");
  frameTimer = setInterval(() => {
    if (cameraHidden) return;
    const remote = media?.remoteVideo;
    const frame = remote ? remote.getCurrentFrameData?.() : null;
    const width = frame?.width ?? video.videoWidth;
    const height = frame?.height ?? video.videoHeight;
    if (!width || !height) return;
    const scale = Math.min(1, 640 / width);
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    const context = canvas.getContext("2d");
    if (frame) {
      const temp = document.createElement("canvas");
      temp.width = width;
      temp.height = height;
      temp.getContext("2d").putImageData(frame, 0, 0);
      context.drawImage(temp, 0, 0, canvas.width, canvas.height);
    } else {
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
    }
    send({type: "video", data: canvas.toDataURL("image/jpeg", 0.7).split(",", 2)[1]});
  }, FRAME_MS);
}

// ---------- Conversation view ----------

function caption(role, text) {
  const list = byId("captions");
  if (!lastCaption || lastCaption.dataset.role !== role) {
    lastCaption = document.createElement("li");
    lastCaption.dataset.role = role;
    const who = document.createElement("b");
    who.textContent = role === "you" ? "You" : "Gemini";
    lastCaption.append(who, document.createElement("span"));
    list.append(lastCaption);
  }
  lastCaption.lastChild.textContent += text;
  list.scrollTop = list.scrollHeight;
}

function toolActivity(name, status) {
  const label = name === "confirm_placement" ? "Placing the reviewed items" : "Asking the room planner";
  let item = byId("tools").querySelector(`[data-tool="${name}"][data-status="running"]`);
  if (!item) {
    item = document.createElement("li");
    item.dataset.tool = name;
    byId("tools").append(item);
  }
  item.dataset.status = status;
  item.textContent = `${label}${status === "running" ? "…" : ""}`;
}

function renderChat(chat) {
  const phase = byId("phase");
  phase.dataset.status = chat.phase;
  phase.textContent = PHASES[chat.phase] ?? chat.phase;
  const assistant = chat.messages.filter((message) => message.role === "assistant");
  byId("planner-text").textContent = assistant.at(-1)?.text ?? "";
  byId("confirm").hidden = chat.phase !== "review";
  byId("to-design").hidden = chat.phase !== "placed";
}

// ---------- Session ----------

function controls(live) {
  const start = byId("start");
  start.dataset.live = String(live);
  start.textContent = live ? "End voice session" : "Start voice session";
  for (const id of ["mute-mic", "mute-camera", "type-input", "type-send"]) byId(id).disabled = !live;
  byId("sees-badge").hidden = !live || cameraHidden;
  byId("camera-empty").hidden = live && !cameraHidden;
}

async function start() {
  byId("start").disabled = true;
  line("working", "spinner", "Opening the microphone…");
  try {
    media = await openMedia();
    const video = byId("preview");
    capture = await startCapture(media.stream);
    playback = startPlayback();
    if (media.agora) {
      playback.agoraTrack = AgoraRTC.createCustomAudioTrack({mediaStreamTrack: playback.destination.stream.getAudioTracks()[0]});
      await media.agora.client.publish(playback.agoraTrack);
    }
    line("working", "spinner", "Connecting to", {model: "gemini-3.8-live"});
    socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/studio/voice/live${roomId ? `?room_id=${roomId}` : ""}`);
    socket.onmessage = ({data}) => onMessage(JSON.parse(data), video);
    socket.onclose = () => stop("Voice session ended.");
    socket.onerror = () => line("blocked", "Could not reach the voice server.");
  } catch (error) {
    line("blocked", error.name === "NotAllowedError" ? "Allow microphone access to talk to Gemini." : error.message);
    await stop();
  } finally {
    byId("start").disabled = false;
  }
}

function onMessage(message, video) {
  if (message.type === "ready") {
    controls(true);
    sendFrames(video);
    renderChat(message.chat);
    line("ok", "Live with", {model: message.model}, cameraHidden ? "· listening (camera off)" : "· listening and seeing your camera");
  } else if (message.type === "audio") {
    play(message.data);
  } else if (message.type === "interrupted") {
    stopSpeaking();
  } else if (message.type === "caption") {
    caption(message.role, message.text);
  } else if (message.type === "turn_complete") {
    lastCaption = null;
  } else if (message.type === "tool") {
    toolActivity(message.name, message.status);
  } else if (message.type === "chat") {
    renderChat(message.chat);
  } else if (message.type === "notice") {
    line("blocked", message.message);
  } else if (message.type === "error") {
    line("blocked", message.message);
  }
}

async function stop(reason) {
  clearInterval(frameTimer);
  frameTimer = null;
  const open = socket;
  socket = null;
  if (open && open.readyState <= WebSocket.OPEN) open.close();
  stopSpeaking();
  await capture?.context.close().catch(() => {});
  await playback?.context.close().catch(() => {});
  capture = playback = null;
  if (media?.agora) {
    for (const track of media.agora.tracks) track.close();
    await media.agora.client.leave().catch(() => {});
  }
  for (const track of media?.stream.getTracks() ?? []) track.stop();
  media = null;
  byId("preview").srcObject = null;
  byId("remote-preview").hidden = true;
  byId("level").style.width = "0";
  controls(false);
  if (reason) line("ok", reason);
}

byId("start").addEventListener("click", () => (socket ? stop("Voice session ended.") : start()));
byId("mute-mic").addEventListener("click", (event) => {
  micMuted = !micMuted;
  event.currentTarget.setAttribute("aria-pressed", String(micMuted));
  event.currentTarget.textContent = micMuted ? "Unmute mic" : "Mute mic";
});
// Optional camera: opened only when the shopper chooses to show Gemini the room.
async function setCamera(on) {
  const button = byId("mute-camera");
  if (on && media && !media.stream.getVideoTracks().length) {
    try {
      if (media.agora) {
        const camera = await AgoraRTC.createCameraVideoTrack({encoderConfig: "480p_1"});
        await media.agora.client.publish(camera);
        media.agora.tracks.push(camera);
        media.stream.addTrack(camera.getMediaStreamTrack());
      } else {
        const video = await navigator.mediaDevices.getUserMedia({video: {width: {ideal: 640}, height: {ideal: 480}}});
        media.stream.addTrack(video.getVideoTracks()[0]);
      }
    } catch {
      line("blocked", "Camera not available. Voice keeps working without it.");
      return;
    }
  }
  for (const track of media?.stream.getVideoTracks() ?? []) track.enabled = on;
  byId("preview").srcObject = on && media ? new MediaStream(media.stream.getVideoTracks()) : null;
  cameraHidden = !on;
  button.setAttribute("aria-pressed", String(on));
  button.textContent = on ? "Hide camera" : "Show camera to Gemini";
  byId("sees-badge").hidden = !on;
  byId("camera-empty").hidden = on;
}
byId("mute-camera").addEventListener("click", () => setCamera(cameraHidden));
byId("type-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = byId("type-input").value.trim();
  if (!text) return;
  byId("type-input").value = "";
  caption("you", text);
  lastCaption = null;
  send({type: "text", text});
});
byId("confirm").addEventListener("click", () => send({type: "confirm"}));
addEventListener("pagehide", () => stop());
