# Vendored PyPIBT (provenance)

This directory contains a minimal, unmodified runtime subset of **pypibt**, a
Python implementation of Priority Inheritance with Backtracking (PIBT) for
Multi-Agent Path Finding.

- Upstream project: https://github.com/Kei18/pypibt
- Author: Keisuke Okumura (Kei18)
- License: MIT (see `LICENSE`, copied verbatim from upstream `LICENCE.txt`)
- Pinned source commit: `a3c97f60413c6619a29a5022969896bc54877edc`

## Why vendored instead of a dependency

Upstream declares `jupyterlab`, `matplotlib`, and `pytest` as *regular* runtime
dependencies even though the PIBT algorithm imports none of them. Depending on
`pypibt` (even as an optional Git dependency) would pull that entire
notebook/development stack into this project's dependency graph. Vendoring the
algorithm subset keeps the only real runtime requirement — NumPy — behind an
optional `recovery` extra, and pulls in no notebook/development packages.

## Files

| File | Origin | Modification |
| --- | --- | --- |
| `pibt.py` | upstream `src/pypibt/pibt.py` | verbatim |
| `dist_table.py` | upstream `src/pypibt/dist_table.py` | verbatim |
| `mapf_utils.py` | upstream `src/pypibt/mapf_utils.py` | **subset**: only the type aliases, `is_valid_coord`, and `get_neighbors` (function bodies verbatim). Benchmark loaders, the visualizer export, and the solution validators are omitted — they are unused and not part of the algorithm. |
| `LICENSE` | upstream `LICENCE.txt` | verbatim |

The PIBT algorithm itself (`pibt.py`, `dist_table.py`) is unmodified. No
algorithm changes were made.
