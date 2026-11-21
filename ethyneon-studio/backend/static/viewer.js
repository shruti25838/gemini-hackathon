import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

const UP = new THREE.Vector3(0, 1, 0);
const SEATING = /seat|back.?rest|upholster|fabric|leather|mesh_panel/i;

function release(group) {
  const materials = new Set();
  const textures = new Set();
  group.traverse((node) => {
    if (!node.isMesh) return;
    node.geometry.dispose();
    for (const material of [node.material].flat()) {
      materials.add(material);
      for (const value of Object.values(material)) {
        if (value?.isTexture) textures.add(value);
      }
    }
  });
  for (const material of materials) material.dispose();
  for (const texture of textures) texture.dispose();
  group.removeFromParent();
}

function namedBounds(group, pattern) {
  const box = new THREE.Box3();
  group.traverse((node) => {
    if (node.isMesh && pattern.test(node.name)) box.expandByObject(node);
  });
  return box.isEmpty() ? null : box;
}

export class ProductViewer {
  constructor(container, spinChanged) {
    this.container = container;
    this.spinChanged = spinChanged;
    this.loader = new GLTFLoader();
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(36, 1, 0.001, 500);
    this.renderer = new THREE.WebGLRenderer({antialias: true, alpha: true});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.15;
    this.renderer.shadowMap.enabled = true;
    container.append(this.renderer.domElement);

    const environment = new RoomEnvironment();
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(environment, 0.04).texture;
    this.scene.environmentIntensity = 0.65;
    environment.dispose();
    pmrem.dispose();

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.enablePan = false;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.controls.autoRotateSpeed = 1;
    this.controls.addEventListener("change", () => { this.dirty = true; });
    this.controls.addEventListener("start", () => this.setSpin(false));
    this.anglesLeft = 0;
    this.lastFrame = performance.now();

    this.scene.add(new THREE.HemisphereLight(0xe9fff3, 0x697167, 1.55));
    this.key = new THREE.DirectionalLight(0xfff2da, 2.5);
    this.key.castShadow = true;
    this.key.shadow.mapSize.set(1024, 1024);
    this.key.shadow.normalBias = 0.005;
    this.scene.add(this.key);
    const rim = new THREE.DirectionalLight(0xb9dfd2, 1.2);
    rim.position.set(-2, 3, -4);
    this.scene.add(rim);

    this.floor = new THREE.Mesh(
      new THREE.PlaneGeometry(2, 2), new THREE.ShadowMaterial({opacity: 0.35}),
    );
    this.floor.receiveShadow = true;
    this.floor.rotation.x = -Math.PI / 2;
    this.floor.position.y = -0.005;
    this.scene.add(this.floor);
    this.grid = new THREE.GridHelper(2.6, 16, 0x5d7564, 0x486051);
    this.grid.position.y = -0.009;
    this.grid.material.transparent = true;
    this.grid.material.opacity = 0.35;
    this.scene.add(this.grid);

    this.reset();
    new ResizeObserver(() => this.resize()).observe(container);
    this.renderer.setAnimationLoop((time) => this.frame(time));
  }

  resize() {
    const {clientWidth: width, clientHeight: height} = this.container;
    if (!width || !height) return;
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.dirty = true;
  }

  reset() {
    const size = this.product?.size ?? new THREE.Vector3(1, 1, 1);
    const span = Math.max(size.x, size.y, size.z);
    this.anglesLeft = 0;
    this.controls.target.set(0, this.product ? size.y * 0.5 : 0, 0);
    this.camera.position.copy(this.controls.target).addScaledVector(new THREE.Vector3(1.37, 0.92, 2.05), span);
    this.camera.near = span * 0.005;
    this.camera.far = span * 35;
    this.camera.updateProjectionMatrix();
    this.controls.minDistance = span * 0.75;
    this.controls.maxDistance = span * 8;
    this.controls.update();
    this.floor.scale.setScalar(span * 3);
    this.grid.scale.setScalar(span * 1.6);
    this.key.position.set(span * 2, span * 3.5, span * 4);
    Object.assign(this.key.shadow.camera, {
      left: -span * 1.5, right: span * 1.5, top: span * 1.5, bottom: -span * 1.5,
      far: span * 10,
    });
    this.key.shadow.camera.updateProjectionMatrix();
    this.dirty = true;
  }

