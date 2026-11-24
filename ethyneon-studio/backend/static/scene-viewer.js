import * as THREE from "three";
import { CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { RoomViewer } from "./room-viewer.js";

const SELECTED = 0xb8eb9c;
const BLOCKED = 0xff3b3b;
const CLEAR = 0x3ecf8e;

// A drag that moves less than this was a click. Three centimetres, from
// the lane A front end, because one is small enough that a steady hand on
// a trackpad nudges the product while trying to select it.
const CLICK_THRESHOLD_M = 0.03;
// Products land on a 5 cm grid. Nobody measures a room to the millimetre,
// and a round number is easier to check against a tape.
const SNAP_M = 0.05;
// Mirrors PASSAGE_CLEARANCE_M in backend/scene_edit.py.
const PASSAGE_CLEARANCE_M = 0.6;

const DOT_TITLES = {
  cached: "3D model built from the merchant listing",
  missing: "Stand-in box at the listing size. Not a 3D model of this product",
  building: "Building a 3D model from the merchant listing…",
  failed: "Stand-in box at the listing size. The 3D build failed",
};
// Anything but a real model is a box the size the listing claims. Saying
// so is not optional: a grey box that looks like a desk, unlabelled, is a
// claim about the product that nobody made.
export const STAND_IN_STATES = new Set(["missing", "failed"]);

// Small status dot: green filled = 3D cached, red hollow = no 3D yet, amber pulsing = building.
export function modelDot(state = "missing") {
  const dot = document.createElement("span");
  dot.className = "model-dot";
  setDot(dot, state);
  return dot;
}

export function setDot(dot, state) {
  dot.dataset.state = state;
  dot.title = DOT_TITLES[state] ?? "";
  dot.setAttribute("aria-label", DOT_TITLES[state] ?? "");
  dot.setAttribute("role", "img");
}

// The floor area a door sweeps, or the clearance a passage needs. This is
// opening_zone() in backend/scene_edit.py, line for line. It is duplicated
// rather than approximated on purpose: if the live red used a different
// rule from the one the server decides with, the colour under the
// shopper's hand would disagree with the verdict on release, which is
// worse than showing no colour at all.
export function openingZones(scene) {
  const width = scene.dimensions.width.value_m;
  const depth = scene.dimensions.depth.value_m;
  const zones = [];
  for (const opening of scene.openings ?? []) {
    if (opening.type === "window") continue;
    const swings = opening.type === "door" && !["outward", "sliding"].includes(opening.swing);
    const reach = swings ? opening.width_m : PASSAGE_CLEARANCE_M;
    const along = opening.offset_m + opening.width_m / 2;
    const box = {
      wall_n: {x: along, z: reach / 2, w: opening.width_m, d: reach},
      wall_s: {x: along, z: depth - reach / 2, w: opening.width_m, d: reach},
      wall_w: {x: reach / 2, z: along, w: reach, d: opening.width_m},
      wall_e: {x: width - reach / 2, z: along, w: reach, d: opening.width_m},
    }[opening.wall];
    if (box) zones.push({...box, label: opening.id});
  }
  return zones;
}

// The axis-aligned bounds of a box turned by yaw. Wider than the box
// itself, so the live colour turns red a little early rather than a
// little late. The server runs the exact oriented test on release.
export function halfExtents(size, yawDeg) {
  const yaw = (yawDeg * Math.PI) / 180;
  const c = Math.abs(Math.cos(yaw));
  const s = Math.abs(Math.sin(yaw));
  return {
    hw: (size.width / 2) * c + (size.depth / 2) * s,
    hd: (size.width / 2) * s + (size.depth / 2) * c,
  };
}

function boxesOverlap(a, b) {
  return Math.abs(a.x - b.x) < a.hw + b.hw && Math.abs(a.z - b.z) < a.hd + b.hd;
}

export function priceLabel(price) {
  if (!price || !Number.isFinite(price.amount) || !/^[A-Z]{3}$/.test(price.currency ?? "")) return "";
  const formatter = new Intl.NumberFormat(undefined, {style: "currency", currency: price.currency});
  return formatter.format(price.amount / 10 ** formatter.resolvedOptions().maximumFractionDigits);
}

// Renders the central room plus Shopify products at their 6-DOF poses, each with a clickable price tag.
export class SceneViewer extends RoomViewer {
  constructor(container, onSelect, {onMoved, onSwap, onDragFeedback} = {}) {
    super(container);
    this.onSelect = onSelect;
    this.onMoved = onMoved;
    this.onSwap = onSwap;
    this.onDragFeedback = onDragFeedback;
    this.moveId = null;
    this.swapFrom = null;
    this.dragging = null;
    this.floorPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
    this.products = new Map();
    this.selectedId = null;
    this.raycaster = new THREE.Raycaster();
    const canvas = this.renderer.domElement;
    let down = null;
    canvas.addEventListener("pointerdown", (event) => {
      down = [event.clientX, event.clientY];
      if (this.moveId && this.pick(event) === this.moveId) {
        const {group} = this.products.get(this.moveId);
        this.dragging = {id: this.moveId, start: group.position.clone()};
        this.controls.enabled = false;
        canvas.setPointerCapture(event.pointerId);
      }
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!this.dragging) return;
      const point = this.floorPoint(event);
      if (!point) return;
      const {group, item} = this.products.get(this.dragging.id);
      const {x, z} = this.clampToRoom(point, item);
      group.position.set(x, this.dragging.start.y, z);
      this.paintDrag(this.dragging.id, this.liveConflict(this.dragging.id, {x, z}));
    });
    canvas.addEventListener("pointerup", (event) => {
      if (this.dragging) {
        const {id, start} = this.dragging;
        const {group} = this.products.get(id);
        this.dragging = null;
        this.controls.enabled = true;
        this.paintDrag(id, null);
        if (group.position.distanceTo(start) > CLICK_THRESHOLD_M) {
          this.onMoved?.(id, {x: group.position.x, z: group.position.z});
        } else {
          // Below the threshold it was a click. Put it back exactly, so a
          // shaky select never silently moves the product a centimetre.
          group.position.copy(start);
          this.select(id);
        }
        return;
      }
      if (!down || Math.hypot(event.clientX - down[0], event.clientY - down[1]) > 5) return;
      const id = this.pick(event);
      if (id) this.select(id);
    });
  }

  pointer(event) {
    const bounds = this.renderer.domElement.getBoundingClientRect();
    return new THREE.Vector2(
      ((event.clientX - bounds.left) / bounds.width) * 2 - 1, -((event.clientY - bounds.top) / bounds.height) * 2 + 1,
    );
  }

  pick(event) {
    this.raycaster.setFromCamera(this.pointer(event), this.camera);
    const groups = [...this.products.values()].map((entry) => entry.group);
    for (const hit of this.raycaster.intersectObjects(groups, true)) {
      let node = hit.object;
      while (node && !node.userData.productId) node = node.parent;
      if (node) return node.userData.productId;
    }
    return null;
  }

  floorPoint(event) {
    this.raycaster.setFromCamera(this.pointer(event), this.camera);
    return this.raycaster.ray.intersectPlane(this.floorPlane, new THREE.Vector3());
  }

  clampToRoom(point, item) {
    const {hw, hd} = halfExtents(item.size, item.pose.rotation.yaw);
    const width = this.scene?.dimensions.width.value_m ?? Infinity;
    const depth = this.scene?.dimensions.depth.value_m ?? Infinity;
    const snap = (value) => Math.round(value / SNAP_M) * SNAP_M;
    return {
      x: Math.min(Math.max(snap(point.x), hw), Math.max(hw, width - hw)),
      z: Math.min(Math.max(snap(point.z), hd), Math.max(hd, depth - hd)),
    };
  }

  // Instant local preview. The server runs the real test on release and
  // its answer is the one that counts; this only decides a colour.
  liveConflict(id, position) {
    const entry = this.products.get(id);
    if (!entry || !this.scene) return false;
    const {hw, hd} = halfExtents(entry.item.size, entry.item.pose.rotation.yaw);
    const moving = {x: position.x, z: position.z, hw, hd};

    for (const [otherId, other] of this.products) {
      if (otherId === id) continue;
      const bounds = halfExtents(other.item.size, other.item.pose.rotation.yaw);
      if (boxesOverlap(moving, {
        x: other.group.position.x, z: other.group.position.z, hw: bounds.hw, hd: bounds.hd,
      })) return true;
    }
    for (const item of this.scene.existing_items ?? []) {
      const bounds = halfExtents(item.size, item.pose.rotation.yaw);
      if (boxesOverlap(moving, {
        x: item.pose.position.x, z: item.pose.position.z, hw: bounds.hw, hd: bounds.hd,
      })) return true;
    }
    for (const zone of openingZones(this.scene)) {
      if (boxesOverlap(moving, {x: zone.x, z: zone.z, hw: zone.w / 2, hd: zone.d / 2})) return true;
    }
    return false;
  }

  // Tint in place rather than rebuilding the room, because rebuilding
  // drops the mesh the pointer is holding.
  paintDrag(id, blocked) {
    const entry = this.products.get(id);
    if (!entry) return;
    entry.group.traverse((node) => {
      if (!node.isMesh || !node.material || !("emissive" in node.material)) return;
      if (blocked === null) {
        node.material.emissive.setHex(0x000000);
        node.material.emissiveIntensity = 0;
        return;
      }
      node.material.emissive.setHex(blocked ? BLOCKED : CLEAR);
      node.material.emissiveIntensity = 0.55;
    });
    this.onDragFeedback?.(blocked);
  }

  setMoveMode(id) {
    this.moveId = id;
    this.renderer.domElement.style.cursor = id ? "grab" : "";
  }

  setSwapMode(id) {
    this.swapFrom = id;
    this.renderer.domElement.style.cursor = id ? "crosshair" : "";
  }

  showScene(scene, resolveModel, stateOf = () => "missing") {
    this.stateOf = stateOf;
    this.scene = scene;
    this.show({...scene, placed_products: []});
    this.products.clear();
    for (const item of scene.placed_products) {
      const group = this.ghostItem(item);
      for (const child of group.children.slice()) {
        if (child.isCSS2DObject) {
          child.element.remove();
          child.removeFromParent();
        }
      }
      group.userData.productId = item.id;
      group.add(this.priceTag(item));
      this.room.add(group);
      this.products.set(item.id, {group, item});
      const room = this.room;
      resolveModel(item).then((model) => {
        if (model && this.room === room) this.loadProduct(room, group, {...item, model});
      });
    }
    if (!this.products.has(this.selectedId)) this.selectedId = null;
    this.highlight();
  }

  priceTag(item) {
    const state = this.stateOf?.(item) ?? "missing";
    const standIn = STAND_IN_STATES.has(state);
    const tag = document.createElement("button");
    tag.type = "button";
    tag.className = "scene-label price-tag";
    tag.dataset.product = item.id;
    tag.dataset.variant = item.variant_id ?? "";
    tag.append(modelDot(state), document.createTextNode(priceLabel(item.price) || "—"));
    if (standIn) {
      const note = document.createElement("span");
      note.className = "tag-note";
      note.textContent = "stand-in";
      tag.append(note);
    }
    if (item.preview) {
      const note = document.createElement("span");
      note.className = "tag-note preview";
      note.textContent = "preview";
      tag.append(note);
    }
    tag.title = standIn
      ? `${item.label} · stand-in box at the listing size, not a 3D model of this product`
      : item.label;
    const spoken = [
      item.label,
      priceLabel(item.price) || "price unavailable",
      standIn ? "shown as a stand-in box at the listing size" : null,
      item.preview ? `preview only, sold by ${item.merchant ?? "another merchant"}` : null,
    ].filter(Boolean).join(", ");
    tag.setAttribute("aria-label", spoken);
    tag.addEventListener("click", () => this.select(item.id));
    const object = new CSS2DObject(tag);
    object.position.set(0, item.size.height / 2 + 0.12, 0);
    return object;
  }

  // Load a model that became available after the scene was drawn (for example one just built on click).
  attachModel(id, model) {
    const entry = this.products.get(id);
    if (entry && model) this.loadProduct(this.room, entry.group, {...entry.item, model});
  }

  select(id) {
    if (!this.products.has(id)) return;
    if (this.swapFrom) {
      const from = this.swapFrom;
      this.setSwapMode(null);
      if (from !== id) return this.onSwap?.(from, id);
    }
    this.selectedId = id;
    this.highlight();
    this.onSelect(this.products.get(id).item);
  }

  highlight() {
    for (const [id, {group, item}] of this.products) {
      group.getObjectByName("selection")?.removeFromParent();
      const tag = group.children.find((child) => child.isCSS2DObject);
      tag?.element.setAttribute("aria-pressed", String(id === this.selectedId));
      if (id !== this.selectedId) continue;
      const {width, depth, height} = item.size;
      const outline = new THREE.LineSegments(
        new THREE.EdgesGeometry(new THREE.BoxGeometry(width + 0.04, height + 0.04, depth + 0.04)),
        new THREE.LineBasicMaterial({color: SELECTED}),
      );
      outline.name = "selection";
      group.add(outline);
    }
  }
}
