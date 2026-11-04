// J.A.R.V.I.S. core: a photoreal 3D model of a machined power core, rendered with three.js
// (MIT licence, in vendor/three).
//
// It is modelled and lit like a real object: a brushed titanium housing, copper coil windings,
// polished steel rings, a glass lens over a bright emitter and a backlit engraved bezel, with studio
// reflections, soft shadows and a camera-like finish (filmic tone mapping, gentle lens glow, grain).
// It works as an instrument too: the light follows JARVIS's state and voice, the LED ring is a live
// level meter, the bezel ratchets round while Claude works, and callouts show live readings.
//
// hud.js writes what JARVIS is doing into window.HUD_SCENE; this file only reads it and draws.
// If 3D isn't available, it says so and hud.js draws the 2D version (reactor2d.js) instead.

import * as THREE from './vendor/three/three.module.min.js';
import { EffectComposer } from './vendor/three/postprocessing/EffectComposer.js';
import { RenderPass } from './vendor/three/postprocessing/RenderPass.js';
import { UnrealBloomPass } from './vendor/three/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from './vendor/three/postprocessing/ShaderPass.js';
import { OutputPass } from './vendor/three/postprocessing/OutputPass.js';

const S = window.HUD_SCENE || (window.HUD_SCENE = { state: 'boot', levels: { mic: 0, out: 0 }, boot: 0, quality: 'full' });
window.HUD_SCENE_STARTED = true;   // three.js loaded; hud.js only falls back to 2D if we report a failure

const TAU = Math.PI * 2;
// How the device behaves in each state: emitter power, glow behind the coils, how fast the rings
// turn, what the LED ring shows, and whether the bezel ratchets.
const DEVICE = {
  boot:         { core: 2.5, glow: 0.5, spin: 0.6, leds: 'boot' },
  standby:      { core: 3.2, glow: 0.55, spin: 0.15, leds: 'idle' },
  listening:    { core: 4.2, glow: 0.9, spin: 0.25, leds: 'mic' },
  transcribing: { core: 4.0, glow: 0.8, spin: 0.6, leds: 'sweep' },
  thinking:     { core: 4.6, glow: 1.0, spin: 0.4, leds: 'chase', ratchet: true },
  speaking:     { core: 4.6, glow: 1.0, spin: 0.25, leds: 'out' },
  confirm:      { core: 3.6, glow: 0.9, spin: 0.0, leds: 'alarm' },
  muted:        { core: 0.7, glow: 0.12, spin: 0.05, leds: 'off' },
};

function fail(reason) {
  window.HUD_SCENE_FAILED = String(reason);
  window.dispatchEvent(new CustomEvent('jarvis-scene-failed', { detail: String(reason) }));
}
function webgl2() {
  try { return !!document.createElement('canvas').getContext('webgl2'); } catch (e) { return false; }
}
const srgb = (r, g, b) => new THREE.Color().setRGB(r, g, b, THREE.SRGBColorSpace);