  setSpin(spin) {
    this.controls.autoRotate = spin;
    this.anglesLeft = 0;
    this.spinChanged(spin);
  }

  rotate(degrees) {
    this.setSpin(false);
    this.anglesLeft = THREE.MathUtils.degToRad(-degrees);
  }

  frame(time) {
    const dt = Math.min((time - this.lastFrame) / 1000, 0.05);
    this.lastFrame = time;
    if (Math.abs(this.anglesLeft) > 0.001) {
      const angle = this.anglesLeft * (1 - Math.exp(-8 * dt));
      this.anglesLeft -= angle;
      this.camera.position.sub(this.controls.target).applyAxisAngle(UP, angle).add(this.controls.target);
      this.dirty = true;
    }
    this.controls.update(dt);
    if (this.dirty) {
      this.renderer.render(this.scene, this.camera);
      this.dirty = false;
    }
  }

  async load(product) {
    const asset = product.model;
    let gltf;
    if (asset.url) {
      gltf = await this.loader.loadAsync(asset.url);
    } else {
      const bytes = Uint8Array.from(atob(asset.data_base64), (character) => character.charCodeAt(0));
      gltf = await this.loader.parseAsync(bytes.buffer, "");
    }
    const node = gltf.scene;
    const bounds = new THREE.Box3().setFromObject(node);
    const size = bounds.getSize(new THREE.Vector3());
    if (bounds.isEmpty() || ![size.x, size.y, size.z].every((axis) => Number.isFinite(axis) && axis > 0)) {
      release(node);
      throw new Error("This Shopify asset has no usable 3D geometry.");
    }
    const center = bounds.getCenter(new THREE.Vector3());
    node.position.sub(new THREE.Vector3(center.x, bounds.min.y, center.z));
    node.traverse((mesh) => {
      if (mesh.isMesh) {
        mesh.castShadow = true;
        mesh.receiveShadow = true;
      }
    });
    const group = new THREE.Group();
    group.add(node);
    return {group, size, dimensions: product.dimensions_m};
  }

  async show(product) {
    const candidate = await this.load(product);
    if (this.product) release(this.product.group);
    this.product = candidate;
    this.headrest = null;
    this.scene.add(candidate.group);
    this.reset();
    this.setSpin(!matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  setColor(hex) {
    const editable = new Set();
    const surfaces = new Set();
    const all = new Set();
    this.product.group.children[0].traverse((node) => {
      if (!node.isMesh) return;
      for (const material of [node.material].flat()) {
        if (!material.color) continue;
        all.add(material);
        if (material.name.startsWith("finish_")) editable.add(material);
        if (SEATING.test(node.name + material.name)) surfaces.add(material);
      }
    });
    const targets = editable.size ? editable : surfaces.size ? surfaces : all;
    for (const material of targets) material.color.set(hex);
    this.dirty = true;
  }

  async attachHeadrest(product) {
    const parent = this.product;
    const accessory = await this.load(product);
    const body = parent.group.children[0];
    const builtin = namedBounds(body, /head.?rest|neck.?pillow/i);
    const back = namedBounds(body, /back.?rest|back.?panel/i);
    const chair = new THREE.Box3().setFromObject(body);
    const mount = builtin ?? back;
    const scale = parent.dimensions && accessory.dimensions ? 1 : parent.size.x * 0.53 / accessory.size.x;
    const height = accessory.size.y * scale;
    const depth = accessory.size.z * scale;
    const mountCenter = mount?.getCenter(new THREE.Vector3());
    const mountY = builtin ? mountCenter.y : (back?.max.y ?? chair.max.y) - height * 0.33;
    const mountZ = mount?.max.z ?? chair.min.z + parent.size.z * 0.31;
    accessory.group.scale.setScalar(scale);
    accessory.group.position.set(mountCenter?.x ?? 0, mountY - height / 2, mountZ + depth * 0.42);
    if (this.headrest) release(this.headrest);
    this.headrest = accessory.group;
    parent.group.add(this.headrest);
    this.dirty = true;
  }
}
