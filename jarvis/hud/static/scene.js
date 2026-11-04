// J.A.R.V.I.S. holographic core, drawn in 3D with three.js (MIT licence, in vendor/three).
//
// The core is a precision instrument: hairline scales, arcs and live readouts (painted by art.js)
// printed on glass layers that sit at slightly different depths, so they shift against each other
// as the instrument tilts. Around the centre, a fine spectrum and an oscilloscope ring follow the
// voice. hud.js writes what JARVIS is doing into window.HUD_SCENE; this file only reads it and draws.
// If 3D isn't available, it says so and hud.js draws the 2D version (reactor2d.js) instead.

import * as THREE from './vendor/three/three.module.min.js';

const S = window.HUD_SCENE || (window.HUD_SCENE = { state: 'boot', levels: { mic: 0, out: 0 }, boot: 0, quality: 'full' });
window.HUD_SCENE_STARTED = true;   // three.js loaded; hud.js only falls back to 2D if we report a failure

function fail(reason) {
  window.HUD_SCENE_FAILED = String(reason);
  window.dispatchEvent(new CustomEvent('jarvis-scene-failed', { detail: String(reason) }));
}
function webgl2() {
  try { return !!document.createElement('canvas').getContext('webgl2'); } catch (e) { return false; }
}

const ADDITIVE = { transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide };
const PASS_POSITION = 'varying vec2 vP; void main() { vP = position.xy; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }';

