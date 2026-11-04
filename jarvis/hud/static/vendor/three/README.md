# three.js (bundled)

The JARVIS display draws its 3D holographic core with [three.js](https://threejs.org), a free,
open-source 3D library (MIT licence, see `LICENSE`). It's copied here so JARVIS works offline.

- **Version:** three 0.185.1 (npm), integrity
  `sha512-5aojFCXKwnjBRZvUnt3WFfEcvUJgkN5LlijRFN95hMy8WVkG4I0QNcJE+OuWvuJ0bOdStrbfXn0pkd6/QyiAlg==`
  (package tarball sha256 `a2143f5bf978bd3470a51024b2b6bdd581913ba8f36ff1538d433f3a95adf2df`).
- **Files:**
  - `three.core.min.js` and `three.module.min.js`, from `build/`;
  - `postprocessing/EffectComposer.js`, `RenderPass.js`, `UnrealBloomPass.js`, `OutputPass.js`,
    `ShaderPass.js`, `Pass.js` and `MaskPass.js`, from `examples/jsm/postprocessing/`;
  - `shaders/CopyShader.js`, `LuminosityHighPassShader.js` and `OutputShader.js`, from
    `examples/jsm/shaders/`.
- **One change:** in `postprocessing/` and `shaders/`, `from 'three'` became
  `from '../three.module.min.js'`. This lets the files load on every macOS version JARVIS supports,
  without import maps. Nothing else was changed.
