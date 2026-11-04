// J.A.R.V.I.S. holographic core, drawn in 3D with three.js (MIT licence, in vendor/three).
//
// hud.js owns the interface and writes what JARVIS is doing into window.HUD_SCENE; this file
// only reads it and draws. If 3D isn't available, it says so and hud.js draws a 2D core instead.

import * as THREE from './vendor/three/three.module.min.js';
import { EffectComposer } from './vendor/three/postprocessing/EffectComposer.js';
import { RenderPass } from './vendor/three/postprocessing/RenderPass.js';
import { UnrealBloomPass } from './vendor/three/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from './vendor/three/postprocessing/ShaderPass.js';
import { OutputPass } from './vendor/three/postprocessing/OutputPass.js';

const S = window.HUD_SCENE || (window.HUD_SCENE = { state: 'boot', levels: { mic: 0, out: 0 }, boot: 0, quality: 'full' });
window.HUD_SCENE_STARTED = true;   // three.js loaded; hud.js only falls back to 2D if we report a failure

// What each state looks like: colour, core energy, ring spin, particle swirl and flow, beam strength.
const LOOKS = {
  boot:         { color: [0.25, 0.80, 1.00], energy: 0.45, spin: 1.6, swirl: 0.6, pull: 0.0, beam: 0.35 },
  standby:      { color: [0.20, 0.72, 1.00], energy: 0.55, spin: 0.6, swirl: 0.25, pull: 0.0, beam: 0.45 },
  listening:    { color: [0.30, 0.92, 1.00], energy: 0.95, spin: 1.1, swirl: 0.5, pull: 1.0, beam: 0.85 },
  transcribing: { color: [0.52, 0.60, 1.00], energy: 0.85, spin: 2.2, swirl: 1.2, pull: 0.5, beam: 0.7 },
  thinking:     { color: [1.00, 0.62, 0.18], energy: 1.00, spin: 3.0, swirl: 1.7, pull: 0.0, beam: 0.9 },
  speaking:     { color: [0.42, 0.95, 1.00], energy: 1.05, spin: 1.2, swirl: 0.7, pull: -0.7, beam: 1.0 },
  confirm:      { color: [1.00, 0.26, 0.16], energy: 0.90, spin: 0.4, swirl: 0.3, pull: 0.0, beam: 0.6 },
  muted:        { color: [0.42, 0.52, 0.60], energy: 0.30, spin: 0.25, swirl: 0.12, pull: 0.0, beam: 0.2 },
};

// Small value-noise used by several shaders.
const NOISE = /* glsl */`
  float hash3(vec3 p) { p = fract(p * 0.3183099 + 0.1); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }
  float noise3(vec3 x) {
    vec3 i = floor(x); vec3 f = fract(x); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix(hash3(i), hash3(i + vec3(1,0,0)), f.x), mix(hash3(i + vec3(0,1,0)), hash3(i + vec3(1,1,0)), f.x), f.y),
               mix(mix(hash3(i + vec3(0,0,1)), hash3(i + vec3(1,0,1)), f.x), mix(hash3(i + vec3(0,1,1)), hash3(i + vec3(1,1,1)), f.x), f.y), f.z);
  }
  float fbm3(vec3 p) { float v = 0.0, a = 0.5; for (int i = 0; i < 4; i++) { v += a * noise3(p); p *= 2.03; a *= 0.5; } return v; }
`;

function fail(reason) {
  window.HUD_SCENE_FAILED = String(reason);
  window.dispatchEvent(new CustomEvent('jarvis-scene-failed', { detail: String(reason) }));
}

function webgl2() {
  try { return !!document.createElement('canvas').getContext('webgl2'); } catch (e) { return false; }
}

function glowTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(128, 128, 0, 128, 128, 128);
  grad.addColorStop(0, 'rgba(255,255,255,1)');
  grad.addColorStop(0.2, 'rgba(255,255,255,0.55)');
  grad.addColorStop(0.5, 'rgba(255,255,255,0.12)');
  grad.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function additive(color, opacity = 1) {
  return new THREE.MeshBasicMaterial({
    color, transparent: true, opacity, blending: THREE.AdditiveBlending,
    depthWrite: false, side: THREE.DoubleSide, toneMapped: false,
  });
}

