# Third-Party Notices

MAPF Splice uses the following direct runtime dependency without vendoring its
source code or assets:

## Pillow

- Project: [Pillow](https://python-pillow.github.io)
- Source: [python-pillow/Pillow](https://github.com/python-pillow/Pillow)
- License expression reported by the installed package: `MIT-CMU`

Pillow retains its own copyright and license terms.

MAPF Splice vendors a minimal runtime subset of the following MIT-licensed
project:

## PyPIBT (vendored)

- Project: [pypibt](https://github.com/Kei18/pypibt) — a minimal Python
  implementation of Priority Inheritance with Backtracking (PIBT) for MAPF.
- Author: Keisuke Okumura (Kei18)
- License: MIT
- Pinned source commit: `a3c97f60413c6619a29a5022969896bc54877edc`
- Vendored location: `src/mapf_splice/_vendor/pypibt/`

Only the PIBT algorithm and the grid helpers it needs are vendored (see
`src/mapf_splice/_vendor/pypibt/PROVENANCE.md`); the upstream notebook,
Matplotlib visualizer, and benchmark tooling are not included. The vendored
code retains its original MIT license and copyright. The `recovery` optional
dependency adds only NumPy, which the vendored solver requires.
