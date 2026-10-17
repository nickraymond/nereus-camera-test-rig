# Handoff 1: pool-2 prep session → successor (Sat 10/17 3:36 PM PT, rotating at ~50 % context)

- **Ticket / goal:**
  - Pool test 2 prep for Wed 10/21: the best video per camera at 10 fps, and a reliable rig.
  - Spec: [SPEC.md](SPEC.md) (tier a). Board: card `c_pool1016_video_review` (yours, keep it current, pinned
    `if_version`, Pacific AM/PM from `date`). Origin: `prop_pool2_prep`, now col done.
  - Rig repo only, worktree `.claude/worktrees/quirky-diffie-ea1766`, branch `study/pool2-prep`, draft PR #121.
- **State now (evidence):**
  - Spec rev 9 is committed byte-identical (`aacf8d28…`, QE 005 PASS Sat 3:37 PM: d55, d56, lens). For any later rev:
    after its QE PASS, `cp ~/NereusData/ops/pe_specs/SPEC_pool2_prep.md docs/pool2_20261021/SPEC.md`, check
    `shasum -a 256`, commit, and tell QE 005 the sha.
  - **Part A done (first version):** [LESSONS.md](LESSONS.md).
    - One-page summary, 10 changes for Wed, clip facts, matched frames, lessons L1–L15.
    - Update it through Tue: the lens result (calibration failed on all 3 cameras, so Wed records uncorrected video;
      d58 asks Nick for a ~15 min lower-half calibration block on Wed) and the part-B answers.
  - **B test card:** [TEST_CARD_B.md](TEST_CARD_B.md), with QE 005 for review. QE's notes so far are applied: the
    IMX708 start is its own line, with its real period; the rev 9 AE3 4:2:0 re-run; the merge is fine for R2. **No bench clip before its
    PASS.** Before the gate, QE checks it again.
  - **Code on #121, all unit-tested** (full suite 766 passed at 3069856; pool2_bench 8/8 at the handoff commit):
    - `scripts/video_clip_table.py`, `scripts/matched_frames.py` (capture-time matching, rev 5).
    - Board stream `subsampling` + `timing`; sync_video per-board settings (`--n6-*` / `--ae3-*`) and fire→first-frame
      delays.
    - Resync fix merged as-is (d2ab3f3 = aa06bc7; #124 closed as superseded) + the combined-decode test (QE R2). QE may
      still ask for a literal rebase; if so, redo it on a fresh branch, never force-push, and tell the rig session.
    - `scripts/gate_cycle.py --video-only` (G-a, G-b, N1–N7, CMA vs the idle reading, `seal`, `sd`).
    - `scripts/pool2_bench.py`: one setting = one command; the bars of rev 8 + d55; fail fast; throttled = void.
    - `scripts/board_hashes.py`: board file sha256 read-back.
  - **Data root:** `~/NereusData/nereus-camera-test-rig/2026-10-17_pool2_prep/` (MANIFEST.md; `partA/`). Bench clips go
    to `bench/` there.
- **In flight:**
  - QE 005 reviews the B card + code (full verdict after its code first pass); nothing deploys before it.
  - PE 002: rev 9 in QE; d58 (lens block) with Nick.
  - No processes running on the Mac or the Pi from this session. No cron, no watchers.
- **Hardware owned:**
  - nereus002, under the rig session's written hand-over (Sat ~3:12 PM; slot ends **Sun 10/18 12:00 PM**). Hand it back
    by message to "nereus002: fix AE3 wedge, 25× start/stop gate, Friday dive" with `~/.no_dive_autostart` PRESENT (plain
    touch, empty, created Sat 3:25 PM). Mon–Tue slots only via PjM 003's sequencing and a new written hand-over.
  - State at 3:35 PM:
    - disarmed, no crontab;
    - `dive-autostart.service` enabled but inactive (guarded by the flag);
    - no recorder running;
    - deployed tree still main@56d49b5 (nothing deployed by me);
    - both boards on 1.28.0 = OpenMV v5.0.1;
    - both at 480M on the hub;
    - SD 33.1 GB free;
    - idle CmaFree 42 MB.
  - The N6 was reset once at 3:28 PM after the mpremote hash read; it came back fine.
- **Open decisions / NEEDED from Nick:**
  - d58 (lens block on Wed) is open.
  - Already decided Sat: d52, d55, d56, d57. Each was forwarded by PjM 003 and read in its own thread by this session.
- **Gotchas learned (one line each):**
  - Run `date` before every clock time; I wrote 3 wrong stamps once (corrected on the card and to the PjM).
  - ssh: `ssh pi@nereus002` (Tailscale) works since Nick's sign-in ~3:24 PM; the check period is unknown (12 h default).
    If it hangs, capture the login.tailscale.com URL and send it to PjM 003; never open it. The bench LAN IP doesn't
    route from this Mac.
  - Pi venv: `/home/pi/nereus-camera-test-rig/.venv/bin/python`. The deployed tree is `~/nereus-rig-poolcam`, holding
    only git files + caches + REVISION, so deploy with `git archive <sha> | ssh pi@nereus002 'tar -x -C ~/nereus-rig-poolcam'`
    and update REVISION. Never `--delete`.
  - mpremote 1.29 `fs sha256sum` prints `sha256sum :file` then the hash; it leaves the board at its REPL unless you chain
    `+ reset` (board_hashes.py does).
  - "Virtual Comm Port in FS Mode" is only the N6's product string; `lsusb -t` says 480M.
  - The AE3 is work-bound at ~107 ms/frame underwater (software JPEG 4:4:4 at q70); 4:2:0 is the B0 lever.
  - zsh doesn't word-split `$var`, and an `rm -rf $VAR` is refused by the safety check: use literal paths, or bash
    scripts in files.
  - Friday's card is tag25h9 (V3), not 36h11.
  - Matched frames: capture time, never host_rx (which lags 100–220 ms).
- **Next 3 steps:**
  1. On QE's card PASS:
     - deploy the bench build (`git archive` of the PASS sha; REVISION);
     - `scripts/board_hashes.py` on both boards → `bench/BUILD.md` (REVISION, Pi-tree hashes, board hashes, firmware,
       the list of A files for the restore);
     - save the scene stills.
  2. B0 with `scripts/pool2_bench.py`:
     - `ctl_1 --under openmv_ae3 -- --timing`;
     - `ae3_420 --under openmv_ae3 -- --ae3-subsampling 420 --timing` (AE3 floor not needed for these arms; report fps);
     - pull each setting with SHA256SUMS between settings, never during a clip;
     - the scene-busy check (AE3 4:4:4 ≥ 24 kB/frame);
     - d56 `evidence` (pinned, that field only);
     - Nick's in-air look under `<date>_ae3_chroma/` (re-encode only after PjM 003's compute all-clear);
     - E1, one exploratory N6 HD clip.
  3. Hand nereus002 back by Sun 12:00 PM. Then:
     - the resolution steps (Mon → Tue noon) under a new hand-over;
     - the gate (Tue PM, `gate_cycle.py arm --video-only …`, with the N1 dry-run measurement first);
     - part C (the Wed card + the rig section of c_envelope_1021) by Tue 3 PM.
- **Who to talk to:**
  - PE 002 ("Principal Engineer 002"): spec.
  - QE 005 ("QE 005: take over reviews from the 10/17 QE handoff"): reviews.
  - PjM 003: schedule, compute all-clear, forwards.
  - The rig session ("nereus002: fix AE3 wedge, 25× start/stop gate, Friday dive"): owns nereus002.
  - Talk to Nick only through the PjM.
