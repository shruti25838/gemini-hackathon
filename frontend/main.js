import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const $ = (id) => document.getElementById(id);
const api = async (path, opts = {}) => {
  const r = await fetch(`/api/session/${SID}${path}`, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.statusText);
  return body;
};
const post = (path, json) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(json ?? {}) });
const money = (m) => `$${(m / 100).toFixed(2)}`;

let SID = sessionStorage.getItem("ethyneon_sid");
let lastSeq = 0, st = null, sceneKey = "", selectedKey = null, highlight = [], frameBlob = null, booted = false;

// ---------- three.js ----------
const vp = $("viewport");
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(devicePixelRatio);
vp.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b0d11);
const camera = new THREE.PerspectiveCamera(50, 1, 0.05, 100);
camera.position.set(1.6, 4.2, 5.5);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(1.6, 0, 1.5);
const ambient = new THREE.AmbientLight(0xffffff, 0.6);
const sun = new THREE.DirectionalLight(0xffffff, 1.2);
sun.position.set(3, 6, 4);
scene.add(ambient, sun);
const roomGroup = new THREE.Group();
scene.add(roomGroup);
const itemMeshes = new Map();

function resize() {
  const { clientWidth: w, clientHeight: h } = vp;
  renderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
addEventListener("resize", resize);
resize();
renderer.setAnimationLoop(() => renderer.render(scene, camera));

const LIGHT = { daylight: [0xffffff, 0.65, 1.2], evening: [0xffd2a1, 0.5, 0.8], rgb: [0xf2f0ff, 0.55, 0.9] };
const accentLight = new THREE.PointLight(0xffffff, 0, 6);
scene.add(accentLight);
const CAT_COLOR = { desk: 0x8d6e63, chair: 0x7c6cf0, monitor: 0x222831, storage: 0xa1887f };

function label(text, color = "#fff") {
  const c = document.createElement("canvas"), g = c.getContext("2d"), fs = 44;
  g.font = `600 ${fs}px system-ui`; c.width = Math.min(1024, g.measureText(text).width + 32); c.height = fs + 24;
  g.font = `600 ${fs}px system-ui`; g.fillStyle = "rgba(15,17,21,0.75)"; g.fillRect(0, 0, c.width, c.height);
  g.fillStyle = color; g.textBaseline = "middle"; g.fillText(text, 16, c.height / 2);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthTest: false }));
  sp.scale.set(c.width / c.height * 0.22, 0.22, 1); sp.renderOrder = 10; return sp;
}