// ------------------------------------------------------------------ procedural textures
// Everything is generated here, so nothing is downloaded.
function makeCanvas(w, h, draw) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  draw(c.getContext('2d'), w, h);
  return c;
}
function random(seed) {
  let s = seed;
  return () => (s = (s * 16807) % 2147483647) / 2147483647;
}
function greyImage(w, h, value) {          // fills a canvas pixel by pixel from value(x, y) in 0..1
  return makeCanvas(w, h, (g) => {
    const img = g.createImageData(w, h);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const v = Math.max(0, Math.min(255, value(x, y) * 255));
        const i = (y * w + x) * 4;
        img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
        img.data[i + 3] = 255;
      }
    }
    g.putImageData(img, 0, 0);
  });
}
// Turned or brushed metal: fine streaks running along the rows.
function brushedCanvas(seed) {
  const r = random(seed);
  const rows = [];
  let row = 0.5;
  for (let y = 0; y < 512; y++) { row = 0.7 * row + 0.3 * r(); rows.push(0.5 + (row - 0.5) * 0.45 + (r() - 0.5) * 0.18); }
  return greyImage(256, 512, (x, y) => rows[y] + (r() - 0.5) * 0.06);
}
// The same, as rings around the centre of a flat face.
function concentricCanvas(seed) {
  const r = random(seed);
  const rows = [];
  let row = 0.5;
  for (let i = 0; i < 800; i++) { row = 0.7 * row + 0.3 * r(); rows.push(0.5 + (row - 0.5) * 0.45 + (r() - 0.5) * 0.18); }
  const n = 1024;
  return greyImage(n, n, (x, y) => rows[Math.min(799, Math.round(Math.hypot(x - n / 2, y - n / 2)))] + (r() - 0.5) * 0.05);
}
const wireCanvas = () => greyImage(64, 4, (x) => 0.5 + 0.5 * Math.cos((x / 64) * TAU));            // wound wire
const knurlCanvas = () => greyImage(64, 64, (x, y) => 0.5 + 0.25 * (Math.cos(((x + y) / 64) * TAU) + Math.cos(((x - y) / 64) * TAU)));
// The engraved, backlit markings on the bezel (white = engraved).
function bezelCanvas(inner, outer) {
  const n = 2048;
  return makeCanvas(n, n, (g) => {
    const k = n / 2 / outer;                   // pixels per unit
    g.fillStyle = '#000';
    g.fillRect(0, 0, n, n);
    g.translate(n / 2, n / 2);
    g.strokeStyle = g.fillStyle = '#fff';
    for (let i = 0; i < 120; i++) {
      const a = (i / 120) * TAU, major = i % 10 === 0, mid = i % 5 === 0;
      const r1 = (outer - 0.006) * k, r0 = (major ? outer - 0.04 : mid ? outer - 0.028 : outer - 0.019) * k;
      g.lineWidth = (major ? 0.0045 : 0.0028) * k;
      g.beginPath();
      g.moveTo(Math.sin(a) * r0, -Math.cos(a) * r0);
      g.lineTo(Math.sin(a) * r1, -Math.cos(a) * r1);
      g.stroke();
    }
    g.font = `600 ${0.015 * k}px "SF Mono", Menlo, monospace`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    for (let d = 0; d < 360; d += 30) {
      g.save();
      g.rotate((d / 360) * TAU);
      g.fillText(String(d).padStart(3, '0'), 0, -(inner + 0.012) * k);
      g.restore();
    }
  });
}
function glowCanvas() {
  return makeCanvas(256, 256, (g) => {
    const grad = g.createRadialGradient(128, 128, 0, 128, 128, 128);
    grad.addColorStop(0, 'rgba(255,255,255,1)');
    grad.addColorStop(0.25, 'rgba(255,255,255,0.35)');
    grad.addColorStop(0.6, 'rgba(255,255,255,0.06)');
    grad.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = grad;
    g.fillRect(0, 0, 256, 256);
  });
}

// A dark photo studio with soft light panels, used only for reflections on the metal and glass.
function studio(renderer) {
  const env = new THREE.Scene();
  env.add(new THREE.Mesh(new THREE.BoxGeometry(14, 14, 14), new THREE.MeshBasicMaterial({ color: 0x07090b, side: THREE.BackSide })));
  const panel = (w, h, x, y, z, strength, color = 0xffffff) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(color).multiplyScalar(strength), side: THREE.DoubleSide }));
    m.position.set(x, y, z);
    m.lookAt(0, 0, 0);
    env.add(m);
  };
  panel(8, 2.4, 0, 6, 1.5, 4.5);                   // large softbox overhead
  panel(1.4, 7, -6, 0.5, 2.5, 3.2);                // tall strip, left
  panel(1.4, 7, 6, -0.5, -1.5, 1.4, 0x9cc4ff);     // cool strip, right and behind
  panel(6, 1.6, 0, -2, 6, 0.9);                    // low fill in front (shows in the faces)
  panel(2.6, 2.6, 3.2, 3.2, 5, 2.2);               // small key, top right in front
  const pmrem = new THREE.PMREMGenerator(renderer);
  const target = pmrem.fromScene(env, 0.035);
  pmrem.dispose();
  env.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
  return target.texture;
}

// A turned part: `points` is the profile as [radius, depth] pairs, counter-clockwise; every corner is
// kept crisp unless the point is marked smooth with a third value of true.
function lathe(points, segments = 160) {
  const pts = [];
  points.forEach((p, i) => {
    const v = new THREE.Vector2(p[0], p[1]);
    pts.push(v);
    if (!p[2] && i > 0 && i < points.length - 1) pts.push(v.clone());
  });
  const g = new THREE.LatheGeometry(pts, segments);
  g.rotateX(Math.PI / 2);                      // the lathe turns around y; the device faces +z
  return g;
}

const FINISH = {                               // the camera: slight colour fringing at the edges, film grain
  uniforms: { tDiffuse: { value: null }, uTime: { value: 0 }, uGrain: { value: 0.028 } },
  vertexShader: 'varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
  fragmentShader: /* glsl */`
    uniform sampler2D tDiffuse; uniform float uTime; uniform float uGrain; varying vec2 vUv;
    float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
    void main() {
      vec2 c = vUv - 0.5;
      vec2 off = c * dot(c, c) * 0.01;
      vec3 col = vec3(texture2D(tDiffuse, vUv + off).r, texture2D(tDiffuse, vUv).g, texture2D(tDiffuse, vUv - off).b);
      col += (hash(gl_FragCoord.xy + fract(uTime * 7.31) * 113.0) - 0.5) * uGrain;
      gl_FragColor = vec4(col, 1.0);
    }`,
};

