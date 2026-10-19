# Handoff 2: pool-2 prep successor → next session (living file; first written Sun 10/18 5:51 PM PT)

- **Ticket / goal:** pool test 2 prep for Wed 10/21. Card `c_pool1016_video_review` (keep it current, pinned
  `if_version`, Pacific AM/PM from `date` run in the same step). Spec: `~/NereusData/ops/pe_specs/SPEC_pool2_prep.md`;
  rig main has rev 9 (`aacf8d28`). **Rev 12** (W1–W5, binding per PE 003, sha `760aab8783dc`, readings pinned) is in QE:
  commit it byte-identical as `docs/pool2_20261021/SPEC.md` only after its QE PASS.
- **PRs (rig repo):**
  - #125 `study/pool2-prep-qe-fixes` @ a9ac2af: QE 005's R1–R8 + QE 006's R1/R2 fixes + TEST_CARD_B rev 2.
    **QE 006 PASS @ a9ac2af**, "no more pushes" (Sun 9:55 AM). PjM merges Mon AM; check the merged tree == a9ac2af's.
  - #126 `study/pool2-wed-program` @ 49cb3f8 (draft, stacked on #125): Wed dive-program per-phase args (PE 003's 4
    conditions in the PR body), arg checks up front, probe chroma + `--probe-min-fps` (rev 12 F15). Queued for QE 007
    (QE 006 rotated). If QE 007 hasn't started by Mon 3 PM, tell the PjM. After #125 merges, retarget #126's base to main.
  - `study/pool2-w-scoring` @ 0acdeae (pushed, no PR yet, stacked on #126): `edge_ml/pool2/` = `sfr.py` (MTF50 + 10–90 %
    width; analytic-edge test), `wscore.py` (W1 verdict with PE 003's pinned readings, W2, F10, F12, N6-vs-AE3),
    `pose.py` (tag25h9 range from bm #184's intrinsics, scaled per mode). Tests `tests/unit/test_pool2_{sfr,wscore,pose}.py`.
- **Still to build for W1–W5 (code + QE before any W result):**
  1. frame extraction per camera with capture times (reuse `matched_frames.board_frames` / `imx_frames`; IMX708 frames
     from the .h264 by index);
  2. the blind reference-box tool (boxes for each urchin sample per selected frame, drawn and committed BEFORE the
     detector runs, sha in RESULTS);
  3. the detector: `edge_ml/urchin/owl.py` (OWLv2, "purple sea urchin"), checkpoint + threshold from rig #123's merged
     pilot config (#123 is still OPEN). These are values on the Wed card;
  4. W3 CIEDE2000 per V3 patch (`color.metrics.delta_e2000`, card YAML via `color.card.load_card`, patch boxes via the tag
     homography). The c1 YAML truth is still `design_…_unmeasured`: check for the measured block (PR #93) and use
     measured over design;
  5. the W5 blur width vs speed table, and a driver writing RESULTS tables.
- **nereus002:**
  - I HOLD it for B0 under the rig session's written hand-over (Sun ~5:25 PM PT, ends at my hand-back message, Mon 8:00 AM
    PT at the latest). Acked.
  - Terms: only the a9ac2af tree (git archive, no --delete) + REVISION; no firmware change; at most one cold boot, only if
    throttled ≠ 0x0.
  - **Nothing has touched the unit.** My ssh has waited on Nick's Tailscale check since 5:22 PM; the link went to PjM 004,
    then PjM 005 (5:44 PM). Never open it yourself.
  - B0 commands: `~/NereusData/nereus-camera-test-rig/2026-10-17_pool2_prep/bench/RUNSHEET_B0.md`. The Mac build tree +
    hashes + restore list are in my session scratchpad: rebuild them with `git archive a9ac2af`.
  - If the slot lapses without B0: hand back by message (flag untouched, REVISION still 56d49b5), and ask the PjM for a
    Mon AM slot ahead of the ladder.
- **Part B/C next:** B0 (ctl_1, ae3_420, E1, resync check, chroma pair, d56 evidence) → ladder Mon → Tue noon.
  - Add the N6 4:4:4 probe (rev 12 F15: `n6_444_probe --under openmv_n6 --probe 640x400 --probe-min-fps 5 --
    --n6-subsampling 444 --timing`) **after it's on the card** (TEST_CARD_B rev 3: write it on #126 for QE 007).
  - 25× gate Tue PM on the final build (#125 + #126 merged).
  - Wed card by Tue 3 PM: part-B winners, the program string + its sha256 (PE condition 3), must-dos 16–18, the bench run of
    the exact Wed program (PE condition 1), the print-sheet PDF sha256 (in Nick's debrief Claude Doc; no local copy yet).
- **Gotchas:**
  - Write no clock time without running `date` in the same step. I slipped four times: each was corrected to the reader.
  - Never touch `/Volumes/Nick_R_SSD` (Nick unplugged it). The data root `~/NereusData` is on the internal disk.
  - pool2_bench exit codes: 0 holds, 1 fails, 2 usage, 3 refused, 4 throttled stop. A label is judged over every clip it
    ever had; a new build needs new labels.
- **Who:** PE 003 (spec), QE 007 (reviews; QE 006 rotated), PjM 005 (schedule, Tailscale link to Nick), the rig session
  (owns nereus002).
