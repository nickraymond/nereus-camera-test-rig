# Handoff: E6 urchin (Reef Check), Fri 10/16 1:55 PM PT

From session "Urchin ML: Reef Check stills + note (E6)", rotating at 53 % context (house rule ~50 %).

- **Ticket / goal:** board card `p_e6_reefcheck` (E6, goal `edge`).
  - (1) A draft status note to Reef Check by **Fri 10/23**: did turning their video into stills work, and did the stills help a purple-urchin detector? Nick sends it himself.
  - (2) Toward mid-Nov, an urchin model with P/R on held-out Reef Check frames.
  - New since 1:54 PM: the PE's **night pilot** spec, `SPEC_e6_night_pilot.md`, sha256 `69cdf13bcdde`, tier (b): "are these videos good enough to work". The successor owns it.

- **State now (done, with evidence):**
  - **Merged on main.**
    - #117: PREREG + `extract_stills.py` + `metrics.py`.
    - #118: owl/sam/review/split/train_b/evaluate + all PREREG amendments.
    - #119: extractor skips exFAT `._` files; the VideoToolbox decoder is part of the method, with the download format per video (nv12/p010le); `extract_run.json`.
    - Every PR got QE PASS.
  - **PREREG** (`edge_ml/urchin/PREREG.md`) is final for the note. Read its amendment log.
    - Groups are site + dive date, from a frozen map: `groups_reefcheck.json`, sha `b10b076d…`, 29 videos → 7 groups, Mendocino included.
    - Validation never starves model B.
    - Claim scope is limited when no new site is held out.
    - Outcome is "incomplete" unless seeds 0–2 each finish 24 epochs.
    - The decoder is VideoToolbox.
    - Nothing has been screened, no group hash computed, no Reef Check frame viewed.
  - **Data.**
    - Nick downloaded the 3 authorised folders on Thu, 2:23–2:33 PM, as **16 zips in `~/Downloads`**, 26.63 GB: `South Wreck-20261015T212025Z-1-001…014.zip`, `October 2022-20261015T212029Z-1-001.zip`, `October 2025-20261015T212032Z-1-001.zip`. Untouched. Never move or delete them without Nick's word.
    - On the SSD (`/Volumes/Nick_R_SSD`, **ejected 9:05 AM Fri**; check `mount` before any write, or you'll write to the laptop disk), in `NereusData/nereus-camera-test-rig/2026-10-15_reefcheck/`:
      - `video/Mendocino Headlands/Mendocino-Headlands-25-08-29-1230-IanNorton-c2.MP4`: 265,872,264 B, sha256 `c47b25fc…`.
      - `video/October 2022/FullersResto-…-leadline.MP4`: 382,488,338 B, `4d28403c…`.
      - `video/October 2025/Caspar-…-1215-IanNorton-c3.MP4`: 176,573,976 B, `fafd7003…`.
      - Full shas are in `download_ledger.tsv`.
      - `runs/extract_smoke_mendocino{,_v2,_v3}`: 4K smoke runs, 23 stills each, byte-identical.
  - **Drive folders.** Nick authorised only his 3 URLs: South Wreck `1Jabkbh729c-…`, October 2022 `1F598YxZ…`, October 2025 `1YzHc56e…`.
    - Mendocino (`1Oiywf…`) was found by Drive search: **kept by Nick's decision 10/15**, but never download from it again.
    - The PE pilot excludes Mendocino.
  - **Environment:** `~/NereusData/nereus-camera-test-rig/_env/urchin-ml` (torch 2.14.1 MPS, transformers 5.19.0; pins in `edge_ml/urchin/requirements.txt`).
    - OWLv2 and SAM weights are in the HF cache.
    - The torchvision COCO checkpoint is in `~/.cache/torch/hub/checkpoints` (fetched with curl, hash prefix checked).
    - The repo `.venv` runs extraction and tests.
  - **Measured:**
    - 4K HEVC clip, 47 s: extraction takes 22 s wall, 9 s CPU, under 600 MB, giving 23 stills (3 MB each).
    - Whole set ≈ 75 min of video → ~40 min of extraction and ~2,200 stills (~7 GB).
    - OWLv2 fires up to 0.32 on kelp/fish/rock: ~150 candidates per kelp frame at 0.05.
    - SAM takes 0.45 s per prompt.
    - Model B trains at ~1.5 s per batch of 4 on MPS (~45 min per seed for 300 stills).

- **In flight:**
  - Branch `feat/e6-urchin-screen` @ `aece215`+handoff (pushed, **no PR yet**). It has review `still-sheets` (57/57 urchin tests), this note, and `archive_zips.py` (copy/verify/unpack for the 16 zips; **not yet QE-reviewed**; dry-run tested only).
  - No processes running, no cron, no watchers.
  - Laptop: 12 GiB free (98 %).
  - 4 hidden stalled app downloads (`~/Downloads/.Q6L2SF6YDW.com.anthropic.claudefordesktop.*`, ~1.4 GB). Nick was asked to remove them; I am not allowed to hard-delete files.

- **Hardware owned:** none. Never touched nereus002, the bmcams or the Spotters.

- **Open decisions / NEEDED from Nick:**
  - The night-shift OK for the pilot (per the PE spec).
  - Whether to free the 26.6 GB of zips from `~/Downloads` once they're archived on the SSD.
  - A ≤ 10-min label audit of 10 test stills before Thu 10/22 (PREREG). If it isn't done, the note says the labels weren't checked by a person.

- **Gotchas (one line each):**
  - exFAT writes `._` sidecars next to every file (videos *and* JPEGs). Never glob the stills folder; use `stills.csv` and lists.
  - Writing to `/Volumes/Nick_R_SSD` while it's unmounted silently creates the folder on the laptop disk.
  - Built-in browser downloads stall as hidden temp files; the Claude in Chrome extension wasn't connected; Nick downloads himself.
  - The python.org Python has no CA certs: fetch weights with curl and check the hash.
  - GitHub drops connections often: retry push and `gh` in a loop.
  - VideoToolbox and software decoding give different pixels on 8-bit video: one decoder per out_dir (the code enforces it).
  - The `owl.py` default floor is 0.05 (pre-labels); scoring needs `--min-score 0.01` (evaluate enforces it).
  - QE order: fill and commit `group_screen.csv` (alphabetical, never hash order) **before** `split folds`.
  - `split folds` refuses a group map whose sha isn't in the PREREG.
  - Heavy jobs: nice 10, ≤ 2 workers, PjM go first, yield to E7 encodes, none Fri 8 AM–6 PM.

- **Next 3 steps:**
  1. Run the PE night pilot after Nick's night-shift OK and the PjM's all-clear: inventory the 16 zips with `unzip -l` + sha256 and no unpacking; pick n = 6 by the written rule; extract one video at a time; owl pre-labels; quality table; 100-still Q1 sample; morning batch by 8 AM.
  2. Archive the zips to the SSD (`archive_zips.py copy` → `verify` after a remount → MANIFEST + `~/NereusData/ARCHIVE_INDEX.csv`), then `unpack` into `video/<folder>/` and run the full extraction (`--decoder videotoolbox` default).
  3. Group screen → commit `group_screen.csv` → `split folds` → labels → models A/B (training needs a PjM slot) → `evaluate` → note draft by Thu 10/22.

- **Who to talk to:**
  - PjM: "PjM (fresh): take over from the 10/15 PjM handoff".
  - QE: "QE review pool PRs #151–#154".
  - Principal Engineer: pilot spec owner.
  - Nick: via the card's `NEEDED:` line only.