function buildRoom() {
  roomGroup.clear();
  itemMeshes.clear();
  if (!st?.room) return;
  const r = st.room, W = r.width_m.value_m, D = r.depth_m.value_m, H = r.height_m.value_m;
  const sc = st.scene || {};
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(W, D), new THREE.MeshStandardMaterial({ color: r.floor_color_hex }));
  floor.rotation.x = -Math.PI / 2; floor.position.set(W / 2, 0, D / 2); floor.name = "floor";
  roomGroup.add(floor);
  const wallColor = (id) => (sc.wall_colors || {})[id] || (r.walls.find((w) => w.id === id) || {}).color_hex || "#d9d4cc";
  const walls = { wall_n: [W, [W / 2, H / 2, 0], 0], wall_w: [D, [0, H / 2, D / 2], Math.PI / 2], wall_e: [D, [W, H / 2, D / 2], -Math.PI / 2] };
  for (const [id, [len, pos, rotY]] of Object.entries(walls)) {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(len, H), new THREE.MeshStandardMaterial({ color: wallColor(id), side: THREE.DoubleSide, transparent: true, opacity: 0.92 }));
    m.position.set(...pos); m.rotation.y = rotY; roomGroup.add(m);
  }
  // openings drawn on their walls
  for (const o of r.openings) {
    const color = o.type === "door" ? 0xff5ea8 : 0x66ccff;
    const g = new THREE.Mesh(new THREE.PlaneGeometry(o.width_m, o.height_m), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.55, side: THREE.DoubleSide }));
    const mid = o.offset_m + o.width_m / 2, y = o.sill_m + o.height_m / 2;
    if (o.wall === "wall_n") g.position.set(mid, y, 0.01);
    else if (o.wall === "wall_s") { g.position.set(mid, y, D - 0.01); }
    else if (o.wall === "wall_w") { g.position.set(0.01, y, mid); g.rotation.y = Math.PI / 2; }
    else { g.position.set(W - 0.01, y, mid); g.rotation.y = Math.PI / 2; }
    roomGroup.add(g);
  }
  for (const arc of st.door_arcs || []) {
    const shape = new THREE.Shape(arc.points.map(([x, z]) => new THREE.Vector2(x, z)));
    const m = new THREE.Mesh(new THREE.ShapeGeometry(shape), new THREE.MeshBasicMaterial({ color: 0xff5ea8, transparent: true, opacity: 0.25, side: THREE.DoubleSide }));
    m.rotation.x = Math.PI / 2; m.position.y = 0.01; roomGroup.add(m);
  }
  const bad = new Set((st.fit?.collisions || []).flatMap((c) => [c.a, c.b]));
  const placed = new Set((sc.placements || []).map((p) => p.key));
  const owned = (r.existing_items || []).filter((i) => !placed.has(i.id))
    .map((i) => ({ key: i.id, category: i.label, x: i.x, z: i.z, w: i.w, d: i.d, h: i.h, rot: 0, locked: true }));
  for (const p of [...(sc.placements || []), ...owned]) {
    const rot = p.rot === 90 || p.rot === 270;
    const w = rot ? p.d : p.w, d = rot ? p.w : p.d;
    const mat = new THREE.MeshStandardMaterial({ color: bad.has(p.key) ? 0xff6b6b : p.locked ? 0x5f6b7a : (CAT_COLOR[p.category] || 0x9e9e9e), transparent: p.locked, opacity: p.locked ? 0.7 : 1 });
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, p.h || 0.75, d), mat);
    m.position.set(p.x, (p.h || 0.75) / 2, p.z);
    m.userData = { key: p.key, locked: p.locked, w, d };
    if (p.key === selectedKey || highlight.includes(p.key)) { m.material.emissive = new THREE.Color(p.key === selectedKey ? 0x7c6cf0 : 0xff5ea8); m.material.emissiveIntensity = 0.6; }
    roomGroup.add(m); itemMeshes.set(p.key, m);
    const name = (st.selected.find((o) => o.variant_id === p.variant_id)?.title) || (p.locked ? `${p.category} (yours)` : p.category);
    const lab = label(name, p.locked ? "#9aa3b5" : "#ffffff");
    lab.position.set(p.x, (p.h || 0.75) + 0.25, p.z); roomGroup.add(lab);
  }
  const [lc, ai, si] = LIGHT[sc.lighting || "daylight"];
  ambient.color.set(lc); ambient.intensity = ai; sun.intensity = si;
  // accent / RGB = a coloured glow near the desk wall, like an LED bar; the room keeps its real colours
  const glow = sc.accent_hex || (sc.lighting === "rgb" ? "#7c6cf0" : null);
  accentLight.intensity = glow ? 2.5 : 0;
  if (glow) { accentLight.color.set(glow); accentLight.position.set(W / 2, 1.1, 0.4); }
  controls.target.set(W / 2, 0.3, D / 2);
  if (!buildRoom.framed) { camera.position.set(W * 0.5, Math.max(W, D) * 0.95, D * 1.9); buildRoom.framed = true; }
}

