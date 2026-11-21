import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

// Room frame (see backend/room.py): metres, Y-up, origin at the wall_n / wall_w floor corner,
// +x along wall_n toward wall_e, +z along wall_w toward wall_s.
const WALL_THICKNESS = 0.12;
const UP = new THREE.Vector3(0, 1, 0);
const DEG = Math.PI / 180;

function wallFrames(width, depth) {
  return {
    wall_n: {start: new THREE.Vector3(0, 0, 0), dir: new THREE.Vector3(1, 0, 0), out: new THREE.Vector3(0, 0, -1), length: width},
    wall_s: {start: new THREE.Vector3(0, 0, depth), dir: new THREE.Vector3(1, 0, 0), out: new THREE.Vector3(0, 0, 1), length: width},
    wall_w: {start: new THREE.Vector3(0, 0, 0), dir: new THREE.Vector3(0, 0, 1), out: new THREE.Vector3(-1, 0, 0), length: depth},
    wall_e: {start: new THREE.Vector3(width, 0, 0), dir: new THREE.Vector3(0, 0, 1), out: new THREE.Vector3(1, 0, 0), length: depth},
  };
}
// Facing a wall from inside the room, its "left" end is the low-offset end on wall_n and wall_e.
const LEFT_IS_START = {wall_n: true, wall_e: true, wall_s: false, wall_w: false};

function floorTexture(material, color) {
  const size = 256;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, size, size);
  let seed = 7;
  const random = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  if (material === "wood" || material === "laminate" || material === "vinyl") {
    for (let row = 0; row < 8; row += 1) {
      ctx.fillStyle = `rgba(0,0,0,${0.03 + random() * 0.07})`;
      ctx.fillRect(0, row * 32, size, 32);
      ctx.fillStyle = "rgba(0,0,0,0.22)";
      ctx.fillRect(0, row * 32, size, 1.5);
      ctx.fillRect((row % 2 ? 96 : 200) + random() * 30, row * 32, 1.5, 32);
      for (let grain = 0; grain < 6; grain += 1) {
        ctx.fillStyle = `rgba(255,255,255,${random() * 0.05})`;
        ctx.fillRect(0, row * 32 + random() * 32, size, 1);
      }
    }
  } else if (material === "tile" || material === "stone") {
    ctx.fillStyle = "rgba(0,0,0,0.18)";
    for (let i = 0; i <= size; i += 64) {
      ctx.fillRect(i - 1, 0, 2, size);
      ctx.fillRect(0, i - 1, size, 2);
    }
  } else {
    for (let i = 0; i < 9000; i += 1) {
      ctx.fillStyle = `rgba(${random() > 0.5 ? "255,255,255" : "0,0,0"},${random() * 0.07})`;
      ctx.fillRect(random() * size, random() * size, 2, 2);
    }
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  return texture;
}

function label(text, className) {
  const element = document.createElement("div");
  element.className = "scene-label " + (className ?? "");
  element.textContent = text;
  return new CSS2DObject(element);
}

function dispose(group) {
  group.traverse((node) => {
    node.geometry?.dispose();
    for (const material of [node.material ?? []].flat()) {
      material.map?.dispose();
      material.dispose();
    }
    if (node.isCSS2DObject) node.element.remove();
  });
  group.removeFromParent();
}

function fmt(metres) {
  return metres.toFixed(2) + " m";
}

export class RoomViewer {
  constructor(container) {
    this.container = container;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(50, 1, 0.05, 200);
    this.renderer = new THREE.WebGLRenderer({antialias: true, alpha: true});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    container.append(this.renderer.domElement);
    this.labels = new CSS2DRenderer();
    this.labels.domElement.className = "label-layer";
    container.append(this.labels.domElement);

    const pmrem = new THREE.PMREMGenerator(this.renderer);
    const environment = new RoomEnvironment();
    this.scene.environment = pmrem.fromScene(environment, 0.04).texture;
    this.scene.environmentIntensity = 0.45;
    environment.dispose();
    pmrem.dispose();
    this.scene.add(new THREE.HemisphereLight(0xfff8ee, 0x5b5f58, 1.4));
    this.sun = new THREE.DirectionalLight(0xfff1dc, 2.1);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    this.sun.shadow.normalBias = 0.02;
    this.scene.add(this.sun, this.sun.target);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.maxPolarAngle = Math.PI * 0.495;
    this.controls.screenSpacePanning = false;
    this.showLabels = true;
    this.showItems = false;
    this.loader = new GLTFLoader();
    this.walls = [];
    new ResizeObserver(() => this.resize()).observe(container);
    this.renderer.setAnimationLoop(() => this.frame());
  }

  resize() {
    const {clientWidth: width, clientHeight: height} = this.container;
    if (!width || !height) return;
    this.renderer.setSize(width, height, false);
    this.labels.setSize(width, height);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }

  frame() {
    this.controls.update();
    // Cut away any wall standing between the camera and the room so the interior stays visible.
    for (const wall of this.walls) {
      const toCamera = this.camera.position.clone().sub(wall.plane);
      wall.group.visible = toCamera.dot(wall.out) < 0.05;
    }
    this.renderer.render(this.scene, this.camera);
    this.labels.render(this.scene, this.camera);
  }

  show(room) {
    const resetCamera = room.room_id !== this.spec?.room_id ||
      ["width", "depth", "height"].some((axis) => room.dimensions[axis].value_m !== this.spec.dimensions[axis].value_m);
    if (this.room) dispose(this.room);
    this.spec = room;
    this.walls = [];
    const width = room.dimensions.width.value_m;
    const depth = room.dimensions.depth.value_m;
    const height = room.dimensions.height.value_m;
    const group = new THREE.Group();
    group.name = "room";
    this.room = group;
    this.scene.add(group);

    // Floor with a procedural finish repeated every metre.
    const texture = floorTexture(room.floor.material, "#ffffff");
    texture.repeat.set(width, depth);
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(width, depth),
      new THREE.MeshStandardMaterial({color: room.floor.color, map: texture, roughness: room.floor.material === "tile" ? 0.45 : 0.85}),
    );
    floor.rotation.x = -Math.PI / 2;
    floor.position.set(width / 2, 0, depth / 2);
    floor.receiveShadow = true;
    floor.name = "floor";
    group.add(floor);

    // Half-metre measuring grid clipped to the floor.
    const points = [];
    for (let x = 0; x <= width + 1e-6; x += 0.5) points.push(x, 0.002, 0, x, 0.002, depth);
    for (let z = 0; z <= depth + 1e-6; z += 0.5) points.push(0, 0.002, z, width, 0.002, z);
    const gridGeometry = new THREE.BufferGeometry();
    gridGeometry.setAttribute("position", new THREE.Float32BufferAttribute(points, 3));
    const grid = new THREE.LineSegments(gridGeometry, new THREE.LineBasicMaterial({color: 0x000000, transparent: true, opacity: 0.08}));
    this.grid = grid;
    group.add(grid);

