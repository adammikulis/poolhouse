// The 3D room: loads room.glb.gz, drives its instruments from the app's one run feed, wires navigation and cards.
import * as THREE from 'three';
import { GLTFLoader } from './vendor/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'poolhouse-assets/RoomEnvironment.js';
import { Driver } from './drive.js';
import { Navigator } from './fly.js';
import { describe, fillCard, instrumentOf } from './cards.js';
import { paintFloor, paintCeiling, paintMimic } from './paint.js';

const EYE_START = 1.7;

function applyMaterials(root) {
  const floorTex = paintFloor();
  floorTex.repeat.set(1, 1);
  const mimics = [0, 1, 2, 3].map((i) => paintMimic(i));
  root.traverse((o) => {
    if (!o.isMesh) return;
    const m = o.material;
    if (m.name === 'M_floor') { m.map = floorTex; m.needsUpdate = true; }
    if (m.name === 'M_ceiling') { m.map = paintCeiling(); m.needsUpdate = true; }
    const mm = /^M_mimic(\d)$/.exec(m.name);
    if (mm) { o.material = new THREE.MeshStandardMaterial({ map: mimics[+mm[1]], emissiveMap: mimics[+mm[1]], emissive: 0xffffff, emissiveIntensity: 0.35, roughness: 0.4 }); }
    if (m.name === 'M_light_panel' || m.name === 'M_hall_glow') m.toneMapped = false;
  });
}

function addLights(root, scene) {
  root.traverse((o) => {
    if (!/^LIGHT_\d+$/.test(o.name)) return;
    const p = new THREE.PointLight(0xfff4e6, 7, 14, 1.6);
    o.getWorldPosition(p.position);
    scene.add(p);
  });
}

function collectBookmarks(root, state) {
  const pos = {}, tgt = {};
  root.traverse((o) => {
    let m = /^CAM_(.+)$/.exec(o.name);
    if (m) pos[m[1]] = o.getWorldPosition(new THREE.Vector3());
    m = /^CAMTARGET_(.+)$/.exec(o.name);
    if (m) tgt[m[1]] = o.getWorldPosition(new THREE.Vector3());
  });
  for (const k of Object.keys(pos)) state.bookmarks[k] = [pos[k], tgt[k]];
}

async function loadRoom() {
  const res = await fetch(new URL('./room.glb.gz', import.meta.url));
  if (!res.ok) throw new Error(`room.glb.gz answered HTTP ${res.status}`);
  const buf = await new Response(res.body.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
  return new GLTFLoader().parseAsync(buf, '/room/');
}

// Builds the room and returns {show, hide, goBookmark, goTo}; ``state`` is filled in for the page and the tests.
export async function start3d({ RB, feed, spec, $, state }) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, canvas: $('view') });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.9;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x15181a);
  const camera = new THREE.PerspectiveCamera(68, 1, 0.05, 60);
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.3;
  scene.add(new THREE.HemisphereLight(0xdfe8ee, 0x555048, 0.35));
  Object.assign(state, { spec, bookmarks: {}, raycaster: new THREE.Raycaster(), THREE });

  const resize = () => {
    const w = $('stage').clientWidth, h = $('stage').clientHeight;
    if (w < 2 || h < 2) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  addEventListener('resize', resize);
  new ResizeObserver(resize).observe($('stage'));

  const showCard = (key) => {
    fillCard($, describe(key, spec, state.driver, feed));
    $('card-core').hidden = !(key === 'RODMAP' || key.startsWith('ROD_'));
    $('card-open').hidden = true;
    state.cardKey = key;
  };

  const pick = (cx, cy) => {
    const r = renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((cx - r.left) / r.width) * 2 - 1, -((cy - r.top) / r.height) * 2 + 1);
    state.raycaster.setFromCamera(ndc, camera);
    const hit = state.raycaster.intersectObject(state.root, true).find((h) => h.object.isMesh && !h.object.material.transparent);
    if (!hit) return;
    const key = instrumentOf(hit.object);
    state.lastHit = hit.point.clone();
    if (key === 'FLOOR') {
      state.nav.teleport(hit.point);
      $('card').hidden = true;
      return;
    }
    const ins = key && spec.instruments.find((i) => i.id === key);
    if (ins && ins.type === 'key') {
      RB.desk.press(ins.action);
      return;
    }
    if (key) showCard(key);
  };

  const goBookmark = (id) => {
    const [p, t] = state.bookmarks[id];
    state.nav.flyTo(p, t);
  };
  const goTo = (b, instant = true) => {
    const [p, t] = state.bookmarks[b];
    state.nav.flyTo(p, t, instant ? 0.001 : 0.9);
    state.nav.update(1);
  };

  const gltf = await loadRoom();
  state.root = gltf.scene;
  scene.add(gltf.scene);
  applyMaterials(gltf.scene);
  addLights(gltf.scene, scene);
  collectBookmarks(gltf.scene, state);
  const outline = JSON.parse((gltf.scene.userData && gltf.scene.userData.outline) || (gltf.scenes[0].userData || {}).outline || '[]');
  state.driver = new Driver(gltf.scene, spec, feed);
  state.nav = new Navigator(camera, renderer.domElement, outline, pick);
  camera.position.set(0, EYE_START, 0);
  goBookmark('overview');
  state.nav.update(1);

  let last = performance.now();
  const frame = (now) => {
    const dt = Math.min((now - last) / 1000, 0.1);
    last = now;
    state.nav.update(dt);
    state.driver.update(dt);
    if (state.cardKey && !$('card').hidden && Math.floor(now / 400) !== state.lastCard) {
      state.lastCard = Math.floor(now / 400);
      showCard(state.cardKey);
    }
    renderer.render(scene, camera);
  };
  return {
    show() {
      resize();
      last = performance.now();
      renderer.setAnimationLoop(frame);
    },
    hide() {
      renderer.setAnimationLoop(null);
    },
    goBookmark,
    goTo,
    setNavigation(mode) {
      state.nav.setMode(mode);
    },
  };
}
