# Handoff 3: pool-2 prep successor 3 → next session (living file; first written Sun 10/18 9:48 PM PT)

- **Ticket / goal:** pool test 2 prep for Wed 10/21. Card `c_pool1016_video_review` (pinned `if_version`; owner field starts
  "Nick (…)" while a Nick ask is open, per PjM 005). Spec: `~/NereusData/ops/pe_specs/SPEC_pool2_prep.md`.
  - Rev 15 (d322d4bac58b, must do 19 + F19 CMA) has QE 008 PASS-with-nits (c_qe, 8:57 PM), with N1–N3.
  - Rev 16 (db5a60958bdc, undistorted tag corners) is in QE 008's spec gate. PE 005: build to it, but report no W result
    before its spec PASS.
  - Main carries rev 13 (2e5525246709). Commit rev 16 byte-identical as `docs/pool2_20261021/SPEC.md` only after its PASS.
- **Who:** PE 005 (spec; ListAgents "Principal Engineer 005"), QE 008, PjM 005, the rig session ("nereus002: fix AE3 wedge,
  25× start/stop gate, Friday dive", owns nereus002). roles/ROLES.md on team main still names PjM 003 / PE 002 (stale).
- **Main:** #125 → fc28e37, #126 → 9b3534d (PjM 005, 9:30–9:31 PM, "on Nick's word"). Tip 4441175. I checked that #125's
  files on main equal QE-passed a9ac2af and #126's equal 227425c, and that nothing after 9b3534d touches scripts/, openmv/,
  src/ or docs/pool2_20261021/. Build from main, not from a branch.

## W1–W5 scoring: rig #133 (draft → QE 008)
- https://github.com/nickraymond/nereus-camera-test-rig/pull/133, branch `study/pool2-w-scoring` @ 5323c7f (my local branch
  is `s3/pool2-w-scoring` in worktree clever-goldwasser-83f544; successor 2's worktree hungry-hawking-7ac1a6 still has the
  branch checked out, clean). Merge main in; never rebase a pushed branch.
- `python -m edge_ml.pool2.run scan | select | (commit box file) | detect | crops | score`. The PR body maps stages to spec
  lines and lists 9 readings for QE to confirm.
- Tests: 923 passed, 5 skipped at 5323c7f (92 pool2). Hygiene PASS (team repo `tools/pr_hygiene_check.sh` from origin/main;
  the team main checkout is behind, so use `git show origin/main:tools/pr_hygiene_check.sh`).
- Envs: tests `/Users/nickbuemond/Documents/GitHub/nereus-camera-test-rig/.venv/bin/python` with `PYTHONPATH=src:.`;
  detect needs torch: `~/NereusData/nereus-camera-test-rig/_env/urchin-ml/bin/python`.
- **Dry run (real data):** `~/NereusData/nereus-camera-test-rig/2026-10-18_pool2_wscore_dryrun/` (MANIFEST.md). With raw
  corners F10 would have voided W1. With rev 16, F10 is 0.6–5.8 % on Friday's clips 099–103.
- **Open on #133:**
  1. Detector threshold: unknown. #123's pilot has 0.05 / 0.3, no single value. Asked PE 005 (REC 0.3) at ~9:42 PM.
     Checkpoint pinned: `google/owlv2-base-patch16-ensemble @ cfd3195ba4ea9592eec887ded089f4c08eff231d`.
  2. Board layout: template only. The measured `board_layout_wed.yaml` (`measured: true`) comes from Nick's ruler or a flat
     photo, before the dive.

## Must do 19 (rev 15), facts from the rig session (Sun ~9:20 PM, read-only on nereus002)
- Driver-held CMA: **unknown**. Kernel 6.18.34+rpt-rpi-v8 has CONFIG_CMA_DEBUGFS and CONFIG_CMA_SYSFS not set. So the CMA
  bar = 0 CMA/dma-buf allocation errors in dmesg only. Readable: /proc/meminfo CmaTotal (262144 kB) and CmaFree; `pi` is
  in adm, so `journalctl -k` works.
- 10/13 driver-held CMA at the resets: **not in the logs**. The only datum is CmaFree 16 kB ~3 min after the 3:02 PM reset,
  under ci-record. N1's "0.9 × reading" bar therefore doesn't apply.
- CMA raise (only if the CMA bar fails at both buffer counts): needs Nick's own words, and root (Nick's sudo). `config.txt`
  line 26 `dtoverlay=vc4-kms-v3d` → `dtoverlay=vc4-kms-v3d,cma-320`. Backup `sudo cp -p /boot/firmware/config.txt
  /boot/firmware/config.txt.bak-20261019`; restore = the reverse cp; one cold boot each. The overlay help says cma-320 "needs
  1GB"; this Pi has 512 MB. NEEDED is on the card blocker for Nick's batch (PjM 005 told).
- pool2_bench gives fps / lost / max_gap / host_stats per clip, but **no kernel-log lines and no reset evidence**. Must do 19
  needs a small checker + a test-card section. Plan: TEST_CARD_B rev 4 + `scripts/md19_check.py` (journalctl -k -o
  short-monotonic in each clip's window; boot_id + uptime before/after; all cameras' capture bars from setting.json).
  Separate PR, QE before it runs.

## Monday
- The rig session cold-boots first (flag present, `ups_power_cycle.sh`), checks that idle CmaFree recovered, then writes the
  hand-over (ladder TEST_CARD_B rev 3 + must do 19). Target start ~9 AM PT (told the rig session + PjM 005).
- Ladder per HANDOFF_2 "Next" §2: ctl_2, n6_hd n=3 (AE3 at 4:2:0 control), ae3_hd 4:2:0, 960×600 only if n6_hd fails,
  n6_444_probe, per-cycle MB. Then must do 19 at the Wed modes. Then the 25× gate Tue PM.
- Wed card by Tue 3 PM. In addition to HANDOFF_2's list, it needs:
  - fx/fy plus dist_coeffs and calib file sha (`edge_ml/pool2/pose.py` CALIB / DIST / CALIB_FILES);
  - rev 16 guard status per camera;
  - detector threshold + revision;
  - must do 19 results + CMA raise status;
  - the 1.0 m fit check (needs Nick's board outline via the PjM, plus the N6↔AE3 baseline from Friday's matched frames,
    which should now be computed with undistorted corners).

## Gotchas (mine tonight)
- Stamp no time without `date` in the same step. I wrote "~9:40 / ~9:50 PM" into the dry-run MANIFEST from a guess (PE 005
  caught it); corrected from file times.
- A bare `cat > file` with no heredoc waits on stdin forever (it hung a probe). Write scratch files with the Write tool.
- transformers 5.19 leaves `config._commit_hash` unset. `detect` reads the revision from the HF cache's `main` snapshot,
  offline.
