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

// Viewer-only dressing, below. None of it is in the scene file, none of it
// is posted back, and the fit engine never sees it. It is how the room is
// drawn, not what the room is. A test pins that, because a sky plane that
// quietly became a scene object would be a window on to somewhere that
// does not exist, recorded as if a shopper had put it there.
//
// The ceiling is one plain colour rather than a sampled one for the same
// reason: nothing has measured a ceiling, so nothing may claim to have.
const CEILING_COLOR = 0xf2efe7;
// Where the sky sits and how big it is.
//
// SET_BACK is how far past the wall's outer face it stands: barely clear,
// so there is no gap for the eye to find around its edge, and so it cannot
// be caught from outside.
//
// MARGIN is how far it reaches past the windows on the wall, vertically.
// HEADROOM is the strip of wall that must be left above it, and it is the
// one that was learned the hard way. The first sky was as tall as the wall
// and set well back. From inside it looked right; from above it did not.
// Plan and Overview both look over the top of the wall, and anything
// standing behind that wall above the sight line shows as a blue band along
// the roofline. Keeping the sky under the wall top removes the band.
//
// Sideways there is nothing to leak through, because the sky stops exactly
// where the wall stops, so it simply runs the whole length of it. That also
// means two windows on one wall share a single sky, instead of putting two
// planes in the same place to flicker between.
const SKY_SET_BACK_M = 0.02;
const SKY_MARGIN_M = 0.25;
const SKY_HEADROOM_M = 0.18;
// The patch of light on the floor inside a window, and how warm it is.
const SUN_PATCH_DEPTH_M = 1.35;
const SUN_PATCH_SPREAD_M = 0.5;

// Eye-level vantages. x and z are fractions of the room's width and depth
// so a vantage travels to a room of another size; y is a real height in
// metres, because eye level is eye level whatever the room measures.
// Three of them because one guess framed the first hero badly: the sofa
// was cropped at a corner and the shelves were jammed into the right
// edge. ?eye=N picks one, and 0 is what the Eye level button uses.
//
// All three stand in the south-east quarter and look north-west, aimed so
// the frame edge falls clear of whatever stands on wall_s behind the
// shooter. The first set of three did not do that and each one caught the
// door leaf or the back of a bookcase as an unlit slab down one side.
const EYE_VIEWS = [
  // 0 - the whole room readable: sofa left, window centre, shelves right.
  //     Aimed a few degrees further round than feels natural, because the
  //     bottom corner of a frame reaches wider than its half-angle and an
  //     inward-swinging door behind the shooter kept creeping into it.
  {fov: 63, eye: [0.8950, 1.60, 0.75625], look: [0.5125, 1.00, 0.15625]},
  // 1 - a step closer and a little lower, so the seating fills more frame.
  {fov: 68, eye: [0.8625, 1.55, 0.7188], look: [0.4375, 0.95, 0.1563]},
  // 2 - further back and narrower, more floor and more ceiling.
  {fov: 62, eye: [0.9125, 1.65, 0.8438], look: [0.4750, 1.05, 0.1875]},
];
// Plan and Overview keep the lens they have always had. Widening the lens
// for the hero must not quietly re-frame the two views people work in.
const PLAN_FOV = 50;

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

/** A painted vertical gradient that reads as a bright day outside.
 *
 * Canvas, not a photograph. Nothing here is a picture of anywhere, which
 * matters because every other image in this app is a merchant's: sky blue
 * at the top, haze at the horizon, a warm band below it. A window needs
 * something behind it or it is a hole on to the page background, and a hole
 * on to a dark page is the flat grey panel the first hero shot showed.
 */
function skyTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = 8;
  canvas.height = 256;
  const ctx = canvas.getContext("2d");
  const gradient = ctx.createLinearGradient(0, 0, 0, 256);
  gradient.addColorStop(0.00, "#5e9ede");
  gradient.addColorStop(0.34, "#a6d0f4");
  gradient.addColorStop(0.58, "#e4f0fb");
  gradient.addColorStop(0.68, "#f8f2e0");
  gradient.addColorStop(0.80, "#d8e3c6");
  gradient.addColorStop(1.00, "#b9c9a6");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, 8, 256);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/** A soft round falloff, used as the shape of the light on the floor. */
