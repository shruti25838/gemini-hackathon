import * as THREE from "three";
import { CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { RoomViewer } from "./room-viewer.js";

const SELECTED = 0xb8eb9c;

const DOT_TITLES = {
  cached: "3D model cached",
  missing: "No 3D model yet. Click to build it from the Shopify listing",
  building: "Building the 3D model from the Shopify listing…",
  failed: "3D model could not be built. Click to retry",
};

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

export function priceLabel(price) {
  if (!price || !Number.isFinite(price.amount) || !/^[A-Z]{3}$/.test(price.currency ?? "")) return "";
  const formatter = new Intl.NumberFormat(undefined, {style: "currency", currency: price.currency});
  return formatter.format(price.amount / 10 ** formatter.resolvedOptions().maximumFractionDigits);
}

// Renders the central room plus Shopify products at their 6-DOF poses, each with a clickable price tag.
export class SceneViewer extends RoomViewer {
  constructor(container, onSelect, {onMoved, onSwap} = {}) {
    super(container);
    this.onSelect = onSelect;
    this.onMoved = onMoved;
    this.onSwap = onSwap;
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
      const picked = this.moveId || this.dragAny ? this.pick(event) : null;
      if (picked && (this.dragAny || picked === this.moveId)) {
        const {group} = this.products.get(picked);
        this.dragging = {id: picked, start: group.position.clone()};
        this.controls.enabled = false;
        canvas.setPointerCapture(event.pointerId);
      }
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!this.dragging) return;
      const point = this.floorPoint(event);
      if (point) this.products.get(this.dragging.id).group.position.set(point.x, this.dragging.start.y, point.z);
    });
    canvas.addEventListener("pointerup", (event) => {
      if (this.dragging) {
        const {id, start} = this.dragging;
        const {group} = this.products.get(id);
        this.dragging = null;
        this.controls.enabled = true;
        if (group.position.distanceTo(start) > 0.01) this.onMoved?.(id, {x: group.position.x, z: group.position.z});
        else if (this.dragAny) this.select(id);  // a click without movement still selects
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
    const tag = document.createElement("button");
    tag.type = "button";
    tag.className = "scene-label price-tag";
    tag.dataset.product = item.id;
    tag.dataset.variant = item.variant_id ?? "";
    tag.append(modelDot(this.stateOf?.(item) ?? "missing"), document.createTextNode(priceLabel(item.price) || "—"));
    tag.title = item.label;
    tag.setAttribute("aria-label", `${item.label}, ${priceLabel(item.price) || "price unavailable"}`);
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
