# hydrium, vendored for the compression study

[hydrium](https://github.com/Traneptora/hydrium) (BSD-2-Clause, `LICENSE`) by Leo Izen: a small
streaming JPEG XL (VarDCT) encoder in plain C. Vendored from upstream commit `UPSTREAM_COMMIT`
(`src/libhydrium/` + `src/include/libhydrium/libhydrium.h`); the first commit adding this folder
is upstream byte for byte, every later change is a study patch (see `git log -p` on this folder).

Study-only code: not shipped, not imported from `src/`. The Mac CLI (`../hyd.c`) and the
OpenMV native module (`../../natmod/hyd_mod.c`) compile these same files, so the board and the
Mac write identical bytes.