// drag items on the floor plane; drop -> server move review
const ray = new THREE.Raycaster(), mouse = new THREE.Vector2(), plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
let drag = null;
function ndc(e) { const b = renderer.domElement.getBoundingClientRect(); mouse.set(((e.clientX - b.left) / b.width) * 2 - 1, -((e.clientY - b.top) / b.height) * 2 + 1); ray.setFromCamera(mouse, camera); }
renderer.domElement.addEventListener("pointerdown", (e) => {
  ndc(e);
  const hit = ray.intersectObjects([...itemMeshes.values()])[0];
  if (hit) { selectedKey = hit.object.userData.key; highlight = []; sceneKey = ""; render(); $("selInfo").textContent = `Selected: ${selectedKey}`; }
  if (hit && !hit.object.userData.locked) { drag = hit.object; controls.enabled = false; }
});
renderer.domElement.addEventListener("pointermove", (e) => {
  if (!drag) return; ndc(e);
  const p = new THREE.Vector3(); ray.ray.intersectPlane(plane, p);
  drag.position.x = Math.round(p.x * 20) / 20; drag.position.z = Math.round(p.z * 20) / 20;
});
addEventListener("pointerup", async () => {
  if (!drag) return;
  const m = drag; drag = null; controls.enabled = true;
  const p = st.scene.placements.find((q) => q.key === m.userData.key);
  try { await post("/move", { key: p.key, x: m.position.x, z: m.position.z, rot: p.rot }); } catch (err) { flash(err.message); }
});

// ---------- UI rendering ----------
function render() {
  const key = JSON.stringify([st.room?.room_revision, st.room?.existing_items?.length, st.scene?.rev, st.fit?.status, st.door_arcs?.length, st.scene?.lighting, st.scene?.wall_colors]);
  if (key !== sceneKey) { sceneKey = key; buildRoom(); }
  const fb = $("fitBadge"); const fs = st.fit?.status || "—";
  fb.textContent = `fit: ${fs}${st.fit?.checked_by?.length ? " · " + st.fit.checked_by.join(" + ") : ""}`; fb.className = `badge ${fs}`;
  renderDims(); renderDoor(); renderProducts(); renderAlt();
  renderPending();
}

let pendKey = null, clarifyNote = false;
function renderPending() {
  const pend = st.pending, key = pend ? JSON.stringify(pend) : null;
  if (key === pendKey) return;  // unchanged: never wipe what the shopper is typing
  pendKey = key;
  if (!pend) { if (!clarifyNote) $("clarify").hidden = true; return; }
  clarifyNote = false; $("clarify").hidden = false;
  $("clarify").innerHTML = `<b>Needs you:</b> ${pend.prompt}` + (pend.kind === "measurement"
    ? ` <input id="pendVal" size="6" placeholder="metres" inputmode="decimal"> <button id="pendBtn">Confirm</button>` : "");
  if (pend.kind === "measurement") {
    const submit = () => {
      const v = parseFloat($("pendVal").value);
      if (!(v > 0)) { flash("Type the length in metres, e.g. 3.2"); return; }
      post("/measure", { field: pend.field, value_m: v }).then(refresh).catch((e) => flash(e.message));
    };
    $("pendBtn").onclick = submit;
    $("pendVal").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } };
    $("pendVal").focus();
  }
}

let dimsKey = null;
function renderDims() {
  const r = st.room; if (!r) return;
  const key = JSON.stringify([r.room_revision, r.width_m, r.depth_m, r.height_m, r.notes.length]);
  if (key === dimsKey || $("roomDims").contains(document.activeElement)) return;
  dimsKey = key;
  const row = (k, label) => `<label>${label} <span><input data-f="${k}" value="${r[k].value_m.toFixed(2)}"> m <span class="src ${r[k].source}">${r[k].source}</span></span></label>`;
  $("roomDims").innerHTML = `<div class="dims">${row("width_m", "Far wall (wall_n)")}${row("depth_m", "Depth")}${row("height_m", "Height")}</div>
    <p class="hint">Measure one wall and type it in: every estimated length rescales from it.</p>
    ${r.notes.length ? `<p class="hint">Uncertain: ${r.notes.slice(-3).join("; ")}</p>` : ""}`;
  $("roomDims").querySelectorAll("input").forEach((inp) => inp.onchange = () =>
    post("/measure", { field: inp.dataset.f, value_m: parseFloat(inp.value) }).then(refresh).catch((e) => flash(e.message)));
}

