# Credits and Attribution

## Project license

This project is released under the **MIT License**. See [`LICENSE`](LICENSE).

The MIT license covers this project's original source code and documentation. It does not override the licenses of third-party components or of the research paper.

## Primary research source

This project draws its mesostructure modelling approach from:

**Seibel, S., & Kiendl, J. (2025).** *A finite element approach for modelling the fracture behaviour of unidirectional FFF-printed parts.* **Progress in Additive Manufacturing, 10**, 6981–6992. https://doi.org/10.1007/s40964-025-01021-8

- **Authors:** Simon Seibel and Josef Kiendl
- **Affiliation:** Institute of Engineering Mechanics and Structural Analysis, Universität der Bundeswehr München, Werner-Heisenberg-Weg 39, 85577 Neubiberg, Germany
- **Published:** 25 February 2025
- **Journal:** Progress in Additive Manufacturing (2025) 10:6981–6992
- **DOI:** [10.1007/s40964-025-01021-8](https://doi.org/10.1007/s40964-025-01021-8)

### Citation

```bibtex
@article{Seibel2025,
  author  = {Seibel, Simon and Kiendl, Josef},
  title   = {A finite element approach for modelling the fracture behaviour of unidirectional FFF-printed parts},
  journal = {Progress in Additive Manufacturing},
  volume  = {10},
  pages   = {6981--6992},
  year    = {2025},
  doi     = {10.1007/s40964-025-01021-8},
  url     = {https://doi.org/10.1007/s40964-025-01021-8}
}
```

## License and changes

The paper is Open Access and licensed under the **Creative Commons Attribution 4.0 International License (CC BY 4.0)**: https://creativecommons.org/licenses/by/4.0/

© 2025 The Author(s).

A local copy is included at `sources/fracture_behavior/fracture_behavior_FFF.pdf`, with the license notice at `sources/fracture_behavior/LICENSE.md`. The paper's figures and other third-party material remain subject to the paper's CC BY 4.0 terms and any notices in the paper.

**Changes made by this project:** the paper's mesostructure modelling approach is adapted and generalized for arbitrary OrcaSlicer-sliced FFF parts. This project adds a volume-conserving bead model, a parameterized contact/interface representation, and a separate treatment of contact geometry and bond quality. The authors of the paper have not endorsed this project, and any errors or limitations in this implementation are the responsibility of this project's authors.

## What this project uses from the paper

The paper is used as a modelling reference and validation target. This repository includes a local copy of the paper PDF under its CC BY 4.0 license; it does not redistribute the paper's figures as separate assets. Specific elements include:

- The octagon cross-section approximation for individual FFF strands.
- The horizontal and vertical contact lengths `c_h` and `c_v` as the geometric parameters that control interlayer and intralayer contact.
- The measured contact lengths (`c_h ≈ 0.72`, `c_v ≈ 0.36`), the initial model values (`c_h = 0.7`, `c_v = 0.2`), and the calibrated value `c_v = 0.225` for the 90° case.
- The area-scaling factor `A_rect / A_oct = 1.136` and the scaled material properties `E_sim = 3901 MPa`, `ν = 0.3`, `σ_f,sim = 45.38 MPa`.
- The Rankine maximum-normal-stress failure criterion, element deletion, and the localization strategy that deletes only a small fraction of failing elements per step.
- The validation targets: unidirectional PLA tensile specimens at 0°, 30°, 45°, 60°, and 90°, with intralayer debonding as the dominant failure mode for non-0° cases.
- The finding that strand turnarounds/edges have a small (~5%) influence and can be simplified.

The project has **not yet reproduced** the paper's benchmark results. Reproducing them is a planned validation phase; see `ROADMAP.md`.

## Reference hardware note

The paper's experiments used a Raise3D Pro2 with a 0.4 mm nozzle and PLA at 200 °C nozzle / 60 °C bed and 30 mm/s. This project's physical validation uses a **Creality Ender 3 V2 Neo**, so the paper's calibrated contact lengths and material data are treated as starting points rather than universal constants. See `AGENTS.md` and `ROADMAP.md`.

## Software and tools

This project also depends on or is developed alongside:

- **OrcaSlicer** — the slicer targeted by the plugin. https://github.com/OrcaSlicer/OrcaSlicer — AGPL-3.0-only.
- **MaxEllis OrcaSlicer fork** — the build that provides the Remote API used by the optional MCP tooling. https://github.com/MaxEllis/OrcaSlicer — AGPL-3.0-only.
- **orcaslicer-mcp** — the optional MCP server used during development. https://github.com/MaxEllis/orcaslicer-mcp — AGPL-3.0-only.

The paper's CC BY 4.0 license applies to the paper and its included material. This project's original code and documentation are MIT-licensed; see `LICENSE`. OrcaSlicer and the MaxEllis fork are AGPL-3.0-only; the plugin runs inside OrcaSlicer via its plugin API, so review AGPL obligations before distributing a bundled plugin or a modified OrcaSlicer. Audit all third-party dependency licenses before release.

## Attribution in derived work

If you reuse this project's implementation or documentation, preserve the MIT copyright and license notice (`LICENSE`) and cite the original paper:

> Seibel, S., & Kiendl, J. (2025). A finite element approach for modelling the fracture behaviour of unidirectional FFF-printed parts. *Progress in Additive Manufacturing, 10*, 6981–6992. https://doi.org/10.1007/s40964-025-01021-8
