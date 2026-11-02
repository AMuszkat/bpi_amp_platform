---
name: render
description: Render the boards photographically in Blender from the KiCad projects, and rebuild the README's slider (the render, then the photo). `/render preview` makes a quick 1600 px picture in build/boards/ to judge a change; `/render final` makes docs/images/boards-render.jpg and docs/images/boards.svg. Use for any change to the boards' picture; normally through the render-dev agent.
---

# /render

```
tools/render.sh --quality preview --keep-export   # build/boards/boards-render-preview.png, about a minute
tools/render.sh                                   # final: docs/images/boards-render.jpg and boards.svg, about 8 minutes
tools/render.sh --blend build/boards/boards.blend # also save the scene, to open in Blender
tools/blender.sh build/boards/boards.blend        # open it (the pinned Blender)
```

What it does, in order (`hardware/render/render.py`):

1. Copies `hardware/` into `build/boards/src/` and fixes the copies' 3D model paths (KiCad's
   library ships STEP only; vendor models missing here are swapped for a library twin when
   there is one, `SUBSTITUTES`). `hardware/` itself is never opened.
2. `kicad-cli pcb export glb`: board, copper, pads, vias, mask, silkscreen and components, as
   separate meshes. `--keep-export` reuses the GLB files when only the Blender side changed.
3. `build/boards/parts.json`: the footprints with no model, and the stand-in body each gets
   (`KINDS`: WLP, QFN, PQFN, the inductor, JST VH, pin header, DF40, USB-C).
4. Blender (`hardware/render/scene.py`): materials (a clear-coated, slightly translucent mask
   over the copper, HASL pads, matte silkscreen, the FR4 edge), the stand-ins, a paper table
   with soft-box lights, a camera with depth of field, Cycles on the CPU with OpenImageDenoise,
   AgX. Where each board lies is `BOARDS` in `scene.py`.
5. After a final render, `hardware/render/slider.py` writes `docs/images/boards.svg`.

Requirements: KiCad 9 or later (`kicad-cli`) and curl; `tools/blender.sh` fetches Blender
4.5 LTS into `build/tools/` the first time (about 350 MB, SHA-256 checked), or `BLENDER=` names
another. With the vendor models (`BPI_AMP_3DMODELS`, `hardware/README.md`), parts found there
are rendered from them instead of a stand-in.

Always look at the picture before judging it. `git status` must not show a `.kicad_prl`
afterwards: if it does, something opened `hardware/` and the file is restored with
`git checkout --`.