let doorKey = null;
function renderDoor() {
  const doors = (st.room?.openings || []).filter((o) => o.type === "door");
  const key = JSON.stringify(doors);
  if (key === doorKey) return;
  doorKey = key;
  $("doorManual").innerHTML = doors.map((d) => `<div class="hint">${d.id} (${d.wall} wall): ${d.motion ? `hinge ${d.motion.hinge_side}, swings ${d.motion.swing} <span class="src ${d.motion.source}">${d.motion.source}</span>` : "swing unknown"}
    <br>Set: <button class="secondary" data-o="${d.id}" data-h="left">hinge L</button><button class="secondary" data-o="${d.id}" data-h="right">hinge R</button>
    <button class="secondary" data-o="${d.id}" data-s="in">opens in</button><button class="secondary" data-o="${d.id}" data-s="out">opens out</button></div>`).join("");
  $("doorManual").querySelectorAll("button").forEach((b) => b.onclick = () =>
    post("/door/manual", { opening_id: b.dataset.o, ...(b.dataset.h ? { hinge_side: b.dataset.h } : { swing: b.dataset.s }) }).then(refresh).catch((e) => flash(e.message)));
}

const notBuying = new Set();
let prodKey = null;
function renderProducts() {
  const key = JSON.stringify(st.selected.map((o) => o.variant_id));
  if (key === prodKey) return;
  prodKey = key;
  $("products").innerHTML = st.selected.map((o) => `<div class="prod"><input type="checkbox" class="buy" data-v="${o.variant_id}" ${notBuying.has(o.variant_id) ? "" : "checked"} title="Buy this item">${o.image_url ? `<img src="${o.image_url}" alt="">` : `<div class="noimg"></div>`}
    <div class="t">${o.title}<br><span class="hint">${o.variant_title} · ${o.category}${o.dims_m ? "" : " · dims unknown"}</span>
    ${o.source === "fixture" ? '<div class="tag">FIXTURE CATALOG · NOT LIVE</div>' : ""}</div><div class="p">${money(o.price_minor)}</div></div>`).join("") || '<p class="hint">No products yet.</p>';
  $("products").querySelectorAll("input.buy").forEach((cb) => cb.onchange = () => {
    cb.checked ? notBuying.delete(cb.dataset.v) : notBuying.add(cb.dataset.v);
    updateSubtotal(); $("review").innerHTML = "";  // selection changed: review again
  });
  updateSubtotal();
}
function updateSubtotal() {
  const buying = st.selected.filter((o) => !notBuying.has(o.variant_id));
  const sum = buying.reduce((a, o) => a + o.price_minor, 0);
  $("subtotal").textContent = st.selected.length
    ? `buying ${buying.length} of ${st.selected.length} · ${money(sum)} / budget ${money(st.brief.budget_minor)}` : "";
}

function renderAlt() {
  const a = st.alternative, el = $("altCard");
  el.hidden = !a; if (!a) return;
  el.innerHTML = `<h3>${a.title || "Placement review"}</h3><p>${a.conflicts.map((c) => c.detail).join("<br>")}</p>
    <p>Checked alternative: <b>${a.report.status}</b> (checked by ${a.report.checked_by.join(" + ")})${a.style ? `<br><i>Argon:</i> ${a.style}` : ""}</p>
    ${(a.findings || []).length ? `<p class="hint"><i>Carbon / probe findings:</i><br>${a.findings.map((f) => `${f.kind} · ${f.item_key}: ${f.detail} — ${f.reproduced_by_server ? "reproduced by server" : "NOT reproduced by server"}${f.advisory ? " (advisory)" : ""}`).join("<br>")}</p>` : ""}
    <button id="altYes">Use the alternative</button> <button class="secondary" id="altNo">Keep previous</button>`;
  $("altYes").onclick = () => post("/alternative/accept").then(refresh);
  $("altNo").onclick = () => post("/alternative/reject").then(refresh);
}