function start() {
  const ART = window.JARVIS_ART;
  if (!ART) return fail('art.js did not load');
  const LOOKS = ART.LOOKS;
  const canvas = document.getElementById('scene');
  if (!canvas || !webgl2()) return fail('WebGL2 not available');

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: 'high-performance' });
  renderer.setClearColor(0x030608, 1);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  const maxAniso = renderer.capabilities.getMaxAnisotropy();

  const scene = new THREE.Scene();
  scene.environment = studio(renderer);
  scene.environmentIntensity = 0.75;
  const camera = new THREE.PerspectiveCamera(26, 1, 0.1, 60);
  const device = new THREE.Group();
  scene.add(device);

  function texture(cv, { repeat = null, color = false } = {}) {
    const t = new THREE.CanvasTexture(cv);
    t.colorSpace = color ? THREE.SRGBColorSpace : THREE.NoColorSpace;
    t.anisotropy = maxAniso;
    if (repeat) { t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(repeat[0], repeat[1]); }
    return t;
  }
  const add = (geometry, material, z = 0, parent = device) => {
    const m = new THREE.Mesh(geometry, material);
    m.position.z = z;
    m.castShadow = m.receiveShadow = true;
    parent.add(m);
    return m;
  };

  // ---------------------------------------------------------------- materials
  const turned = texture(brushedCanvas(7), { repeat: [1, 5] });
  const turnedFine = texture(brushedCanvas(19), { repeat: [1, 9] });
  const faceGrain = texture(concentricCanvas(11));
  const titanium = new THREE.MeshPhysicalMaterial({
    color: srgb(0.38, 0.4, 0.42), metalness: 1, roughness: 0.62, roughnessMap: turned, bumpMap: turned, bumpScale: 0.35, anisotropy: 0.55,
  });
  const steel = new THREE.MeshPhysicalMaterial({
    color: srgb(0.72, 0.74, 0.76), metalness: 1, roughness: 0.4, roughnessMap: turnedFine, bumpMap: turnedFine, bumpScale: 0.25, anisotropy: 0.6,
  });
  const chrome = new THREE.MeshPhysicalMaterial({ color: srgb(0.74, 0.76, 0.79), metalness: 1, roughness: 0.14, side: THREE.DoubleSide });
  const anodised = new THREE.MeshPhysicalMaterial({ color: srgb(0.075, 0.08, 0.088), metalness: 0.7, roughness: 0.5, clearcoat: 0.3, clearcoatRoughness: 0.4 });
  const iron = new THREE.MeshPhysicalMaterial({ color: srgb(0.42, 0.43, 0.44), metalness: 1, roughness: 0.35 });
  const bobbin = new THREE.MeshPhysicalMaterial({ color: srgb(0.09, 0.095, 0.1), metalness: 0, roughness: 0.55, clearcoat: 0.4, clearcoatRoughness: 0.3 });
  const wire = texture(wireCanvas(), { repeat: [1 / 0.0062, 1] });
  const copper = new THREE.MeshPhysicalMaterial({
    color: srgb(0.86, 0.5, 0.33), metalness: 1, roughness: 0.32, bumpMap: wire, bumpScale: 1.4, clearcoat: 0.85, clearcoatRoughness: 0.1,
  });
  const engraving = texture(bezelCanvas(0.878, 0.942));
  const bezelFaceMaterial = new THREE.MeshPhysicalMaterial({
    color: srgb(0.66, 0.68, 0.7), metalness: 1, roughness: 0.42, roughnessMap: faceGrain, bumpMap: engraving, bumpScale: -1.5,
    emissive: new THREE.Color(1, 1, 1), emissiveMap: engraving, emissiveIntensity: 1, anisotropy: 0.4,
  });
  const socket = new THREE.MeshStandardMaterial({ color: 0x050505, roughness: 0.9 });

  // ---------------------------------------------------------------- the model (outer radius 1.04)
  // Housing: a thick turned ring with grooves round its side and a recessed channel for the LEDs.
  add(lathe([
    [1.02, -0.23], [1.04, -0.21], [1.04, -0.15], [1.032, -0.145], [1.032, -0.125], [1.04, -0.12],
    [1.04, -0.085], [1.032, -0.08], [1.032, -0.06], [1.04, -0.055], [1.04, 0.0], [1.02, 0.02],
    [0.86, 0.02], [0.86, 0.008], [0.83, 0.008], [0.83, 0.02], [0.815, 0.02], [0.8, 0.005], [0.8, -0.23], [1.02, -0.23],
  ]), titanium);

  // Bolts round the face and between the coils.
  const boltSpots = [];
  for (let i = 0; i < 18; i++) boltSpots.push([0.99, (i / 18) * TAU + 0.17, 0.02]);
  for (let i = 0; i < 12; i++) boltSpots.push([0.715, (i / 12) * TAU + TAU / 24, -0.19]);
  const headGeometry = new THREE.CylinderGeometry(0.016, 0.016, 0.011, 28).rotateX(Math.PI / 2).translate(0, 0, 0.0055);
  const socketGeometry = new THREE.CylinderGeometry(0.0072, 0.0072, 0.003, 6).rotateX(Math.PI / 2).translate(0, 0, 0.0105);
  const heads = new THREE.InstancedMesh(headGeometry, steel, boltSpots.length);
  const sockets = new THREE.InstancedMesh(socketGeometry, socket, boltSpots.length);
  const place = new THREE.Object3D();
  boltSpots.forEach(([r, a, z], i) => {
    place.position.set(Math.sin(a) * r, Math.cos(a) * r, z);
    place.rotation.set(0, 0, a * 3.7);
    place.updateMatrix();
    heads.setMatrixAt(i, place.matrix);
    sockets.setMatrixAt(i, place.matrix);
  });
  heads.castShadow = heads.receiveShadow = true;
  device.add(heads, sockets);

  // Bezel: a raised ring with engraved, backlit markings; it turns.
  const bezel = new THREE.Group();
  device.add(bezel);
  add(lathe([[0.95, 0.02], [0.95, 0.042], [0.942, 0.05], [0.878, 0.05], [0.87, 0.042], [0.87, 0.02], [0.95, 0.02]]), steel, 0, bezel);
  add(new THREE.RingGeometry(0.878, 0.942, 256, 1), bezelFaceMaterial, 0.0502, bezel);

  // LED ring: 48 segments in the channel; a live level meter.
  const LEDS = 48;
  const leds = new THREE.InstancedMesh(new THREE.BoxGeometry(0.072, 0.011, 0.005), new THREE.MeshBasicMaterial({ color: 0xffffff }), LEDS);
  for (let i = 0; i < LEDS; i++) {
    const a = (i / LEDS) * TAU;
    place.position.set(Math.sin(a) * 0.845, Math.cos(a) * 0.845, 0.011);
    place.rotation.set(0, 0, -a);
    place.updateMatrix();
    leds.setMatrixAt(i, place.matrix);
    leds.setColorAt(i, new THREE.Color(0.02, 0.02, 0.02));
  }
  device.add(leds);

  // Back plate with vents, and the glow behind it.
  const plateShape = new THREE.Shape();
  plateShape.absarc(0, 0, 0.8, 0, TAU, false);
  for (let i = 0; i < 36; i++) {
    const a0 = (i / 36) * TAU + 0.035, a1 = a0 + TAU / 36 - 0.07;
    const hole = new THREE.Path();
    hole.absarc(0, 0, 0.765, a0, a1, false);
    hole.absarc(0, 0, 0.53, a1, a0, true);
    plateShape.holes.push(hole);
  }
  add(new THREE.ExtrudeGeometry(plateShape, { depth: 0.02, bevelEnabled: true, bevelThickness: 0.003, bevelSize: 0.003, bevelSegments: 1, curveSegments: 72 }), anodised, -0.212);
  const ventMaterial = new THREE.MeshBasicMaterial({ color: 0x000000 });
  const vent = new THREE.Mesh(new THREE.RingGeometry(0.5, 0.79, 128), ventMaterial);
  vent.position.z = -0.228;
  device.add(vent);

  // Twelve coils: copper wound on a bobbin round an iron core.
  const COILS = 12;
  const coilShape = new THREE.Shape();
  (() => {
    const x = -0.1, y = -0.052, w = 0.2, h = 0.104, r = 0.022;
    coilShape.moveTo(x + r, y); coilShape.lineTo(x + w - r, y); coilShape.quadraticCurveTo(x + w, y, x + w, y + r);
    coilShape.lineTo(x + w, y + h - r); coilShape.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    coilShape.lineTo(x + r, y + h); coilShape.quadraticCurveTo(x, y + h, x, y + h - r);
    coilShape.lineTo(x, y + r); coilShape.quadraticCurveTo(x, y, x + r, y);
  })();
  const windingGeometry = new THREE.ExtrudeGeometry(coilShape, { depth: 0.084, bevelEnabled: true, bevelThickness: 0.012, bevelSize: 0.01, bevelSegments: 4, curveSegments: 8 }).translate(0, 0, -0.042);
  const windings = new THREE.InstancedMesh(windingGeometry, copper, COILS);
  const flanges = new THREE.InstancedMesh(new THREE.BoxGeometry(0.014, 0.132, 0.124), bobbin, COILS * 2);
  const cores = new THREE.InstancedMesh(new THREE.BoxGeometry(0.262, 0.046, 0.046), iron, COILS);
  for (let i = 0; i < COILS; i++) {
    const a = (i / COILS) * TAU;
    const at = (radial) => {
      place.position.set(Math.cos(a) * radial, Math.sin(a) * radial, -0.13);
      place.rotation.set(0, 0, a);
      place.updateMatrix();
      return place.matrix.clone();
    };
    const middle = at(0.645);
    windings.setMatrixAt(i, middle);
    cores.setMatrixAt(i, middle);
    flanges.setMatrixAt(i * 2, at(0.645 - 0.112));
    flanges.setMatrixAt(i * 2 + 1, at(0.645 + 0.112));
  }
  [windings, flanges, cores].forEach((m) => { m.castShadow = m.receiveShadow = true; device.add(m); });

  // Inner retaining ring with a knurled band; it turns slowly.
  const innerRing = new THREE.Group();
  device.add(innerRing);
  add(lathe([[0.5, -0.19], [0.5, 0.036], [0.49, 0.05], [0.415, 0.05], [0.4, 0.036], [0.4, -0.19], [0.5, -0.19]]), steel, 0, innerRing);
  const knurl = texture(knurlCanvas(), { repeat: [96, 1] });
  add(new THREE.CylinderGeometry(0.5016, 0.5016, 0.05, 192, 1, true).rotateX(Math.PI / 2),
    new THREE.MeshPhysicalMaterial({ color: srgb(0.7, 0.72, 0.74), metalness: 1, roughness: 0.38, bumpMap: knurl, bumpScale: 2.5 }), -0.03, innerRing);

  // Polished reflector, the emitter and its filament ring, and the glass lens over them.
  const bowl = [];
  for (let i = 0; i <= 20; i++) { const t = i / 20; bowl.push([0.07 + 0.325 * t, -0.13 + 0.15 * t * t, true]); }
  add(lathe(bowl, 128), chrome);
  const emitterMaterial = new THREE.MeshBasicMaterial({ color: 0x000000 });
  const emitter = new THREE.Mesh(new THREE.SphereGeometry(0.05, 32, 16), emitterMaterial);
  emitter.position.z = -0.065;
  device.add(emitter);
  const filamentMaterial = new THREE.MeshBasicMaterial({ color: 0x000000 });
  const filament = new THREE.Mesh(new THREE.TorusGeometry(0.085, 0.0045, 12, 128), filamentMaterial);
  filament.position.z = -0.07;
  device.add(filament);
  const lensGeometry = new THREE.SphereGeometry(0.99, 128, 24, 0, TAU, 0, 0.41).rotateX(Math.PI / 2);
  const glass = new THREE.MeshPhysicalMaterial({ color: 0xffffff, metalness: 0, roughness: 0.04, transmission: 1, thickness: 0.12, ior: 1.5, clearcoat: 1, clearcoatRoughness: 0.03 });
  const glassLite = new THREE.MeshPhysicalMaterial({ color: 0xffffff, metalness: 0, roughness: 0.05, transparent: true, opacity: 0.18, clearcoat: 1, depthWrite: false });
  const lens = new THREE.Mesh(lensGeometry, glass);
  lens.position.z = 0.03 - 0.99 * Math.cos(0.41);
  device.add(lens);
  const hit = new THREE.Mesh(new THREE.CircleGeometry(0.46, 32), new THREE.MeshBasicMaterial({ visible: false }));
  hit.position.z = 0.06;
  device.add(hit);

  // Lights: the emitter lights the device; a studio key and a cool rim light shape it.
  const tint = srgb(...LOOKS.boot.color);
  const coreLight = new THREE.PointLight(tint, 0, 0, 2);
  coreLight.position.set(0, 0, 0.14);
  device.add(coreLight);
  const key = new THREE.DirectionalLight(0xffffff, 2.0);
  key.position.set(-2.6, 3.2, 4.2);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  Object.assign(key.shadow.camera, { left: -1.4, right: 1.4, top: 1.4, bottom: -1.4, near: 1, far: 12 });
  key.shadow.bias = -0.0004;
  key.shadow.normalBias = 0.015;
  key.shadow.radius = 3;
  scene.add(key);
  const rim = new THREE.DirectionalLight(0x9cc4ff, 0.9);
  rim.position.set(3.5, 0.6, -2.5);
  scene.add(rim);

  // A soft haze of light round the core, and a little dust caught in it.
  const hazeMaterial = new THREE.SpriteMaterial({ map: texture(glowCanvas(), { color: true }), color: 0x000000, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false });
  const haze = new THREE.Sprite(hazeMaterial);
  haze.scale.set(3.6, 3.6, 1);
  haze.position.z = -0.5;
  device.add(haze);
  const DUST = 300;
  const dustGeometry = new THREE.BufferGeometry();
  const dp = new Float32Array(DUST * 3), ds = new Float32Array(DUST);
  for (let i = 0; i < DUST; i++) {
    dp[i * 3] = (Math.random() - 0.5) * 5; dp[i * 3 + 1] = (Math.random() - 0.5) * 4; dp[i * 3 + 2] = -1.5 + Math.random() * 3.2;
    ds[i] = Math.random();
  }
  dustGeometry.setAttribute('position', new THREE.BufferAttribute(dp, 3));
  dustGeometry.setAttribute('aSeed', new THREE.BufferAttribute(ds, 1));
  const dustMaterial = new THREE.ShaderMaterial({
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
    uniforms: { uColor: { value: new THREE.Color() }, uTime: { value: 0 }, uPixelRatio: { value: 1 } },
    vertexShader: /* glsl */`
      attribute float aSeed; uniform float uTime; uniform float uPixelRatio; varying float vA;
      void main() {
        vec3 p = position;
        p.x += sin(uTime * 0.05 + aSeed * 40.0) * 0.12;
        p.y = mod(position.y + uTime * 0.012 * (0.5 + aSeed) + 2.0, 4.0) - 2.0;
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        gl_Position = projectionMatrix * mv;
        gl_PointSize = (0.8 + aSeed * 1.4) * uPixelRatio * (6.0 / -mv.z);
        vA = (0.08 + 0.3 * aSeed) * (1.0 - smoothstep(0.4, 2.2, length(p.xy)));   // lit by the core
      }`,
    fragmentShader: /* glsl */`
      uniform vec3 uColor; varying float vA;
      void main() { float m = 1.0 - smoothstep(0.0, 0.5, length(gl_PointCoord - 0.5)); gl_FragColor = vec4(uColor * m * vA, 1.0); }`,
  });
  const dust = new THREE.Points(dustGeometry, dustMaterial);
  dust.frustumCulled = false;
  scene.add(dust);

  // ---------------------------------------------------------------- the camera's finish
  const composer = new EffectComposer(renderer, new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: 4 }));
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.32, 0.32, 1.0);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());
  const finish = new ShaderPass(FINISH);
  composer.addPass(finish);

  // ---------------------------------------------------------------- callouts with live readings
  const NS = 'http://www.w3.org/2000/svg';
  const callouts = document.createElement('div');
  callouts.className = 'callouts';
  callouts.setAttribute('aria-hidden', 'true');
  const svg = document.createElementNS(NS, 'svg');
  callouts.appendChild(svg);
  const items = [
    { key: 'cpu', name: 'Processor', at: [-0.66, 0.66], corner: [-1, -1] },
    { key: 'mem', name: 'Memory', at: [0.66, 0.66], corner: [1, -1] },
    { key: 'net', name: 'Network', at: [-0.66, -0.66], corner: [-1, 1] },
    { key: 'core', name: 'AI core', at: [0.66, -0.66], corner: [1, 1] },
  ].map((it) => {
    const line = document.createElementNS(NS, 'polyline');
    const dot = document.createElementNS(NS, 'circle');
    dot.setAttribute('r', '2.5');
    svg.append(line, dot);
    const el = document.createElement('div');
    el.className = `callout ${it.corner[0] < 0 ? 'left' : 'right'}`;
    const name = document.createElement('span');
    name.textContent = it.name;
    const value = document.createElement('b');
    value.textContent = '--';
    el.append(name, value);
    callouts.appendChild(el);
    return { ...it, line, dot, el, value, text: '--', width: 0, point: new THREE.Vector3(it.at[0], it.at[1], 0.02) };
  });
  document.body.insertBefore(callouts, document.querySelector('.hud'));
  const projected = new THREE.Vector3();
  function placeCallouts() {
    const v = S.view;
    const W = canvas.clientWidth || window.innerWidth, H = canvas.clientHeight || window.innerHeight;
    const show = !!v && Math.min(v.w, v.h) > 380 && v.w > 420 && S.state !== 'boot';
    callouts.classList.toggle('shown', show);
    if (!show) return;
    const readouts = S.readouts || {};
    items.forEach((it) => {
      const text = readouts[it.key] || '--';
      if (text !== it.text) { it.text = text; it.value.textContent = text; it.width = 0; }
      if (!it.width) it.width = it.el.offsetWidth;
      projected.copy(it.point);
      device.localToWorld(projected);
      projected.project(camera);
      const ax = ((projected.x + 1) / 2) * W, ay = ((1 - projected.y) / 2) * H;
      const edge = v.x + it.corner[0] * (v.w / 2 - 14);                  // outer end of the leader line
      const ly = v.y + it.corner[1] * (v.h / 2 - 34);
      let ex = ax + it.corner[0] * Math.abs(ly - ay);                     // 45-degree run, then level
      ex = it.corner[0] < 0 ? Math.max(ex, edge + it.width + 8) : Math.min(ex, edge - it.width - 8);
      it.line.setAttribute('points', `${ax.toFixed(1)},${ay.toFixed(1)} ${ex.toFixed(1)},${ly.toFixed(1)} ${edge.toFixed(1)},${ly.toFixed(1)}`);
      it.dot.setAttribute('cx', ax.toFixed(1));
      it.dot.setAttribute('cy', ay.toFixed(1));
      const x = it.corner[0] < 0 ? edge : edge - it.width;
      it.el.style.transform = `translate(${x.toFixed(1)}px, ${(ly - 34).toFixed(1)}px)`;
    });
  }

  // ---------------------------------------------------------------- sizing and quality
  let quality = null, distance = 8, viewKey = '';
  function applyQuality() {
    quality = S.quality === 'lite' ? 'lite' : 'full';
    const ratio = quality === 'lite' ? 1 : Math.min(window.devicePixelRatio || 1, 1.5);
    renderer.setPixelRatio(ratio);
    composer.setPixelRatio(ratio);
    dustMaterial.uniforms.uPixelRatio.value = ratio;
    dust.visible = quality === 'full';
    lens.material = quality === 'full' ? glass : glassLite;     // real refraction costs an extra pass
    resize();
  }
  function resize() {
    const w = canvas.clientWidth || window.innerWidth, h = canvas.clientHeight || window.innerHeight;
    renderer.setSize(w, h, false);
    composer.setSize(w, h);
    camera.aspect = w / h;
    viewKey = '';
    fitToStage();
  }
  window.addEventListener('resize', resize);
  // hud.js reports the free space between the panels (S.view, in pixels). Aim the camera so the
  // device sits in the middle of it, and pull back until it fits.
  function fitToStage() {
    const W = canvas.clientWidth || window.innerWidth, H = canvas.clientHeight || window.innerHeight;
    const v = S.view;
    const key = v ? `${W}x${H}:${v.x | 0},${v.y | 0},${v.w | 0},${v.h | 0}` : `${W}x${H}`;
    if (key === viewKey) return;
    viewKey = key;
    const tan = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const room = 1.1;                          // the housing (radius 1.04) plus a little margin
    if (!v || v.w < 50 || v.h < 50) {
      camera.clearViewOffset();
      distance = room / (2 * tan * 0.44 * Math.min(1, camera.aspect));
    } else {
      camera.setViewOffset(W, H, W / 2 - v.x, H / 2 - v.y, W, H);
      distance = (room * H) / (2 * tan * 0.44 * Math.min(v.w, v.h));
    }
    camera.updateProjectionMatrix();
  }

  // --------------------------------------------------------------- interaction
  const pointer = { x: 0, y: 0, tx: 0, ty: 0 };
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  function hitsCore(ev) {
    const r = canvas.getBoundingClientRect();
    ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    raycaster.setFromCamera(ndc, camera);
    return raycaster.intersectObject(hit, false).length > 0;
  }
  window.addEventListener('pointermove', (ev) => {
    pointer.tx = (ev.clientX / window.innerWidth - 0.5) * 2;
    pointer.ty = (ev.clientY / window.innerHeight - 0.5) * 2;
    if (ev.target === canvas) canvas.style.cursor = hitsCore(ev) ? 'pointer' : 'default';
  });
  canvas.addEventListener('click', (ev) => { if (hitsCore(ev)) window.dispatchEvent(new CustomEvent('jarvis-core-click')); });

  // ------------------------------------------------------------------ animation
  const target = new THREE.Color();
  const dark = new THREE.Color(0.018, 0.019, 0.02);
  const ledColor = new THREE.Color();
  const live = { core: 0, glow: 0, spin: 0.2, audio: 0, power: 0 };
  let lastFrame = 0, bezelAngle = 0, bezelTarget = 0, ratchetClock = 0;
  const t0 = performance.now();

  function ledLevel(i, mode, time, A) {
    const f = i / LEDS;
    const around = (pos, width) => { const d = Math.abs(((f - pos + 1.5) % 1) - 0.5) * LEDS; return Math.exp(-(d * d) / (2 * width * width)); };
    switch (mode) {
      case 'boot': return f < (S.boot || 0) ? 0.7 : 0;
      case 'mic':
      case 'out': {                            // a level meter rising from the bottom up both sides
        const fromBottom = Math.abs(f - 0.5) * 2;
        const level = Math.min(1, A * 1.15);
        return fromBottom < level ? 0.45 + 0.55 * (1 - fromBottom) : 0.03;
      }
      case 'sweep': return 0.05 + 0.9 * around((time * 0.45) % 1, 2.5);
      case 'chase': return 0.05 + [0, 1, 2].reduce((s, k) => s + 0.9 * around((time * 0.55 + k / 3) % 1, 1.6), 0);
      case 'alarm': return 0.15 + 0.85 * (0.5 + 0.5 * Math.sin(time * 5.5));
      case 'off': return 0;
      default: return 0.06 + 0.5 * around((time * 0.06) % 1, 2);   // standing by: a slow heartbeat
    }
  }

  function frame(now) {
    requestAnimationFrame(frame);
    if (S.paused) return;                      // (used to take still pictures for the docs)
    if (S.quality !== quality) applyQuality();
    // Leave the graphics chip to speech recognition while it works; save power in Lite mode.
    const minGap = S.state === 'transcribing' ? 100 : quality === 'lite' ? 33 : 0;
    if (now - lastFrame < minGap) return;
    const dt = Math.min(0.1, (now - (lastFrame || now)) / 1000);
    lastFrame = now;
    const time = (now - t0) / 1000;
    const motion = reduceMotion ? 0.25 : 1;
    fitToStage();

    const look = LOOKS[S.state] || LOOKS.standby;
    const dev = DEVICE[S.state] || DEVICE.standby;
    const k = Math.min(1, dt * 2.5);
    tint.lerp(target.setRGB(look.color[0], look.color[1], look.color[2], THREE.SRGBColorSpace), k);
    live.power += ((S.state === 'boot' ? Math.max(0.05, S.boot || 0) : 1) - live.power) * Math.min(1, dt * 1.5);
    live.core += (dev.core - live.core) * k;
    live.glow += (dev.glow - live.glow) * k;
    live.spin += (dev.spin - live.spin) * k;
    const raw = S.state === 'speaking' ? S.levels.out : (S.state === 'listening' || S.state === 'confirm') ? S.levels.mic : 0;
    const audio = Math.min(1, Math.sqrt(Math.max(0, raw || 0)) * 2.4);
    live.audio += (audio - live.audio) * Math.min(1, dt * (audio > live.audio ? 14 : 4));
    const A = live.audio, P = live.power;

    // light: emitter, filament, the lamp it casts, the vents and the haze
    const pulse = S.state === 'confirm' ? 0.75 + 0.25 * Math.sin(time * 5.5) : 1;
    const core = live.core * P * pulse * (1 + 0.9 * A);
    emitterMaterial.color.copy(tint).multiplyScalar(core * 1.1);
    filamentMaterial.color.copy(tint).multiplyScalar(core * 0.6);
    coreLight.color.copy(tint);
    coreLight.intensity = core * 0.045;
    ventMaterial.color.copy(tint).multiplyScalar(live.glow * P * (0.3 + 0.45 * A) * 0.16);
    hazeMaterial.color.copy(tint).multiplyScalar((0.006 + 0.01 * live.glow + 0.01 * A) * P);
    bezelFaceMaterial.emissive.copy(tint);
    bezelFaceMaterial.emissiveIntensity = (0.12 + 0.45 * live.glow) * P;
    dustMaterial.uniforms.uColor.value.copy(tint);
    dustMaterial.uniforms.uTime.value = time * motion;

    // LED ring
    for (let i = 0; i < LEDS; i++) {
      const v = ledLevel(i, dev.leds, time, A) * P;
      ledColor.copy(dark).lerp(tint, Math.min(1, v * 4)).multiplyScalar(0.6 + v * 1.4);
      leds.setColorAt(i, ledColor);
    }
    leds.instanceColor.needsUpdate = true;

    // moving parts: the bezel drifts, or ratchets a few marks at a time while Claude works
    if (dev.ratchet) {
      ratchetClock += dt * motion;
      if (ratchetClock > 0.42) { ratchetClock = 0; bezelTarget += (TAU / 120) * 3; }
    } else {
      bezelTarget += dt * live.spin * 0.05 * motion;
    }
    bezelAngle += (bezelTarget - bezelAngle) * Math.min(1, dt * 9);
    bezel.rotation.z = -bezelAngle;
    innerRing.rotation.z += dt * live.spin * 0.12 * motion;

    // a three-quarter view, like a product shot; follows the mouse a little
    pointer.x += (pointer.tx - pointer.x) * Math.min(1, dt * 2);
    pointer.y += (pointer.ty - pointer.y) * Math.min(1, dt * 2);
    const sway = reduceMotion ? 0 : 1;
    device.rotation.y = -0.42 + (pointer.x * 0.12 + Math.sin(time * 0.11) * 0.05) * sway;
    device.rotation.x = -0.06 + (pointer.y * 0.08 + Math.sin(time * 0.08) * 0.025) * sway;
    camera.position.set(0, distance * 0.07, distance);
    camera.lookAt(0, 0, 0);

    finish.uniforms.uTime.value = time;
    composer.render(dt);
    S.frames = (S.frames || 0) + 1;
    placeCallouts();
    if (!window.HUD_SCENE_READY) {
      window.HUD_SCENE_READY = true;
      window.dispatchEvent(new CustomEvent('jarvis-scene-ready'));
    }
  }

  applyQuality();
  requestAnimationFrame(frame);
}

try {
  start();
} catch (err) {
  console.error(err);
  fail(err && err.message ? err.message : err);
}