function glowTexture() {
  const size = 128;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  gradient.addColorStop(0.00, "rgba(255,255,255,0.95)");
  gradient.addColorStop(0.40, "rgba(255,255,255,0.42)");
  gradient.addColorStop(1.00, "rgba(255,255,255,0)");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, size, size);
  return new THREE.CanvasTexture(canvas);
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
    // Three lights, and the balance between them is the whole point. The
    // room used to be lit almost entirely by the environment and a strong
    // hemisphere, which lights every face of every object about equally:
    // correct exposure, no shading, and furniture that reads as cut paper.
    // Now the warm key off the window side does most of the work, a cool
    // sky fill keeps the shadow sides from going black, and the environment
    // is pulled back so the key is what shapes the surfaces.
    this.scene.environmentIntensity = 0.30;
    environment.dispose();
    pmrem.dispose();
    this.scene.add(new THREE.HemisphereLight(0xdcebff, 0x6b6a5e, 0.72));
    this.sun = new THREE.DirectionalLight(0xffe6bd, 2.1);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    this.sun.shadow.normalBias = 0.02;
    // Cool bounce from the far side, aimed at the same point as the key and
    // casting nothing, so it fills without inventing a second set of shadows.
    this.fill = new THREE.DirectionalLight(0xc9dcf2, 0.42);
    this.fill.target = this.sun.target;
    this.scene.add(this.sun, this.sun.target, this.fill);

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

    // A plain ceiling, drawn only from below. The plane is single sided with
    // its normal pointing down, so from anywhere above the room there is
    // simply nothing there to draw: Plan looks straight through it and
    // Overview looks over the wall tops exactly as before. No view has to
    // remember to switch it off, which is the only version of this that
    // cannot be got wrong later.
    const ceiling = new THREE.Mesh(
      new THREE.PlaneGeometry(width + WALL_THICKNESS * 2, depth + WALL_THICKNESS * 2),
      // The emissive term stands in for the light a real ceiling gets back
      // off the floor, which nothing here simulates. Without it the ceiling
      // faces straight down into the hemisphere's ground colour and reads as
      // a dirty grey lid over a bright room.
      new THREE.MeshStandardMaterial({
        color: CEILING_COLOR, emissive: CEILING_COLOR, emissiveIntensity: 0.30,
        roughness: 0.96, side: THREE.FrontSide,
      }),
    );
    ceiling.rotation.x = Math.PI / 2;
    ceiling.position.set(width / 2, height, depth / 2);
    ceiling.castShadow = false;
    ceiling.receiveShadow = true;
    ceiling.name = "ceiling";
    group.add(ceiling);

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
      if (openings.some((opening) => opening.type === "window")) {
        group.add(this.daylight(wall, frame, openings, height));
      }
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
      // Stand the key well outside the window and above the head of it, so
      // the light rakes across the room instead of dropping on to it. A
      // directional light only takes its direction from this, not its
      // distance, and a raking direction is what gives the floor and the
      // seat fronts a long readable shadow.
      const {x, y, z} = window.center;
      this.sun.position.set(x, y + 1.15, z).addScaledVector(frames[window.wall].out, 2.6);
      this.sun.intensity = Math.min(2.6, 0.5 + 11 * room.lighting.window_area_m2 / (width * depth));
    } else {
      this.sun.position.set(width * 0.2, height * 3, depth * 1.4);
      this.sun.intensity = 0.4;
    }
    this.sun.target.position.set(width / 2, 0, depth / 2);
    // The fill is the key reflected through the room centre, lifted so it
    // comes from slightly above rather than up off the floor.
    this.fill.position.copy(this.sun.target.position).multiplyScalar(2).sub(this.sun.position);
    this.fill.position.y = height * 1.2;
    Object.assign(this.sun.shadow.camera,
      {left: -span * 1.2, right: span * 1.2, top: span * 1.2, bottom: -span * 1.2, near: 0.1, far: span * 8});
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
        // Mostly clear, now that there is a sky behind it. At the old 0.55 the
        // pane was a pale blue film over whatever was beyond, which with
        // nothing beyond it was the grey slab the first hero shot showed. The
        // small emissive keeps a sheen on the glass so it is still glass.
        new THREE.MeshStandardMaterial({
          color: 0xeaf6ff, emissive: 0xcfe6fa, emissiveIntensity: 0.20, transparent: true,
          opacity: 0.14, roughness: 0.04, depthWrite: false,
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

  /** Daylight for one wall: a sky behind each window, light on the floor.
   *
   * Viewer only. Nothing here is written to the scene file, posted to the
   * server or handed to the fit engine, and nothing here is an obstacle:
   * the floor patch has depthWrite off and is on no hit layer, so it is
   * neither raycast nor dragged into, and the sky is outside the room
   * altogether. A window with a sky behind it is still a window; it has not
   * become a view of a real place, and the gradient is painted, not
   * photographed.
   *
   * Sizing is the whole trick. The sky runs the full length of the wall
   * but stops short of its top, and sits barely past its outer face. Within
   * the wall's own outline and below its top edge, it is covered by that
   * wall from everywhere outside the room, including from above, so Plan
   * and Overview see nothing but the room they always saw.
   *
   * A window looked at from far enough to the side shows its reveal rather
   * than its glass, and no sky changes that. That is the opening's real
   * shape, not something missing.
   */
  daylight(wall, frame, openings, height) {
    const group = new THREE.Group();
    group.name = "daylight_" + wall.id;
    const alongX = Math.abs(frame.dir.x) > 0.5;
    const windows = openings.filter((opening) => opening.type === "window");
    const extend = frame.dir.x ? WALL_THICKNESS : 0;

    // One sky for the wall, not one per window, so two windows on the same
    // wall cannot put two coplanar planes in the same place and flicker
    // between them. It is as long as the wall and as tall as it can be
    // without reaching the wall top.
    const lowest = Math.min(...windows.map((opening) => opening.sill_m));
    const highest = Math.max(...windows.map((opening) => opening.sill_m + opening.height_m));
    const top = Math.max(highest, Math.min(highest + SKY_MARGIN_M, height - SKY_HEADROOM_M));
    const bottom = Math.min(lowest, Math.max(lowest - SKY_MARGIN_M, 0));
    const sky = new THREE.Mesh(
      new THREE.PlaneGeometry(frame.length + extend * 2, top - bottom),
      new THREE.MeshBasicMaterial({map: skyTexture(), side: THREE.FrontSide, toneMapped: false}),
    );
    sky.name = "sky_" + wall.id;
    sky.position.copy(frame.start)
      .addScaledVector(frame.dir, frame.length / 2)
      .addScaledVector(UP, (top + bottom) / 2)
      .addScaledVector(frame.out, WALL_THICKNESS + SKY_SET_BACK_M);
    sky.lookAt(sky.position.clone().addScaledVector(frame.out, -1));
    group.add(sky);

    for (const opening of windows) {
      const across = opening.width_m + SUN_PATCH_SPREAD_M;
      const patch = new THREE.Mesh(
        new THREE.PlaneGeometry(alongX ? across : SUN_PATCH_DEPTH_M, alongX ? SUN_PATCH_DEPTH_M : across),
        new THREE.MeshBasicMaterial({
          map: glowTexture(), color: 0xffeccd, transparent: true, opacity: 0.55,
          blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false,
        }),
      );
      patch.name = opening.id + "-daylight";
      patch.rotation.x = -Math.PI / 2;
      patch.position.copy(frame.start)
        .addScaledVector(frame.dir, opening.offset_m + opening.width_m / 2)
        .addScaledVector(frame.out, -SUN_PATCH_DEPTH_M / 2);
      patch.position.y = 0.008;
      group.add(patch);
    }
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
        // Everything the stand-in drew is replaced by the real model,
        // except the invisible solid that makes the product grabbable.
        // Disposing that left a loaded model with nothing to press on.
        if (child.userData.hitVolume) continue;
        if (child.isMesh || child.isLineSegments) dispose(child);
      }
      preview.add(normalized);
    } catch {
      // Leave the correctly sized room footprint in view if a model cannot load.
    }
  }

  /** Recolour what is already drawn, without rebuilding the room.
   *
   * The wheel calls this on every pointer move so the room follows the
   * thumb. Rebuilding from the scene data would mean a server round trip
   * and a dropped frame per pixel, and would reset the camera on a room
   * whose dimensions changed. The authoritative colour still arrives the
   * normal way when the edit is saved and the scene re-renders.
   */
  previewRoomColor(target, hex) {
    if (!this.room) return;
    const paint = (node) => {
      if (node.isMesh && node.material?.color) node.material.color.set(hex);
    };
    if (target === "floor") {
      const floor = this.room.getObjectByName("floor");
      if (floor) paint(floor);
      return;
    }
    // By wall group, so the door leaf and the window glass keep their own
    // colours instead of turning into painted wall.
    for (const id of ["wall_n", "wall_e", "wall_s", "wall_w"]) {
      this.room.getObjectByName(id)?.traverse(paint);
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

  view(name, candidate = this.eyeCandidate ?? 0) {
    if (!this.spec) return;
    this.lastView = name;
    this.eyeCandidate = candidate;
    const width = this.spec.dimensions.width.value_m;
    const depth = this.spec.dimensions.depth.value_m;
    const height = this.spec.dimensions.height.value_m;
    const span = Math.max(width, depth);
    const target = new THREE.Vector3(width / 2, height * 0.3, depth / 2);
    let fov = PLAN_FOV;
    if (name === "plan") {
      target.y = 0;
      this.camera.position.set(width / 2, span * 1.75 + height, depth / 2 + 0.001);
    } else if (name === "eye") {
      // Standing in the room rather than flat against wall_s. The old vantage
      // was dead centre on the south wall, which put whatever stood along the
      // side walls into the two bottom corners and cropped it.
      const chosen = EYE_VIEWS[candidate] ?? EYE_VIEWS[0];
      fov = chosen.fov;
      const eyeHeight = Math.min(chosen.eye[1], height - 0.25);
      target.set(chosen.look[0] * width, Math.min(chosen.look[1], height - 0.4), chosen.look[2] * depth);
      this.camera.position.set(chosen.eye[0] * width, eyeHeight, chosen.eye[2] * depth);
    } else {
      this.camera.position.set(width * 1.25, height + span * 0.95, depth + span * 0.95);
    }
    // Only the eye-level lens is wide. Plan and Overview keep PLAN_FOV, so
    // the two views people actually work in frame what they always framed.
    if (this.camera.fov !== fov) {
      this.camera.fov = fov;
      this.camera.updateProjectionMatrix();
    }
    this.controls.target.copy(target);
    this.controls.minDistance = 0.3;
    this.controls.maxDistance = span * 5;
    this.controls.update();
  }
}