function renderEvents(evs) {
  const ol = $("activity");
  for (const e of evs) {
    if (e.seq <= lastSeq) continue;  // already shown
    const li = document.createElement("li"); li.className = e.status;
    const meta = [e.data?.latency_ms && `${e.data.latency_ms} ms`, e.data?.interaction_id && `interaction ${String(e.data.interaction_id).slice(0, 10)}`, e.data?.environment_id && `sandbox ${String(e.data.environment_id).slice(0, 10)}`].filter(Boolean).join(" · ");
    li.innerHTML = `<span class="step">${e.step}</span> ${e.detail}${meta ? ` <span class="meta">(${meta})</span>` : ""}`;
    if (booted && voice.on) { const line = SPOKEN(e); if (line) say(line); }
    ol.prepend(li);
    lastSeq = e.seq;
  }
}

function flash(msg) { const li = document.createElement("li"); li.className = "error"; li.textContent = msg; $("activity").prepend(li); }

let refreshing = false;
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const [s, evs] = await Promise.all([api("/state"), api(`/events?since=${lastSeq}`)]);
    st = s; renderEvents(evs); render();
  } catch (e) { if (String(e.message).includes("unknown session")) { sessionStorage.removeItem("ethyneon_sid"); location.reload(); } }
  finally { refreshing = false; }
}

// ---------- inputs ----------
async function reencode(file, max = 1600) {
  const bmp = await createImageBitmap(file);
  const k = Math.min(1, max / Math.max(bmp.width, bmp.height));
  const c = Object.assign(document.createElement("canvas"), { width: Math.round(bmp.width * k), height: Math.round(bmp.height * k) });
  c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
  return new Promise((res) => c.toBlob(res, "image/jpeg", 0.88)); // canvas output carries no EXIF/GPS
}

$("presetBtn").onclick = () => post("/preset").then(refresh).catch((e) => flash(e.message));

$("photo").onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return;
  let blob; try { blob = await reencode(f); } catch { flash("Couldn't decode that image. Try a JPEG/PNG or take a photo."); return; }
  $("photoPreview").src = URL.createObjectURL(blob); $("photoPreview").style.display = "block";
  const fd = new FormData(); fd.append("file", blob, "room.jpg");
  try { const r = await api("/photo", { method: "POST", body: fd }); if (!r.accepted) flash(r.gate.message); } catch (err) { flash(err.message); }
  refresh();
};

$("doorClip").onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return;
  $("doorInfo").textContent = "Uploading and checking the clip (a few seconds)…";
  const fd = new FormData(); fd.append("file", f, f.name);
  try { const r = await api("/door", { method: "POST", body: fd }); $("doorInfo").textContent = r.accepted ? r.message : (r.problem || r.gate?.message); }
  catch (err) { $("doorInfo").textContent = err.message; }
  refresh();
};

async function sendCommand(fd, spoken = false) {
  if (selectedKey) fd.append("selected_key", selectedKey);
  if (frameBlob) { fd.append("frame", frameBlob, "frame.jpg"); frameBlob = null; $("frameInfo").textContent = ""; }
  try {
    const r = await api("/command", { method: "POST", body: fd });
    if (spoken) transcript("you", r.transcript);
    if (spoken && r.say && r.status !== "clarify") say(r.say);
    if (r.status === "clarify") {
      clarifyNote = true; pendKey = "__note"; $("clarify").hidden = false; $("clarify").innerHTML = `<b>Quick check:</b> ${r.message}`;
      highlight = r.candidates || []; sceneKey = ""; render();
      if (spoken) say(r.message);
    }
    if (r.status === "review") showReview(r.review);
  } catch (err) { flash(err.message); }
  refresh();
}
$("frameIn").onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return;
  try { frameBlob = await reencode(f, 1024); $("frameInfo").textContent = "Camera frame attached to your next request"; } catch { flash("Couldn't read that image."); }
};
$("cmdForm").onsubmit = (e) => { e.preventDefault(); const t = $("cmdText").value.trim(); if (!t) return; const fd = new FormData(); fd.append("text", t); $("cmdText").value = ""; sendCommand(fd); };