    // Room outline at floor level stays visible when a wall is cut away.
    const outlineGeometry = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, 0.004, 0), new THREE.Vector3(width, 0.004, 0), new THREE.Vector3(width, 0.004, depth),
      new THREE.Vector3(0, 0.004, depth), new THREE.Vector3(0, 0.004, 0),
    ]);
    group.add(new THREE.Line(outlineGeometry, new THREE.LineBasicMaterial({color: 0xb8eb9c})));

    const frames = wallFrames(width, depth);
    for (const wall of room.walls) {
      const frame = frames[wall.id];
      const openings = room.openings.filter((opening) => opening.wall === wall.id).sort((a, b) => a.offset_m - b.offset_m);
      group.add(this.buildWall(wall, frame, openings, height));
      for (const opening of openings) group.add(this.buildOpening(opening, frame, height));
    }

    this.itemGroup = new THREE.Group();
    this.itemGroup.name = "detected-items";
    for (const item of room.existing_items) {
      const preview = this.ghostItem(item);
      if (item.fixed) group.add(preview);
      else this.itemGroup.add(preview);
    }
    group.add(this.itemGroup);
    this.setItems(this.showItems);
    for (const item of room.placed_products) {
      const preview = this.ghostItem(item);
      group.add(preview);
      this.loadProduct(group, preview, item);
    }

    const span = Math.max(width, depth);
    const window = room.lighting.windows.reduce((largest, entry) =>
      !largest || entry.area_m2 > largest.area_m2 ? entry : largest, null);
    if (window) {
      const {x, y, z} = window.center;
      this.sun.position.set(x, y, z).addScaledVector(frames[window.wall].out, 0.6);
      this.sun.intensity = Math.min(2.1, 0.4 + 10 * room.lighting.window_area_m2 / (width * depth));
    } else {
      this.sun.position.set(width * 0.2, height * 3, depth * 1.4);
      this.sun.intensity = 0.4;
    }
    this.sun.target.position.set(width / 2, 0, depth / 2);
    Object.assign(this.sun.shadow.camera, {left: -span, right: span, top: span, bottom: -span, near: 0.1, far: span * 6});
    this.sun.shadow.camera.updateProjectionMatrix();
    this.setLabels(this.showLabels);
    if (resetCamera) this.view(this.lastView ?? "overview");
  }

  buildWall(wall, frame, openings, height) {
    const group = new THREE.Group();
    group.name = wall.id;
    const material = new THREE.MeshStandardMaterial({color: wall.color, roughness: 0.92, side: THREE.DoubleSide});
    // wall_n and wall_s run past the corners so the side walls close against them.
    const extend = frame.dir.x ? WALL_THICKNESS : 0;
    const addBox = (u0, u1, v0, v1) => {
      if (u1 - u0 < 0.005 || v1 - v0 < 0.005) return;
      const length = u1 - u0;
      const geometry = frame.dir.x
        ? new THREE.BoxGeometry(length, v1 - v0, WALL_THICKNESS)
        : new THREE.BoxGeometry(WALL_THICKNESS, v1 - v0, length);
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.copy(frame.start)
        .addScaledVector(frame.dir, (u0 + u1) / 2)
        .addScaledVector(UP, (v0 + v1) / 2)
        .addScaledVector(frame.out, WALL_THICKNESS / 2);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      group.add(mesh);
    };
    let cursor = -extend;
    for (const opening of openings) {
      const start = opening.offset_m;
      const end = start + opening.width_m;
      addBox(cursor, start, 0, height);
      addBox(start, end, 0, opening.sill_m);
      addBox(start, end, opening.sill_m + opening.height_m, height);
      cursor = end;
    }
    addBox(cursor, frame.length + extend, 0, height);

    const tag = label(
      `${wall.id.replace("wall_", "Wall ").toUpperCase()} · ${fmt(wall.length_m)}`,
      "wall-label",
    );
    tag.position.copy(frame.start).addScaledVector(frame.dir, frame.length / 2).addScaledVector(frame.out, 0.35);
    tag.position.y = 0.02;
    const holder = new THREE.Group();
    holder.add(group, tag);
    this.walls.push({group, out: frame.out, plane: frame.start.clone()});
    return holder;
  }

  buildOpening(opening, frame, height) {
    const group = new THREE.Group();
    group.name = opening.id;
    const startAtLeft = LEFT_IS_START[opening.wall];
    const center = frame.start.clone()
      .addScaledVector(frame.dir, opening.offset_m + opening.width_m / 2)
      .addScaledVector(UP, opening.sill_m + opening.height_m / 2)
      .addScaledVector(frame.out, WALL_THICKNESS / 2);

    if (opening.type === "window") {
      const glass = new THREE.Mesh(
        frame.dir.x ? new THREE.BoxGeometry(opening.width_m, opening.height_m, 0.01) : new THREE.BoxGeometry(0.01, opening.height_m, opening.width_m),
        new THREE.MeshStandardMaterial({
          color: 0xdcefff, emissive: 0xbfdcf5, emissiveIntensity: 0.55, transparent: true, opacity: 0.55, roughness: 0.05,
        }),
      );
      glass.position.copy(center);
      const frameBox = new THREE.LineSegments(
        new THREE.EdgesGeometry(frame.dir.x
          ? new THREE.BoxGeometry(opening.width_m, opening.height_m, WALL_THICKNESS)
          : new THREE.BoxGeometry(WALL_THICKNESS, opening.height_m, opening.width_m)),
        new THREE.LineBasicMaterial({color: 0xf3f1ea}),
      );
      frameBox.position.copy(center);
      group.add(glass, frameBox);
    }

    if (opening.type === "door") {
      const hingeAtStart = opening.hinge === "left" ? startAtLeft : !startAtLeft;
      const hinge = frame.start.clone().addScaledVector(frame.dir, opening.offset_m + (hingeAtStart ? 0 : opening.width_m));
      const closed = frame.dir.clone().multiplyScalar(hingeAtStart ? 1 : -1);
      const swingSide = opening.swing === "outward" ? frame.out.clone() : frame.out.clone().negate();
      if (opening.swing === "outward") hinge.addScaledVector(frame.out, WALL_THICKNESS);
      const swings = opening.swing === "inward" || opening.swing === "outward";
      const angle = swings ? 32 * DEG : 0;
      const openDir = closed.clone().multiplyScalar(Math.cos(angle)).addScaledVector(swingSide, Math.sin(angle));
      const leaf = new THREE.Mesh(
        new THREE.BoxGeometry(opening.width_m - 0.02, opening.height_m - 0.01, 0.04),
        new THREE.MeshStandardMaterial({color: 0xf1ede4, roughness: 0.6}),
      );
      leaf.position.copy(hinge).addScaledVector(openDir, opening.width_m / 2).addScaledVector(UP, opening.height_m / 2);
      if (!swings) leaf.position.addScaledVector(frame.out, WALL_THICKNESS / 2);
      leaf.rotation.y = Math.atan2(-openDir.z, openDir.x);
      leaf.castShadow = true;
      group.add(leaf);
      if (swings) {
        // The swept floor area: a quarter circle from the closed leaf to fully open.
        const a0 = Math.atan2(-closed.z, closed.x);
        const a1 = Math.atan2(-swingSide.z, swingSide.x);
        let delta = a1 - a0;
        delta = Math.atan2(Math.sin(delta), Math.cos(delta));
        const arc = new THREE.Mesh(
          new THREE.CircleGeometry(opening.width_m, 40, delta > 0 ? a0 : a1, Math.PI / 2),
          new THREE.MeshBasicMaterial({color: 0xffb86b, transparent: true, opacity: 0.22, depthWrite: false, side: THREE.DoubleSide}),
        );
        arc.rotation.x = -Math.PI / 2;
        arc.position.copy(hinge).setY(0.006);
        arc.name = opening.id + "-swing";
        group.add(arc);
      }
    }

    const tag = label(`${opening.id.replace("_", " ")} · ${fmt(opening.width_m)}`, "opening-label");
    tag.position.copy(center).addScaledVector(UP, opening.height_m / 2 + 0.12).addScaledVector(frame.out, -0.2);
    group.add(tag);
    return group;
  }

  ghostItem(item) {
    const {width, depth, height} = item.size;
    const box = new THREE.BoxGeometry(width, height, depth);
    const group = new THREE.Group();
    group.name = item.id;
    const color = item.fixed ? 0xdfaa76 : 0x9ad1ff;
    const mesh = new THREE.Mesh(box, new THREE.MeshBasicMaterial({color, transparent: true, opacity: 0.16, depthWrite: false}));
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(box), new THREE.LineBasicMaterial({color}));
    group.add(mesh, edges);
    // 6-DOF pose: position is the base centre; yaw, pitch, roll apply in that order.
    const {position, rotation} = item.pose;
    group.position.set(position.x, position.y + height / 2, position.z);
    group.rotation.set(rotation.pitch * DEG, rotation.yaw * DEG, rotation.roll * DEG, "YXZ");
    const tag = label(item.label, "item-label");
    tag.position.set(0, height / 2 + 0.1, 0);
    group.add(tag);
    return group;
  }

  async loadProduct(roomGroup, preview, item) {
    try {
      const model = item.model;
      const gltf = model.url ? await this.loader.loadAsync(model.url) : await this.loader.parseAsync(
        Uint8Array.from(atob(model.data_base64), (char) => char.charCodeAt(0)).buffer, "",
      );
      const asset = gltf.scene;
      const box = new THREE.Box3().setFromObject(asset);
      const extent = box.getSize(new THREE.Vector3());
      if (this.room !== roomGroup || box.isEmpty() || extent.x <= 0 || extent.y <= 0 || extent.z <= 0) {
        dispose(asset);
        return;
      }
      const center = box.getCenter(new THREE.Vector3());
      const scale = new THREE.Vector3(item.size.width / extent.x, item.size.height / extent.y, item.size.depth / extent.z);
      const normalized = new THREE.Group();
      normalized.scale.copy(scale);
      asset.position.sub(center);
      asset.traverse((node) => {
        if (node.isMesh) {
          node.castShadow = true;
          node.receiveShadow = true;
        }
      });
      normalized.add(asset);
      for (const child of preview.children.slice()) {
        if (child.isMesh || child.isLineSegments) dispose(child);
      }
      preview.add(normalized);
    } catch {
      // Leave the correctly sized room footprint in view if a model cannot load.
    }
  }

  setLabels(visible) {
    this.showLabels = visible;
    this.labels.domElement.hidden = !visible;
  }

  setItems(visible) {
    this.showItems = visible;
    if (!this.itemGroup) return;
    this.itemGroup.visible = visible;
    // CSS2D labels ignore a hidden parent, so toggle them directly.
    this.itemGroup.traverse((node) => {
      if (node.isCSS2DObject) node.visible = visible;
    });
  }

  view(name) {
    if (!this.spec) return;
    this.lastView = name;
    const width = this.spec.dimensions.width.value_m;
    const depth = this.spec.dimensions.depth.value_m;
    const height = this.spec.dimensions.height.value_m;
    const span = Math.max(width, depth);
    const target = new THREE.Vector3(width / 2, height * 0.3, depth / 2);
    if (name === "plan") {
      target.y = 0;
      this.camera.position.set(width / 2, span * 1.75 + height, depth / 2 + 0.001);
    } else if (name === "eye") {
      // Standing just inside wall_s, where the first photo was taken, looking toward wall_n.
      target.set(width / 2, 1.3, 0);
      this.camera.position.set(width / 2, 1.6, Math.max(depth - 0.4, depth * 0.8));
    } else {
      this.camera.position.set(width * 1.25, height + span * 0.95, depth + span * 0.95);
    }
    this.controls.target.copy(target);
    this.controls.minDistance = 0.3;
    this.controls.maxDistance = span * 5;
    this.controls.update();
  }
}