function start() {
  const ART = window.JARVIS_ART;
  if (!ART) return fail('art.js did not load');
  const { LOOKS, LAYERS, TAU } = ART;
  const canvas = document.getElementById('scene');
  if (!canvas || !webgl2()) return fail('WebGL2 not available');

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  renderer.setClearColor(0x020507, 1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const maxAniso = renderer.capabilities.getMaxAnisotropy();
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 1, 0.05, 100);
  const rig = new THREE.Group();          // the whole instrument; it tilts like a projection
  scene.add(rig);

  // Every part takes the current state's colour, eased smoothly from one state to the next.
  const tint = new THREE.Color();
  const lookColor = (c, look) => c.setRGB(look.color[0], look.color[1], look.color[2], THREE.SRGBColorSpace);
  lookColor(tint, LOOKS.boot);
  const tinted = [];
  const follow = (material) => { tinted.push(material); return material; };
  const flat = (opacity = 1) => follow(new THREE.MeshBasicMaterial({ ...ADDITIVE, color: tint.clone(), opacity }));
  // A ring that only draws from `start` for `length` radians (clockwise from 12 o'clock), optionally dashed.
  function arcMaterial(start = 0, length = TAU, dashes = 0) {
    return follow(new THREE.ShaderMaterial({
      ...ADDITIVE,
      uniforms: { uColor: { value: tint.clone() }, uOpacity: { value: 0 }, uStart: { value: start }, uLength: { value: length }, uDashes: { value: dashes } },
      vertexShader: PASS_POSITION,
      fragmentShader: /* glsl */`
        uniform vec3 uColor; uniform float uOpacity; uniform float uStart; uniform float uLength; uniform float uDashes;
        varying vec2 vP;
        void main() {
          float a = mod(atan(vP.x, vP.y) - uStart, 6.2831853);
          if (a > uLength) discard;
          if (uDashes > 0.0 && fract(a / 6.2831853 * uDashes) > 0.55) discard;
          gl_FragColor = vec4(uColor, uOpacity);
          #include <colorspace_fragment>
        }`,
    }));
  }
  const setOpacity = (m, v) => { if (m.uniforms) m.uniforms.uOpacity.value = v; else m.opacity = v; };

  // ---------------------------------------------------------------- the glass layers
  const layers = LAYERS.map((spec) => {
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(spec.radius * 2, spec.radius * 2), flat(0));
    mesh.position.z = spec.z;
    rig.add(mesh);
    return { spec, mesh, canvas: null, texture: null, angle: spec.spin ? Math.random() * TAU : 0 };
  });
  function texture(cv) {
    const t = new THREE.CanvasTexture(cv);
    t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = maxAniso;
    return t;
  }
  let artFactor = 0, readoutsKey = '';
  function paintLayers(factor) {           // (re)paint every layer at this resolution
    artFactor = factor;
    readoutsKey = JSON.stringify(S.readouts || {});
    layers.forEach((l) => {
      l.canvas = ART.paint(l.spec, S.readouts, null, factor);
      if (l.texture) l.texture.dispose();
      l.texture = texture(l.canvas);
      l.mesh.material.map = l.texture;
      l.mesh.material.needsUpdate = true;
    });
  }
  function refreshReadouts() {             // repaint the live readouts when their values change
    const key = JSON.stringify(S.readouts || {});
    if (key === readoutsKey) return;
    readoutsKey = key;
    layers.forEach((l) => {
      if (!l.spec.live) return;
      ART.paint(l.spec, S.readouts, l.canvas, artFactor);   // same size, so the texture can be refreshed in place
      l.texture.needsUpdate = true;
    });
  }

  // ---------------------------------------------------------------- live parts
  // Fine audio spectrum: 160 hairline bars around the centre that grow with the voice.
  const BARS = 160;
  const barGeometry = new THREE.PlaneGeometry(1, 1);
  barGeometry.translate(0, 0.5, 0);
  const spectrum = new THREE.InstancedMesh(barGeometry, flat(0), BARS);
  spectrum.frustumCulled = false;
  spectrum.position.z = 0.16;
  rig.add(spectrum);
  const dummy = new THREE.Object3D();

  // Oscilloscope: a thin ring that ripples with the sound.
  const SCOPE = 256;
  const scopeGeometry = new THREE.BufferGeometry();
  scopeGeometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(SCOPE * 2 * 3), 3));
  const scopeIndex = [];
  for (let i = 0; i < SCOPE; i++) {
    const j = (i + 1) % SCOPE;
    scopeIndex.push(i * 2, i * 2 + 1, j * 2, j * 2, i * 2 + 1, j * 2 + 1);
  }
  scopeGeometry.setIndex(scopeIndex);
  const scope = new THREE.Mesh(scopeGeometry, flat(0));
  scope.frustumCulled = false;
  scope.position.z = 0.18;
  rig.add(scope);

  // Radar sweep with a soft fading trail, used while JARVIS works.
  const sweepMaterial = follow(new THREE.ShaderMaterial({
    ...ADDITIVE,
    uniforms: { uColor: { value: tint.clone() }, uOpacity: { value: 0 }, uHead: { value: 0 } },
    vertexShader: PASS_POSITION,
    fragmentShader: /* glsl */`
      uniform vec3 uColor; uniform float uOpacity; uniform float uHead; varying vec2 vP;
      void main() {
        float behind = mod(uHead - atan(vP.x, vP.y), 6.2831853) / 6.2831853;   // 0 at the leading edge
        float trail = pow(1.0 - behind, 12.0) * 0.5 + (1.0 - smoothstep(0.0, 0.004, behind)) * 1.4;
        gl_FragColor = vec4(uColor, trail * uOpacity);
        #include <colorspace_fragment>
      }`,
  }));
  const sweep = new THREE.Mesh(new THREE.RingGeometry(0.3, 0.75, 160, 1), sweepMaterial);
  sweep.position.z = 0.09;
  rig.add(sweep);

  const ringMesh = (inner, outer, material, z) => {
    const m = new THREE.Mesh(new THREE.RingGeometry(inner, outer, 256, 1), material);
    m.position.z = z;
    rig.add(m);
    return m;
  };
  const activity = ringMesh(0.564, 0.571, arcMaterial(0, 1), 0.1);         // chases round while working
  const bootArc = ringMesh(0.985, 0.991, arcMaterial(0, 0), 0.02);         // start-up progress
  const warn = ringMesh(0.9, 0.906, arcMaterial(0, TAU, 96), 0.05);        // authorisation: dashed ring

  // The centre: a small point of light inside two crisp rings.
  const coreMaterial = follow(new THREE.SpriteMaterial({ map: texture(ART.core(256)), color: tint.clone(), transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0 }));
  const core = new THREE.Sprite(coreMaterial);
  core.position.z = 0.2;
  rig.add(core);
  const coreRing = ringMesh(0.15, 0.1535, flat(0), 0.2);
  const coreArc = ringMesh(0.19, 0.192, arcMaterial(0.3, 5.0), 0.19);
  const hit = new THREE.Mesh(new THREE.CircleGeometry(0.34, 32), new THREE.MeshBasicMaterial({ visible: false }));
  hit.position.z = 0.2;
  rig.add(hit);

  // A little dust drifting in front of and behind the glass, for depth.
  const DUST = 360;
  const dustGeometry = new THREE.BufferGeometry();
  const dp = new Float32Array(DUST * 3), ds = new Float32Array(DUST);
  for (let i = 0; i < DUST; i++) {
    dp[i * 3] = (Math.random() - 0.5) * 7;
    dp[i * 3 + 1] = (Math.random() - 0.5) * 4.5;
    dp[i * 3 + 2] = -2.5 + Math.random() * 3.0;
    ds[i] = Math.random();
  }
  dustGeometry.setAttribute('position', new THREE.BufferAttribute(dp, 3));
  dustGeometry.setAttribute('aSeed', new THREE.BufferAttribute(ds, 1));
  const dustMaterial = follow(new THREE.ShaderMaterial({
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
    uniforms: { uColor: { value: tint.clone() }, uOpacity: { value: 1 }, uTime: { value: 0 }, uPixelRatio: { value: 1 } },
    vertexShader: /* glsl */`
      attribute float aSeed; uniform float uTime; uniform float uPixelRatio; varying float vA;
      void main() {
        vec3 p = position;
        p.x += sin(uTime * 0.05 + aSeed * 40.0) * 0.15;
        p.y = mod(position.y + uTime * 0.015 * (0.5 + aSeed) + 2.25, 4.5) - 2.25;
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        gl_Position = projectionMatrix * mv;
        gl_PointSize = (0.8 + aSeed * 1.2) * uPixelRatio * (6.0 / -mv.z);
        vA = 0.05 + 0.12 * fract(aSeed * 7.0 + uTime * 0.04);
      }`,
    fragmentShader: /* glsl */`
      uniform vec3 uColor; uniform float uOpacity; varying float vA;
      void main() {
        float m = 1.0 - smoothstep(0.0, 0.5, length(gl_PointCoord - 0.5));
        gl_FragColor = vec4(uColor, m * vA * uOpacity);
        #include <colorspace_fragment>
      }`,
  }));
  const dust = new THREE.Points(dustGeometry, dustMaterial);
  dust.frustumCulled = false;
  scene.add(dust);

  // ---------------------------------------------------------------- sizing and quality
  let quality = null, distance = 7, viewKey = '';
  function applyQuality() {
    quality = S.quality === 'lite' ? 'lite' : 'full';
    const ratio = quality === 'lite' ? 1 : Math.min(window.devicePixelRatio || 1, 2);
    renderer.setPixelRatio(ratio);
    dustMaterial.uniforms.uPixelRatio.value = ratio;
    dust.visible = quality === 'full';
    const factor = quality === 'lite' ? 0.5 : 1;
    if (factor !== artFactor) paintLayers(factor);
    resize();
  }
  function resize() {
    const w = canvas.clientWidth || window.innerWidth, h = canvas.clientHeight || window.innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    viewKey = '';
    fitToStage();
  }
  window.addEventListener('resize', resize);
  // hud.js reports the free space between the panels (S.view, in pixels). Aim the camera so the
  // instrument sits in the middle of it, and pull back until it fits.
  function fitToStage() {
    const W = canvas.clientWidth || window.innerWidth, H = canvas.clientHeight || window.innerHeight;
    const v = S.view;
    const key = v ? `${W}x${H}:${v.x | 0},${v.y | 0},${v.w | 0},${v.h | 0}` : `${W}x${H}`;
    if (key === viewKey) return;
    viewKey = key;
    const tan = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const room = 1.08;                     // the outer scale (radius 1) plus a little margin
    if (!v || v.w < 50 || v.h < 50) {
      camera.clearViewOffset();
      distance = room / (2 * tan * 0.46 * Math.min(1, camera.aspect));
    } else {
      camera.setViewOffset(W, H, W / 2 - v.x, H / 2 - v.y, W, H);
      distance = (room * H) / (2 * tan * 0.47 * Math.min(v.w, v.h));
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
  const live = { level: 0.7, spin: 0.6, sweep: 0.2, activity: 0, audio: 0, boot: 0, warn: 0 };
  let lastFrame = 0, lastReadouts = 0, sweepHead = 0, actHead = 0;
  const t0 = performance.now();

  function frame(now) {
    requestAnimationFrame(frame);
    if (S.quality !== quality) applyQuality();
    // Leave the graphics chip to speech recognition while it works; save power in Lite mode.
    const minGap = S.state === 'transcribing' ? 100 : quality === 'lite' ? 33 : 0;
    if (now - lastFrame < minGap) return;
    const dt = Math.min(0.1, (now - (lastFrame || now)) / 1000);
    lastFrame = now;
    const time = (now - t0) / 1000;
    const motion = reduceMotion ? 0.25 : 1;
    fitToStage();
    if (now - lastReadouts > 500) { lastReadouts = now; refreshReadouts(); }

    const look = LOOKS[S.state] || LOOKS.standby;
    const k = Math.min(1, dt * 2.5);
    tint.lerp(lookColor(target, look), k);
    live.level += (look.level - live.level) * k;
    live.spin += (look.spin - live.spin) * k;
    live.sweep += (look.sweep - live.sweep) * k;
    live.activity += (look.activity - live.activity) * Math.min(1, dt * 4);
    live.boot += ((S.state === 'boot' ? Math.max(0.08, S.boot || 0) : 1) - live.boot) * Math.min(1, dt * 1.5);
    live.warn += ((S.state === 'confirm' ? 1 : 0) - live.warn) * Math.min(1, dt * 4);
    const raw = S.state === 'speaking' ? S.levels.out : (S.state === 'listening' || S.state === 'confirm') ? S.levels.mic : 0;
    const audio = Math.min(1, Math.sqrt(Math.max(0, raw || 0)) * 2.4);
    live.audio += (audio - live.audio) * Math.min(1, dt * (audio > live.audio ? 14 : 4));
    const A = live.audio;
    const bright = live.level * (0.35 + 0.65 * live.boot);
    tinted.forEach((m) => { if (m.uniforms) m.uniforms.uColor.value.copy(tint); else m.color.copy(tint); });

    // glass layers: each turns at its own slow speed and appears in turn while starting up
    layers.forEach((l, i) => {
      l.angle += dt * 0.035 * l.spec.spin * live.spin * motion;
      l.mesh.rotation.z = -l.angle;
      const reveal = Math.min(1, Math.max(0, live.boot * 1.6 - i * 0.12));
      l.mesh.material.opacity = l.spec.opacity * bright * reveal;
      l.mesh.material.color.copy(tint).multiplyScalar(1.3);   // thin lines lose a little light when scaled down
      l.mesh.scale.setScalar(0.97 + 0.03 * reveal);
    });

    // spectrum
    for (let i = 0; i < BARS; i++) {
      const a = (i / BARS) * TAU;
      const n = 0.5 + 0.5 * Math.sin(i * 0.37 + time * 6.0) * Math.sin(i * 0.11 - time * 2.7);
      const idle = S.state === 'standby' ? 0.004 * (0.5 + 0.5 * Math.sin(time * 1.3 + i * 0.7)) : 0;
      dummy.position.set(Math.sin(a) * 0.3, Math.cos(a) * 0.3, 0);
      dummy.rotation.z = -a;
      dummy.scale.set(0.0032, 0.007 + idle + A * (0.018 + 0.09 * n * n), 1);
      dummy.updateMatrix();
      spectrum.setMatrixAt(i, dummy.matrix);
    }
    spectrum.instanceMatrix.needsUpdate = true;
    spectrum.material.opacity = (0.4 + 0.55 * Math.min(1, A * 1.4)) * bright;

    // oscilloscope
    const sp = scopeGeometry.attributes.position.array;
    const half = 0.0016;
    for (let i = 0; i < SCOPE; i++) {
      const a = (i / SCOPE) * TAU;
      const r = 0.255 + A * (0.016 * Math.sin(a * 9 + time * 9) * Math.sin(a * 3 - time * 4) + 0.006 * Math.sin(a * 23 + time * 15));
      const s = Math.sin(a), c = Math.cos(a);
      sp[i * 6] = s * (r - half); sp[i * 6 + 1] = c * (r - half); sp[i * 6 + 2] = 0;
      sp[i * 6 + 3] = s * (r + half); sp[i * 6 + 4] = c * (r + half); sp[i * 6 + 5] = 0;
    }
    scopeGeometry.attributes.position.needsUpdate = true;
    scope.material.opacity = (0.45 + 0.45 * Math.min(1, A * 1.5)) * bright;

    // sweep, working arc, start-up arc and authorisation ring
    sweepHead = (sweepHead + dt * (0.5 + 1.4 * live.sweep) * motion) % TAU;
    sweepMaterial.uniforms.uHead.value = sweepHead;
    sweepMaterial.uniforms.uOpacity.value = live.sweep * 0.5 * bright;
    actHead = (actHead + dt * 2.2 * motion) % TAU;
    activity.material.uniforms.uStart.value = actHead;
    activity.material.uniforms.uLength.value = 0.7 + 0.45 * Math.sin(time * 1.7);
    setOpacity(activity.material, live.activity * 0.9 * bright);
    bootArc.material.uniforms.uLength.value = S.state === 'boot' ? Math.max(0.02, S.boot || 0) * TAU : 0;
    setOpacity(bootArc.material, S.state === 'boot' ? 0.9 : 0);
    warn.rotation.z = -time * 0.25 * motion;
    setOpacity(warn.material, live.warn * (0.45 + 0.3 * Math.sin(time * 4)));

    // centre
    coreMaterial.opacity = (0.5 + 0.5 * A) * bright;
    core.scale.setScalar(0.36 * (1 + 0.18 * A));
    coreRing.material.opacity = 0.85 * bright;
    coreArc.rotation.z = -time * 0.35 * live.spin * motion;
    setOpacity(coreArc.material, 0.55 * bright);

    dustMaterial.uniforms.uTime.value = time * motion;

    // tilt like a projection seen from slightly below; follow the mouse a little for parallax
    pointer.x += (pointer.tx - pointer.x) * Math.min(1, dt * 2);
    pointer.y += (pointer.ty - pointer.y) * Math.min(1, dt * 2);
    const sway = reduceMotion ? 0 : 1;
    rig.rotation.x = -0.18 + (pointer.y * 0.05 + Math.sin(time * 0.13) * 0.01) * sway;
    rig.rotation.y = (pointer.x * 0.08 + Math.sin(time * 0.09) * 0.012) * sway;
    camera.position.set(0, 0, distance);
    camera.lookAt(0, 0, 0);

    renderer.render(scene, camera);
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