function start() {
  const canvas = document.getElementById('scene');
  if (!canvas || !webgl2()) return fail('WebGL2 not available');

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  renderer.setClearColor(0x01060b, 1);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.85;
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x01060b);
  const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 200);
  camera.position.set(0, 0.6, 12);

  // A soft studio "environment" so the metal parts have something to reflect.
  {
    const env = new THREE.Scene();
    env.add(new THREE.Mesh(new THREE.SphereGeometry(20, 32, 16), new THREE.MeshBasicMaterial({ color: 0x06202c, side: THREE.BackSide })));
    const panel = (x, y, z, w, h, c) => {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: c, side: THREE.DoubleSide }));
      m.position.set(x, y, z); m.lookAt(0, 0, 0); env.add(m);
    };
    panel(-8, 6, 6, 8, 3, 0x9fe8ff);
    panel(9, -2, 4, 4, 8, 0x3fb8e0);
    panel(0, 10, -4, 10, 2, 0xffffff);
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(env, 0.04).texture;
    pmrem.dispose();
  }

  scene.add(new THREE.AmbientLight(0x335566, 0.5));
  const key = new THREE.DirectionalLight(0x9fdcff, 1.2);
  key.position.set(-4, 6, 8);
  scene.add(key);
  const coreLight = new THREE.PointLight(0x55e0ff, 40, 9, 2);
  coreLight.position.set(0, 0, 0.9);

  // ---------------------------------------------------------------- the reactor
  const rig = new THREE.Group();             // everything that hovers above the floor
  rig.position.y = 0.55;
  scene.add(rig);
  rig.add(coreLight);

  const color = new THREE.Color(...LOOKS.boot.color);

  // Plasma core
  const coreUniforms = { uTime: { value: 0 }, uColor: { value: color.clone() }, uEnergy: { value: 0.5 }, uLevel: { value: 0 } };
  const core = new THREE.Mesh(
    new THREE.SphereGeometry(0.62, 96, 64),
    new THREE.ShaderMaterial({
      uniforms: coreUniforms, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
      vertexShader: /* glsl */`
        uniform float uTime; uniform float uLevel;
        varying vec3 vNormal; varying vec3 vViewDir; varying vec3 vPos;
        ${NOISE}
        void main() {
          vPos = position;
          float d = (fbm3(position * 3.0 + uTime * 0.6) - 0.5) * (0.04 + uLevel * 0.16);
          vec4 mv = modelViewMatrix * vec4(position + normal * d, 1.0);
          vNormal = normalize(normalMatrix * normal);
          vViewDir = -mv.xyz;
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: /* glsl */`
        uniform float uTime; uniform vec3 uColor; uniform float uEnergy; uniform float uLevel;
        varying vec3 vNormal; varying vec3 vViewDir; varying vec3 vPos;
        ${NOISE}
        void main() {
          float fres = pow(1.0 - max(dot(normalize(vNormal), normalize(vViewDir)), 0.0), 2.2);
          vec3 p = vPos * 2.4 + vec3(0.0, uTime * 0.35, uTime * 0.2);
          float n = fbm3(p + fbm3(p * 1.7 + uTime * 0.15));
          float bands = smoothstep(0.38, 0.86, n);
          vec3 col = mix(uColor * 0.08, uColor * 1.1, bands) * (0.35 + uEnergy * 0.4 + uLevel * 0.35);
          col += uColor * fres * (0.8 + uLevel * 0.6);
          col += vec3(1.0) * pow(bands, 7.0) * (0.35 + uLevel * 0.45);
          gl_FragColor = vec4(col, clamp(0.5 + fres * 0.5 + bands * 0.3, 0.0, 1.0));
        }`,
    }),
  );
  core.name = 'core';
  rig.add(core);

  const hot = new THREE.Mesh(new THREE.SphereGeometry(0.1, 32, 16), new THREE.MeshBasicMaterial({ color: new THREE.Color(2.2, 2.2, 2.2), toneMapped: false }));
  rig.add(hot);

  const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture(), color: color.clone(), transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.55 }));
  halo.scale.set(3.4, 3.4, 1);
  rig.add(halo);
  const haze = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture(), color: color.clone(), transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.05 }));
  haze.scale.set(11, 11, 1);
  haze.position.z = -3;
  rig.add(haze);

  // Arc-reactor housing: real, lit metal with glowing coils.
  const reactor = new THREE.Group();
  rig.add(reactor);
  const metal = new THREE.MeshStandardMaterial({ color: 0x1a2a33, metalness: 0.95, roughness: 0.26, envMapIntensity: 1.4 });
  reactor.add(new THREE.Mesh(new THREE.TorusGeometry(1.32, 0.065, 24, 160), metal));
  reactor.add(new THREE.Mesh(new THREE.TorusGeometry(0.74, 0.035, 16, 120), metal));
  const coils = [];
  for (let i = 0; i < 10; i++) {
    const a0 = (i / 10) * Math.PI * 2 + 0.06, a1 = ((i + 1) / 10) * Math.PI * 2 - 0.06;
    const shape = new THREE.Shape();
    shape.absarc(0, 0, 1.2, a0, a1, false);
    shape.absarc(0, 0, 0.82, a1, a0, true);
    const geo = new THREE.ExtrudeGeometry(shape, { depth: 0.08, bevelEnabled: true, bevelThickness: 0.014, bevelSize: 0.012, bevelSegments: 2, curveSegments: 10 });
    geo.translate(0, 0, -0.04);
    const mat = new THREE.MeshStandardMaterial({ color: 0x0b1820, emissive: color.clone(), emissiveIntensity: 1.0, metalness: 0.4, roughness: 0.35 });
    const coil = new THREE.Mesh(geo, mat);
    coils.push(coil);
    reactor.add(coil);
  }

  // Gyroscope rings, each tilted differently, with a bright "satellite" riding on it.
  const gyros = [];
  [[2.05, 0.011, 1.25, 0.0], [2.32, 0.009, 0.4, 1.15], [2.6, 0.007, 2.2, -0.6]].forEach(([r, tube, tx, ty], i) => {
    const holder = new THREE.Group();
    holder.rotation.set(tx, ty, 0);
    const ring = new THREE.Mesh(new THREE.TorusGeometry(r, tube, 8, 320), additive(color.clone(), 0.6));
    const sat = new THREE.Mesh(new THREE.SphereGeometry(0.035, 16, 8), new THREE.MeshBasicMaterial({ color: new THREE.Color(2.5, 2.5, 2.5), toneMapped: false }));
    sat.position.x = r;
    ring.add(sat);
    holder.add(ring);
    rig.add(holder);
    gyros.push({ holder, ring, speed: [0.35, -0.27, 0.19][i] });
  });

  // Flat holographic dials, slightly tilted like a projected plate.
  const plate = new THREE.Group();
  plate.rotation.x = -0.14;
  rig.add(plate);
  const ticks = new THREE.InstancedMesh(new THREE.BoxGeometry(0.012, 0.1, 0.001), additive(color.clone(), 0.7), 180);
  {
    const m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3(), z = new THREE.Vector3(0, 0, 1);
    for (let i = 0; i < 180; i++) {
      const a = (i / 180) * Math.PI * 2, long = i % 10 === 0;
      p.set(Math.cos(a) * 3.3, Math.sin(a) * 3.3, 0);
      q.setFromAxisAngle(z, a - Math.PI / 2);
      s.set(long ? 2 : 1, long ? 2.2 : 1, 1);
      ticks.setMatrixAt(i, m.compose(p, q, s));
    }
    ticks.instanceMatrix.needsUpdate = true;
  }
  plate.add(ticks);
  const arcs = new THREE.Group();
  [[0.0, 1.1], [1.35, 0.55], [2.2, 1.4], [3.9, 0.9], [5.0, 0.95]].forEach(([start, len]) => {
    arcs.add(new THREE.Mesh(new THREE.RingGeometry(2.9, 2.97, 96, 1, start, len), additive(color.clone(), 0.75)));
  });
  plate.add(arcs);
  const outerRing = new THREE.Mesh(new THREE.RingGeometry(3.62, 3.635, 256), additive(color.clone(), 0.35));
  plate.add(outerRing);
  const dashUniforms = { uColor: { value: color.clone() }, uTime: { value: 0 } };
  const dashed = new THREE.Mesh(
    new THREE.RingGeometry(2.74, 2.765, 256),
    new THREE.ShaderMaterial({
      uniforms: dashUniforms, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide,
      vertexShader: 'varying vec2 vP; void main(){ vP = position.xy; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
      fragmentShader: `uniform vec3 uColor; uniform float uTime; varying vec2 vP;
        void main(){ float a = atan(vP.y, vP.x); float d = step(0.55, fract(a * 22.0 + uTime * 0.6)); gl_FragColor = vec4(uColor * 1.4, d * 0.8); }`,
    }),
  );
  plate.add(dashed);
  let bootArc = null, bootShown = -1;
  const warning = new THREE.Mesh(new THREE.RingGeometry(3.38, 3.47, 192), additive(new THREE.Color(1.6, 0.3, 0.2), 0));
  plate.add(warning);

  // Audio waveform ribbon around the core.
  const WAVE = 192;
  const waveGeo = new THREE.BufferGeometry();
  const wavePos = new Float32Array((WAVE + 1) * 2 * 3);
  const waveIdx = [];
  for (let i = 0; i < WAVE; i++) {
    const a = i * 2, b = i * 2 + 1, c = i * 2 + 2, d = i * 2 + 3;
    waveIdx.push(a, b, c, b, d, c);
  }
  waveGeo.setAttribute('position', new THREE.BufferAttribute(wavePos, 3));
  waveGeo.setIndex(waveIdx);
  const wave = new THREE.Mesh(waveGeo, additive(color.clone(), 0.9));
  plate.add(wave);

  // Particle accretion disk + distant dust.
  const MAX_PARTICLES = 7000;
  const pg = new THREE.BufferGeometry();
  const attrs = { aRadius: [], aAngle: [], aHeight: [], aSpeed: [], aSize: [], aSeed: [] };
  for (let i = 0; i < MAX_PARTICLES; i++) {
    const dust = i % 5 === 0;
    const r = dust ? 6 + Math.random() * 9 : 1.6 + Math.pow(Math.random(), 1.6) * 4.6;
    attrs.aRadius.push(r);
    attrs.aAngle.push(Math.random() * Math.PI * 2);
    attrs.aHeight.push(dust ? (Math.random() - 0.5) * 9 : (Math.random() - 0.5) * 0.18 * (1 + r * 0.15));
    attrs.aSpeed.push((dust ? 0.02 : 0.45 / Math.sqrt(r)) * (0.7 + Math.random() * 0.6));
    attrs.aSize.push(dust ? 0.6 + Math.random() * 1.2 : 0.8 + Math.random() * 1.8);
    attrs.aSeed.push(Math.random());
  }
  pg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(MAX_PARTICLES * 3), 3));
  for (const [k, v] of Object.entries(attrs)) pg.setAttribute(k, new THREE.BufferAttribute(new Float32Array(v), 1));
  pg.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 20);
  const particleUniforms = {
    uTime: { value: 0 }, uSwirl: { value: 0.3 }, uPull: { value: 0 }, uLevel: { value: 0 },
    uPixelRatio: { value: 1 }, uBoot: { value: 0 }, uColor: { value: color.clone() },
  };
  const particles = new THREE.Points(pg, new THREE.ShaderMaterial({
    uniforms: particleUniforms, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
    vertexShader: /* glsl */`
      attribute float aRadius; attribute float aAngle; attribute float aHeight; attribute float aSpeed; attribute float aSize; attribute float aSeed;
      uniform float uTime; uniform float uSwirl; uniform float uPull; uniform float uLevel; uniform float uPixelRatio; uniform float uBoot;
      varying float vAlpha;
      void main() {
        float ang = aAngle + uTime * aSpeed * (0.25 + uSwirl);
        float cyc = fract(uTime * 0.18 + aSeed);
        float disk = step(aRadius, 6.5);
        float r = aRadius * mix(1.0, 1.0 - uPull * 0.35 * cyc, disk) * mix(0.35, 1.0, uBoot);
        float y = aHeight + sin(uTime * 0.7 + aSeed * 6.283) * 0.06 + disk * uLevel * sin(ang * 6.0 + uTime * 4.0) * 0.14;
        vec4 mv = modelViewMatrix * vec4(cos(ang) * r, y, sin(ang) * r, 1.0);
        gl_Position = projectionMatrix * mv;
        gl_PointSize = aSize * uPixelRatio * (1.0 + uLevel * 0.8 * disk) * (24.0 / -mv.z);
        float twinkle = 0.35 + 0.65 * fract(aSeed * 13.7 + uTime * 0.3 * (0.5 + aSeed));
        float flow = uPull > 0.0 ? 1.0 - cyc * 0.6 * disk : 1.0;
        vAlpha = twinkle * flow * uBoot * mix(0.35, 1.0, disk);
      }`,
    fragmentShader: /* glsl */`
      uniform vec3 uColor; varying float vAlpha;
      void main() {
        float m = smoothstep(0.5, 0.0, length(gl_PointCoord - 0.5));
        gl_FragColor = vec4(uColor * 0.9, m * vAlpha * 0.55);
      }`,
  }));
  const disk = new THREE.Group();
  disk.rotation.x = 0.42;
  disk.add(particles);
  rig.add(disk);

  // Holographic floor with sonar pulses, an emitter pad and a projector beam.
  const FLOOR_Y = -3.9;
  const floorUniforms = { uColor: { value: color.clone() }, uTime: { value: 0 }, uPulses: { value: [-100, -100, -100, -100] }, uOpacity: { value: 0 } };
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(80, 80), new THREE.ShaderMaterial({
    uniforms: floorUniforms, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
    vertexShader: 'varying vec3 vW; void main(){ vec4 w = modelMatrix * vec4(position, 1.0); vW = w.xyz; gl_Position = projectionMatrix * viewMatrix * w; }',
    fragmentShader: /* glsl */`
      uniform vec3 uColor; uniform float uTime; uniform float uPulses[4]; uniform float uOpacity;
      varying vec3 vW;
      float grid(vec2 p, float s) { vec2 q = p * s; vec2 g = abs(fract(q - 0.5) - 0.5) / fwidth(q); return 1.0 - min(min(g.x, g.y), 1.0); }
      void main() {
        vec2 p = vW.xz;
        float d = length(p);
        float lines = grid(p, 1.0) * 0.28 + grid(p, 0.2) * 0.7;
        float fade = smoothstep(30.0, 2.5, d);
        float pulse = 0.0;
        for (int i = 0; i < 4; i++) {
          float age = uTime - uPulses[i];
          if (age > 0.0 && age < 4.0) pulse += smoothstep(0.7, 0.0, abs(d - age * 6.5)) * (1.0 - age / 4.0);
        }
        float pad = smoothstep(2.3, 1.9, d) * 0.12 + smoothstep(0.07, 0.0, abs(d - 2.05)) * 0.9 + smoothstep(0.04, 0.0, abs(d - 1.45)) * 0.6;
        float a = (lines * fade + pad) * uOpacity + pulse * fade * 0.9;
        gl_FragColor = vec4(uColor * 1.3, a);
      }`,
  }));
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = FLOOR_Y;
  scene.add(floor);
  const padRings = new THREE.Group();
  padRings.position.y = FLOOR_Y + 0.02;
  padRings.rotation.x = -Math.PI / 2;
  [[1.7, 1.75, 0, 4.2], [2.3, 2.34, 1.0, 2.6], [1.05, 1.1, 2.0, 5.0]].forEach(([r0, r1, s, l]) => padRings.add(new THREE.Mesh(new THREE.RingGeometry(r0, r1, 128, 1, s, l), additive(color.clone(), 0.8))));
  scene.add(padRings);
  const beamHeight = rig.position.y - FLOOR_Y;
  const beamUniforms = { uColor: { value: color.clone() }, uTime: { value: 0 }, uIntensity: { value: 0.5 } };
  const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.62, 2.0, beamHeight, 96, 1, true), new THREE.ShaderMaterial({
    uniforms: beamUniforms, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide,
    vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: /* glsl */`
      uniform vec3 uColor; uniform float uTime; uniform float uIntensity; varying vec2 vUv;
      void main() {
        float streak = 0.55 + 0.45 * sin(vUv.x * 90.0 + uTime * 2.0) * sin(vUv.x * 23.0 - uTime * 1.3);
        float a = (1.0 - vUv.y) * 0.12 * streak * uIntensity * smoothstep(0.0, 0.1, vUv.y);
        gl_FragColor = vec4(uColor * 1.2, a);
      }`,
  }));
  beam.position.y = FLOOR_Y + beamHeight / 2;
  scene.add(beam);

  // ------------------------------------------------------------- post-processing
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(512, 512), 0.7, 0.45, 0.32);
  composer.addPass(bloom);
  const lens = new ShaderPass({
    uniforms: { tDiffuse: { value: null }, uTime: { value: 0 }, uResolution: { value: new THREE.Vector2(1, 1) }, uAberration: { value: 0.006 }, uGrain: { value: 0.035 }, uVignette: { value: 0.7 } },
    vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: /* glsl */`
      uniform sampler2D tDiffuse; uniform float uTime; uniform vec2 uResolution; uniform float uAberration; uniform float uGrain; uniform float uVignette;
      varying vec2 vUv;
      float rand(vec2 co) { return fract(sin(dot(co, vec2(12.9898, 78.233))) * 43758.5453); }
      void main() {
        vec2 c = vUv - 0.5; float d = length(c);
        vec2 off = c * uAberration * d;
        vec3 col = vec3(texture2D(tDiffuse, vUv + off).r, texture2D(tDiffuse, vUv).g, texture2D(tDiffuse, vUv - off).b);
        col *= 0.975 + 0.025 * sin(vUv.y * uResolution.y * 1.3 + uTime * 18.0);
        col *= 1.0 - uVignette * smoothstep(0.32, 0.95, d);
        col += (rand(vUv * uResolution + fract(uTime)) - 0.5) * uGrain;
        gl_FragColor = vec4(max(col, 0.0), 1.0);
      }`,
  });
  composer.addPass(lens);
  composer.addPass(new OutputPass());

  // ---------------------------------------------------------------- sizing
  let quality = null, particleCount = MAX_PARTICLES, baseDistance = 16.5;
  function applyQuality() {
    quality = S.quality === 'lite' ? 'lite' : 'full';
    const dpr = window.devicePixelRatio || 1;
    const ratio = quality === 'lite' ? 1 : Math.min(dpr, 1.6);
    renderer.setPixelRatio(ratio);
    composer.setPixelRatio(ratio);
    particleUniforms.uPixelRatio.value = ratio;
    particleCount = quality === 'lite' ? 2200 : MAX_PARTICLES;
    pg.setDrawRange(0, particleCount);
    bloom.strength = quality === 'lite' ? 0.55 : 0.7;
    lens.uniforms.uGrain.value = quality === 'lite' ? 0.0 : 0.035;
    resize();
  }
  function resize() {
    const w = canvas.clientWidth || window.innerWidth, h = canvas.clientHeight || window.innerHeight;
    renderer.setSize(w, h, false);
    composer.setSize(w, h);
    camera.aspect = w / h;
    lens.uniforms.uResolution.value.set(w, h);
    viewKey = '';
    fitToStage();
  }
  window.addEventListener('resize', resize);

  // hud.js tells us where the free space between the panels is (S.view, in pixels). Aim the
  // camera so the reactor sits in the middle of that space, and pull back until it fits.
  let viewKey = '';
  function fitToStage() {
    const W = canvas.clientWidth || window.innerWidth, H = canvas.clientHeight || window.innerHeight;
    const v = S.view;
    const key = v ? `${W}x${H}:${v.x | 0},${v.y | 0},${v.w | 0},${v.h | 0}` : `${W}x${H}`;
    if (key === viewKey) return;
    viewKey = key;
    if (!v || v.w < 50 || v.h < 50) {
      camera.clearViewOffset();
      baseDistance = 16.5 * Math.max(1, 1.3 / camera.aspect);
    } else {
      camera.setViewOffset(W, H, W / 2 - v.x, H / 2 - v.y, W, H);
      // The reactor is ~3.9 units across from the middle; keep it inside the free space.
      baseDistance = Math.max(16.5, (12.0 * H) / v.h, (10.9 * H) / v.w);
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
    return raycaster.intersectObjects([core, ...coils], false).length > 0;
  }
  window.addEventListener('pointermove', (ev) => {
    pointer.tx = (ev.clientX / window.innerWidth - 0.5) * 2;
    pointer.ty = (ev.clientY / window.innerHeight - 0.5) * 2;
    if (ev.target === canvas) canvas.style.cursor = hitsCore(ev) ? 'pointer' : 'default';
  });
  canvas.addEventListener('click', (ev) => { if (hitsCore(ev)) window.dispatchEvent(new CustomEvent('jarvis-core-click')); });

  // ------------------------------------------------------------------ animation
  const target = { color: new THREE.Color(), energy: 0.5, spin: 1, swirl: 0.3, pull: 0, beam: 0.4 };
  const live = { energy: 0.5, spin: 1, swirl: 0.3, pull: 0, beam: 0.4, level: 0, boot: 0 };
  let pulseIndex = 0, lastPulse = -10, lastState = null, lastFrame = 0, t0 = performance.now();
  const angles = { ticks: 0, arcs: 0, reactor: 0 };
  const glow = new THREE.Color();
  const pulse = (time) => {
    floorUniforms.uPulses.value[pulseIndex] = time;
    pulseIndex = (pulseIndex + 1) % 4;
    lastPulse = time;
  };

  function frame(now) {
    requestAnimationFrame(frame);
    if (S.quality !== quality) applyQuality();
    // Give the graphics chip to speech recognition while it works; save power in "lite" mode.
    const minGap = S.state === 'transcribing' ? 125 : quality === 'lite' ? 33 : 0;
    if (now - lastFrame < minGap) return;
    const dt = Math.min(0.1, (now - (lastFrame || now)) / 1000);
    lastFrame = now;
    const time = (now - t0) / 1000;
    const motion = reduceMotion ? 0.2 : 1;

    const look = LOOKS[S.state] || LOOKS.standby;
    if (S.state !== lastState) {
      if (['listening', 'speaking', 'confirm'].includes(S.state) || (lastState === 'boot' && S.state !== 'boot')) pulse(time);
      lastState = S.state;
    }
    target.color.setRGB(...look.color);
    const k = Math.min(1, dt * 3);
    color.lerp(target.color, k);
    live.energy += (look.energy - live.energy) * k;
    live.spin += (look.spin - live.spin) * k;
    live.swirl += (look.swirl - live.swirl) * k;
    live.pull += (look.pull - live.pull) * k;
    live.beam += (look.beam - live.beam) * k;
    live.boot += ((S.state === 'boot' ? Math.max(0.05, S.boot || 0) : 1) - live.boot) * Math.min(1, dt * 1.5);
    const raw = S.state === 'speaking' ? S.levels.out : (S.state === 'listening' || S.state === 'confirm') ? S.levels.mic : 0;
    const level = Math.min(1, Math.sqrt(Math.max(0, raw || 0)) * 2.6);
    live.level += (level - live.level) * Math.min(1, dt * (level > live.level ? 16 : 5));
    const L = live.level;
    if (S.state === 'speaking' && L > 0.55 && time - lastPulse > 0.7 && !reduceMotion) pulse(time);

    // colours everywhere
    glow.copy(color).multiplyScalar(1.6);
    for (const m of [halo.material, haze.material]) m.color.copy(color);
    for (const u of [coreUniforms, dashUniforms, particleUniforms, floorUniforms, beamUniforms]) u.uColor.value.copy(color);
    for (const g of gyros) g.ring.material.color.copy(glow);
    ticks.material.color.copy(glow);
    outerRing.material.color.copy(color);
    wave.material.color.copy(glow);
    for (const ch of arcs.children) ch.material.color.copy(glow);
    for (const ch of padRings.children) ch.material.color.copy(glow);
    coreLight.color.copy(color);
    coreLight.intensity = 6 + 12 * live.energy + 12 * L;

    // core
    coreUniforms.uTime.value = time;
    coreUniforms.uEnergy.value = live.energy;
    coreUniforms.uLevel.value = L;
    const breathe = 0.5 + 0.5 * Math.sin(time * (S.state === 'standby' ? 1.1 : 2.3));
    core.scale.setScalar((0.94 + 0.06 * breathe + 0.12 * L) * (0.4 + 0.6 * live.boot));
    halo.material.opacity = (0.16 + 0.14 * live.energy + 0.14 * L) * live.boot;
    hot.scale.setScalar(0.8 + 0.6 * L + 0.15 * breathe);

    // coils: a wave of light runs around the reactor, faster when busy
    coils.forEach((c, i) => {
      const wave = 0.5 + 0.5 * Math.sin(time * (1.5 + live.spin) - i * 0.63);
      c.material.emissive.copy(color);
      c.material.emissiveIntensity = (0.35 + 0.9 * live.energy * wave + 0.6 * L) * live.boot;
    });
    angles.reactor -= dt * 0.12 * live.spin * motion;
    reactor.rotation.z = angles.reactor;

    // rings
    gyros.forEach((g, i) => {
      g.ring.rotation.z += dt * g.speed * live.spin * motion;
      g.holder.rotation.y += dt * 0.05 * (i % 2 ? -1 : 1) * live.spin * motion;
      const s = Math.min(1, Math.max(0, live.boot * 1.6 - i * 0.2));
      g.holder.scale.setScalar(0.6 + 0.4 * s);
      g.ring.material.opacity = 0.6 * s;
    });
    angles.ticks += dt * 0.03 * live.spin * motion;
    angles.arcs -= dt * 0.11 * live.spin * motion;
    ticks.rotation.z = angles.ticks;
    arcs.rotation.z = angles.arcs;
    dashUniforms.uTime.value = time * live.spin * motion;
    plate.scale.setScalar(0.85 + 0.15 * live.boot);
    ticks.material.opacity = 0.5 * live.boot;
    padRings.children.forEach((r, i) => { r.rotation.z += dt * (0.2 + i * 0.12) * (i % 2 ? -1 : 1) * live.spin * motion; });

    // boot progress arc
    const bootStep = S.state === 'boot' ? Math.round((S.boot || 0) * 40) / 40 : 1;
    if (bootStep !== bootShown) {
      if (bootArc) { plate.remove(bootArc); bootArc.geometry.dispose(); bootArc = null; }
      if (S.state === 'boot') {
        bootArc = new THREE.Mesh(new THREE.RingGeometry(3.13, 3.18, 160, 1, Math.PI / 2, -Math.max(0.02, bootStep) * Math.PI * 2), additive(new THREE.Color(2, 2, 2), 0.9));
        plate.add(bootArc);
      }
      bootShown = bootStep;
    }
    warning.material.opacity = S.state === 'confirm' ? 0.35 + 0.5 * breathe : Math.max(0, warning.material.opacity - dt * 2);

    // audio waveform
    const p = waveGeo.attributes.position.array;
    for (let i = 0; i <= WAVE; i++) {
      const a = (i / WAVE) * Math.PI * 2;
      const spectrum = 0.5 + 0.5 * Math.sin(i * 0.31 + time * 7.0) * Math.sin(i * 0.17 - time * 3.3);
      const amp = (0.02 + L * (0.12 + 0.88 * spectrum)) * (S.state === 'standby' ? 0.4 + 0.3 * breathe : 1);
      const r0 = 1.5, r1 = 1.53 + amp * 0.6;
      p[i * 6] = Math.cos(a) * r0; p[i * 6 + 1] = Math.sin(a) * r0; p[i * 6 + 2] = 0;
      p[i * 6 + 3] = Math.cos(a) * r1; p[i * 6 + 4] = Math.sin(a) * r1; p[i * 6 + 5] = 0;
    }
    waveGeo.attributes.position.needsUpdate = true;
    wave.material.opacity = (0.35 + 0.5 * Math.min(1, L * 1.5)) * live.boot;

    // particles, floor and beam
    particleUniforms.uTime.value = time * motion;
    particleUniforms.uSwirl.value = live.swirl;
    particleUniforms.uPull.value = live.pull;
    particleUniforms.uLevel.value = L;
    particleUniforms.uBoot.value = live.boot;
    disk.rotation.y += dt * 0.02 * motion;
    floorUniforms.uTime.value = time;
    floorUniforms.uOpacity.value = 0.42 * live.boot;
    beamUniforms.uTime.value = time;
    beamUniforms.uIntensity.value = live.beam * live.boot * (1 + 0.6 * L);

    // camera drifts gently and follows the mouse a little
    fitToStage();
    pointer.x += (pointer.tx - pointer.x) * Math.min(1, dt * 2);
    pointer.y += (pointer.ty - pointer.y) * Math.min(1, dt * 2);
    const sway = reduceMotion ? 0 : 1;
    camera.position.x = pointer.x * 1.1 * sway + Math.sin(time * 0.11) * 0.3 * sway;
    camera.position.y = 0.9 - pointer.y * 0.7 * sway + Math.sin(time * 0.07) * 0.2 * sway;
    camera.position.z = baseDistance;
    camera.lookAt(0, -0.35, 0);

    lens.uniforms.uTime.value = time;
    composer.render(dt);
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