// ---------- hands-free voice: VAD -> Hydrogen -> spoken reply, with barge-in ----------
const voice = { on: false, state: "off", stream: null, ctx: null, analyser: null, rec: null, chunks: [], floor: 0.01,
                above: 0, belowSince: 0, startedAt: 0, raf: 0 };
function setVoice(state) {
  voice.state = state;
  const labels = { off: "🎙 Talk", listening: "● Listening", hearing: "● Hearing you", thinking: "… Thinking", speaking: "🔊 Speaking" };
  $("micBtn").textContent = labels[state]; $("micBtn").classList.toggle("rec", state !== "off");
  $("voiceState").textContent = state === "off" ? "" : `voice: ${state}`;
}
function transcript(who, text) {
  if (!text) return;
  const d = document.createElement("div"); d.className = `tx ${who}`; d.textContent = `${who === "you" ? "You" : "Ethyneon"}: ${text}`;
  $("transcript").prepend(d);
}
function say(text) {
  if (!voice.on || !text || !("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(text); u.rate = 1.05;
  u.onstart = () => setVoice("speaking");
  u.onend = () => { if (voice.on && voice.state === "speaking") setVoice("listening"); };
  speechSynthesis.speak(u); transcript("ethyneon", text);
}
function rms() {
  const buf = new Float32Array(voice.analyser.fftSize); voice.analyser.getFloatTimeDomainData(buf);
  let s = 0; for (const x of buf) s += x * x; return Math.sqrt(s / buf.length);
}
function startSegment() {
  voice.chunks = []; voice.startedAt = performance.now();
  voice.rec = new MediaRecorder(voice.stream);
  voice.rec.ondataavailable = (e) => e.data.size && voice.chunks.push(e.data);
  voice.rec.onstop = () => {
    const dur = performance.now() - voice.startedAt;
    if (dur < 450 || !voice.on) { if (voice.on) setVoice("listening"); return; }  // too short: cough, click, echo
    setVoice("thinking");
    const fd = new FormData(); fd.append("audio", new Blob(voice.chunks, { type: voice.rec.mimeType }), "voice.webm");
    sendCommand(fd, true).finally(() => { if (voice.on && voice.state === "thinking") setVoice("listening"); });
  };
  voice.rec.start(100);
}
function loop() {
  if (!voice.on) return;
  const level = rms(), now = performance.now();
  // speaking raises the bar so our own voice through the speakers doesn't trigger barge-in
  const thr = Math.max(0.012, voice.floor * 3) * (voice.state === "speaking" ? 2.2 : 1);
  if (level > thr) {
    voice.above++; voice.belowSince = 0;
    if (voice.above === 3 && voice.state !== "hearing") {
      if (speechSynthesis.speaking) speechSynthesis.cancel();  // barge-in: the shopper interrupts
      setVoice("hearing"); startSegment();
    }
  } else {
    voice.above = 0;
    if (voice.state === "hearing") {
      voice.belowSince ||= now;
      if (now - voice.belowSince > 800 || now - voice.startedAt > 15000) { voice.belowSince = 0; voice.rec.stop(); }
    } else if (voice.state === "listening") voice.floor = voice.floor * 0.98 + level * 0.02;  // track room noise
  }
  voice.raf = requestAnimationFrame(loop);
}
async function voiceOn() {
  try {
    voice.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  } catch { flash("Microphone unavailable or not allowed. Type instead."); return; }
  voice.ctx = new AudioContext(); voice.analyser = voice.ctx.createAnalyser(); voice.analyser.fftSize = 1024;
  voice.ctx.createMediaStreamSource(voice.stream).connect(voice.analyser);
  voice.on = true; setVoice("listening"); loop();
  say("I'm listening. Tell me your budget and the look you want.");
}
function voiceOff() {
  voice.on = false; cancelAnimationFrame(voice.raf); speechSynthesis.cancel();
  if (voice.rec?.state === "recording") voice.rec.stop();
  voice.stream?.getTracks().forEach((t) => t.stop()); voice.ctx?.close(); setVoice("off");
}
$("micBtn").onclick = () => (voice.on ? voiceOff() : voiceOn());

// speak the workflow's important moments while voice is on
const SPOKEN = (e) => {
  if (e.status === "needs_input") return e.detail;
  if (e.step === "commit_scene_preview" && e.status === "ok") return `Done. The layout ${e.data?.fit?.status === "fits" ? "fits" : "is " + (e.data?.fit?.status || "ready").replace(/_/g, " ")}.`;
  if (e.step === "scene" && e.status === "ok") return "Moved. " + (e.detail.includes("fits") ? "It fits." : "");
  if (e.step === "agent_review" && e.status === "ok") return "The sandbox agents checked it too.";
  if (e.step === "workflow" && e.status === "error") return "Something went wrong. Your last layout is unchanged.";
  return null;
};

$("undoBtn").onclick = () => post("/undo").then(refresh).catch((e) => flash(e.message));

function showReview(rv) {
  $("review").innerHTML = `<table>${rv.lines.map((l) => `<tr><td>${l.title} · ${l.variant_title}</td><td class="r">${l.quantity} × ${money(l.price_minor)}</td></tr>`).join("")}
    <tr><td><b>Items subtotal</b></td><td class="r"><b>${money(rv.subtotal_minor)}</b></td></tr></table>
    <p class="hint">${rv.note} Fit: ${rv.fit_status}. ${rv.within_budget ? "Within budget." : "OVER BUDGET."}</p>
    ${rv.problems.length ? `<p class="clarify">${rv.problems.join("<br>")}</p>` : ""}
    <button id="approveBtn" ${rv.problems.length ? "disabled" : ""}>Confirm and add to cart</button> <button class="secondary" id="backBtn">Go back</button>
    <div id="cartBox"></div>`;
  $("backBtn").onclick = () => ($("review").innerHTML = "");
  $("approveBtn").onclick = async () => {
    try {
      const c = await post("/approve");
      $("cartBox").innerHTML = `<p>Cart ready: ${c.lines.length} lines, ${money(c.subtotal_minor)}. Next step opens Shopify checkout. Shipping and tax are added there.</p>
        <button id="checkoutBtn">Continue to secure checkout</button>`;
      $("checkoutBtn").onclick = async () => {
        const w = window.open("about:blank", "_blank");
        try {
          const r = await post("/checkout");
          if (r.released) { w.location = r.checkout_url; $("cartBox").innerHTML += `<p class="hint">Checkout opened (test mode). Payment not confirmed yet.</p>`; }
          else { w.close(); $("cartBox").innerHTML += `<p class="clarify">Checkout held by audit: ${JSON.stringify(r.audit.checks)}</p>`; }
        } catch (err) { w.close(); flash(err.message); }
        refresh();
      };
    } catch (err) { flash(err.message); }
    refresh();
  };
}
$("reviewBtn").onclick = () => post("/review", { exclude: [...notBuying] }).then(showReview).catch((e) => flash(e.message));

// ---------- boot ----------
(async () => {
  const h = await (await fetch("/api/health")).json();
  $("status").innerHTML = `<span class="fixture" title="Gemini Live is not enabled on this API key">Voice: Hydrogen + browser speech</span>
    <span class="${h.mock_ai ? "fixture" : h.gemini ? "live" : "off"}">${h.mock_ai ? "AI: MOCK (saved outputs)" : h.gemini ? "Gemini live" : "Gemini: no key"}</span>
    <span class="${h.shopify_live ? "live" : h.fixture_catalog ? "fixture" : "off"}">${h.shopify_live ? "Shopify live" : h.fixture_catalog ? "Fixture catalog · not live" : "Shopify not configured"}</span>`;
  if (!SID) { SID = (await (await fetch("/api/session", { method: "POST" })).json()).id; sessionStorage.setItem("ethyneon_sid", SID); }
  await refresh(); booted = true;
  setInterval(refresh, 1000);
})();
