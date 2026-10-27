# Spec: on-board AprilTag detection on nereus002's N6 + AE3 for pool test 3 (Tue 10/27 ~2 PM) (PE 009 rev 0, Mon 10/26 10:43 AM PT)
- **Ticket / card:** c_pool3_edge_tags (prop_pool3_edge_tags) · **Goal / epic:** `edge` / E6 · **Repo / branch:** nereus-camera-test-rig /
  `feat/pool3-edge-tags` → main.
- **Tier:** (c) if Nick grants a §10 freeze exception (the Lead states the risk: board firmware/code changed the day before a pool day; Nick
  accepts in the Lead's chat). Then spec, code and QE run in parallel, and the spec PASS still comes before any pool clip is scored.
  Without that: (a), QE spec PASS before code.
- **Why (Nick, typed in QE 010's chat, Mon ~10:41 AM, found by PE 009 in that transcript; recorded on nick_pool3_scope `decided`):**
  "1.Flash the N6 and AE3 with firmware to detect april tags. This is an edge detection test. 2. I am at the pool to watch the LEDs
  3. Test is around 2pm, you don't worry about that - i'll take care of it. I need the software done this PM". It counts for actions only
  in QE 010's chat; the rig owner needs Nick's own words in its chat before any flash (§6).
- **Facts at start (read Mon 10:40 AM):**
  - N6: stock v5.0.1 has no tag25h9; rig #140 (merged) carries the patch + build/flash/rollback README; one N6 flashed (nereus001's spare):
    HD GRAYSCALE full frame 182 ms, 32 ms with per-tag ROIs; false ids 2 in 100 frames. Board-side tag code is only on draft #139 (QE 011).
  - AE3: stock OpenMV **v5.0.0** already builds tag25h9 (`boards/OPENMV_AE3/imlib_config.h:144` at tag v5.0.0, local clone
    ~/openmv-dev/openmv). So the AE3 may need **no firmware change**, only board code. What nereus002's AE3 and N6 run today: **unknown**:
    the rig owner reads `omv.version_string()` and `dir(image)` on each (read-only) first.
  - Rig main has no board-side AprilTag call (openmv/common/capture_service.py, openmv/{n6,ae3}/main.py).
- **Must do:**
  1. One board-side hook in `openmv/common/capture_service.py` used by both boards: `find_apriltags(families=image.TAG25H9)` on the frame,
     ids filtered to the V3 card's (`card_values_wed.json`), each result (frame id, ids, corners, decode ms) sent in the stream's per-frame
     metadata the host already logs. Off by default; on by one command/flag (the arm line names it).
  2. Detection never costs capture: it runs on a frame subset (every Nth frame or a downscaled copy), N set by a bench measurement so the
     WED_CARD §6 capture bars still hold (≥ 9.5 fps, 0 lost on the AE3, N6 usable ≥ 95 %, skew ≤ 25 ms). Target: ≥ 1 detection/s per board.
  3. N6 firmware: the #140 image only (sha256 in its README), flashed by the rig owner with the README's rollback (DFU alt 1 read first).
     The AE3 gets a firmware change only if must do 0's read shows tag25h9 missing; then the same treatment via `pi/ae3_flash/` (ADIN_SPI_OpenMV).
  4. Bench proof on nereus002 before the pool (rig owner's hand-over): n = 3 × 60 s clips at the pool settings with tags on, V3 card in
     view: capture bars pass; per board, detections/s, % detection frames with ≥ 1 card id, false ids (after filtering), decode ms.
  5. Rollback written in the PR and the card: hook off (one flag) and, for a flashed board, the stock image back.
  (0. First, read-only: the two version strings and `dir(image)`.)
- **Failure modes:**

  | # | What can go wrong | How we detect it | Then |
  |---|---|---|---|
  | F1 | Detection eats frame time; fps drops | Bench must do 4; per-clip fps bars | Raise N / downscale; else hook off for that board |
  | F2 | A flash bricks or wedges a board the day before | Post-flash version check + one 30 s clip | DFU rollback (README); still bad → record that board without tags |
  | F3 | False ids (tag25h9 small distance) | Ids outside the card's set | Filtered; count reported |
  | F4 | Memory: find_apriltags (64 KB+) beside the JPEG encoder on the AE3 | MemoryError / board reset in the stream log | Downscaled copy; else hook off on the AE3 |
  | F5 | Un-QE'd #139 code reaches the pool | QE on this PR's exact head | This PR re-implements the hook on main; nothing from #139 ships unreviewed |
  | F6 | The NOAA update (Tue from 9:30 AM per nick_pool3_scope) needs Nick while the rig needs him | Lead's schedule | NOAA first; pool ~2 PM per Nick |

- **Pass / fail:** pass = must do 4's capture bars on all 3 bench clips with tags on, AND ≥ 1 detection/s per board with ≥ 1 card id in ≥ 50 %
  of detection frames (card fully in view, bench light). Fail = any capture-bar miss with tags on (then that board records without tags).
- **Test plan:** desk (builder, ~3 h) → QE → bench on nereus002 (rig owner, ~1 h, after Nick's words in its chat) → pool Tue ~2 PM under
  TEST_CARD_pool3 rev 2+. Nothing on nereus002 after the bench proof without Nick accepting it.
- **Out of scope:** pose/range on the board, H.264, the Pi-side detector, IMX708 changes, any other unit.
- **Done:** TEAM_RULES §4; PE checks off must dos 0–5 and F1–F6.

## Rev 1 (PE 009, Mon 10/26 10:50 AM PT): Nick's demo spec + QE 011's rev 0 nits N1–N4. Overrides rev 0 where they conflict; set before any data.
- **Nick, typed in Lead 004's chat Mon ~10:55 AM (read by PE 009 in that transcript):** "I expect nereus002 to give me a demo this PM, all
  three cameras should be running their april tag detection. I want the IMX to have 30fps, I want the N6 to have 30 or 15fps with
  intermittent april tag detection at HD quality, and I want AE3 at the smaller rez with intermittent april tag detection at 10 fps. You
  make it happen and keep them working and testing until there is a solution".
- **Scope now covers three cameras** (rev 0 had N6 + AE3 only). The IMX708's detection runs **live on the Pi**, on a frame subset, in a
  separate process at native scale (apriltag_detector.py's note: upscaled passes OOM on the Pi Zero 2 W), never in the capture path.
- **Targets and bars (per camera, worst of 3 bench clips; the "95 % of asked" rule from WED_CARD's 9.5-of-10):**

  | Camera | Asked | Capture pass | Tags |
  |---|---|---|---|
  | IMX708 (Pi) | 1080p H.264, **30 fps** | **≥ 28.5 fps** delivered, max gap ≤ 100 ms. Below 28.5 but ≥ 26.0 (pool 2's bar) = "capture valid, 30 fps not met", reported, not a pass | intermittent, ≥ 1 detection/s |
  | N6 | HD 1280×800 MJPEG, **15 fps** first, 30 fps tried after | 15 fps: **≥ 14.25**; 30 fps: **≥ 28.5**; usable ≥ 95 % | intermittent, ≥ 1 detection/s |
  | AE3 | 640×400 MJPEG 4:2:0, **10 fps** | **≥ 9.5**, 0 lost | intermittent, ≥ 1 detection/s |

  All: skew ≤ 25 ms, 0 wedges / ladder USB resets, throttled 0x0, ≥ 1 card id in ≥ 50 % of detection frames (card fully in view, bench).
  N6 at 15 or 30 fps HD was never measured on nereus002 (pool 2 held 10 fps): USB/encoder limits are unknown, measured on the bench first.
- **"Keep working until there is a solution":** the stop-early line (rev 0 chip: 4 PM) becomes a fallback, not a stop: at the demo start,
  any camera not yet passing runs at its best passing setting (or without tags), and work continues after the demo; the card says which
  camera ran what.
- **QE 011 nits on rev 0 (qe_reviews/QE011_spec_pool3_edge_tags_1f303d06b14b.md), folded here:**
  - N1 pool output (report-only, before any pool clip is scored): per camera and per held range (2.2 m, 3.3 m): % of detection frames with
    ≥ 1 card id, raw ids outside the card's set, decode ms, and agreement with the Pi-side post-clip detector on matched frames (±50 ms).
  - N2 "detection frame" = a frame the hook ran on (with or without a detection). Bench card distance: 2.2 m and 3.3 m, as the pool holds.
  - N3 false ids are counted **before** the card filter (F3's count); after filtering they are 0 by construction.
  - N4 each camera's **max frame gap with tags on** is reported (bench + pool); a gap > 100 ms (IMX708) or > 2 frame periods (boards) fails
    that setting even if mean fps passes.
- **Rev 1.1 (QE 011 nit N6 on rev 1, text):** new row F7: the Pi-side IMX708 detector runs the Pi Zero 2 W out of memory or starves the
  recorder (detected: OOM in dmesg, recorder frame gap > 100 ms, or MemAvailable falling while it runs) → kill the detector process; the
  IMX708 records without tags for that clip; reported.
- **Rev 1.2 (PE 009; builder's finding on must do 1, text):** `card_values_wed.json` holds no tag ids (keys: source, partb, first_bin,
  guard_cameras, detector; rig main 83086d1). The card-id filter comes from **`configs/cards/nereus_v3_<copy>.yaml` `corner_map`** of the
  card copy in view (c1: {tl 0, tr 1, bl 2, br 3}), as openmv/firmware/README.md says ("filter to the ids of the card copy in view"). The
  arm line names the copy (`--tag-ids`); default c1 only if the owner confirms c1 is the copy at the pool from a bench frame's tag ids.

## Rev 2 (PE 009, Mon 10/26 10:58 AM PT): Nick's demo bar (Lead 004's chat, read by PE 009 at 10:57 AM). Set before any data.
- **Nick, verbatim:** "2. Get nereus002 up and running with my pool demo for tomorrow. Run the demo 10 times in a row, with april tag
  detection and quantify the max frame rate and detection rate that each cam can do. I want max quality for IMX, N6 and AE3 sensors."
- **Pool demo: Tue 10/27** (rev 1's "this PM" is withdrawn). The NOAA update (from 9:30 AM) comes first.
- **Max-quality ladder, measured first on the bench (tags on), per camera:** each camera steps from its highest-quality setting the rig's
  capture path supports (highest resolution, then highest JPEG quality / H.264 bitrate) downwards, and the "max-quality setting" is the
  highest rung that holds the capture-validity floor (IMX708 ≥ 26.0 fps, gap ≤ 100 ms; boards ≥ 95 % of the asked fps, 0 lost / usable
  ≥ 95 %, skew ≤ 25 ms, 0 wedges, throttled 0x0). The rungs tried come from must do 1's mode lists (rig docs / firmware); the owner writes
  them into the run plan before the first clip. Rev 1's targets (IMX708 30, N6 HD 15/30, AE3 640 at 10) stay as named rungs.
- **Deliverable table (report-only numbers, per camera, at each rung tried):** max delivered fps, max frame gap, detection rate (detections/s
  and % of detection frames with ≥ 1 card id), decode ms, raw false ids; resolution / quality of the rung.
- **"10 times in a row" (pass bar):** at the chosen demo settings (each camera's max-quality rung), 10 consecutive bench demo runs (60 s,
  3 cameras synced, tags on) each pass the card's bars. A failed run resets the count; the cause is recorded and fixed before restarting.
  Nobody calls the demo ready before 10/10.

## Rev 2.1 (PE 009, Mon 10/26 10:59 AM PT; owner 2's bench prep, ~/NereusData/nereus-camera-test-rig/2026-10-26_bench_prep/MANIFEST.md). No bar moves.
- **IMX708 rungs (data, IMX-only, no boards, no detection):** 1080p (ScalerCrop full sensor) = 27.5 fps, gap 66.7 ms (n = 1): valid
  capture, "30 not met". 720p (`--imx-size 1280x720`, ScalerCrop (768,432)/3072x1728) = 30.01 fps, gap 33.3 ms, 3/3. The 720p field holds
  the N6's whole field at the same 2.4 sensor px per output px, so nothing is lost inside the N6's view; the wider 1080p field is.
- **Rule for the IMX708's demo rung (rev 1's 30 fps bar and rev 2's "max quality" read together):** the highest rung that passes ≥ 28.5 fps
  **with all three cameras and tags running**. On today's data that is 720p; it is chosen only after the full-rig bench clips confirm it.
  Both rungs go in Nick's table, so he sees 1080p's 27.5 fps and can pick the wider field instead.
- **New row F8, CMA exhaustion on the Pi:** free CMA fell to **2 MB** on 720p clip 3 (all buffers still delivered); on 10/14 ci-record
  hard-reset this Pi at CmaFree 16 kB (sync_video.py comment, per the MANIFEST). Detect: min CmaFree logged per clip. Then: any run with min
  CmaFree < 4 MB counts as an issue (resets the 10/10 count) and goes to the owner + PE with the numbers; a CMA or boot-config raise needs
  Nick's own words (root, per the rig handoff). Never mitigate it by changing the bars.
- AE3 on-board tag25h9 (owner 2, n = 20 frames, 640×400): full frame 140–155 ms, ROI 24–25 ms, ids 0–3 on 20/20, no false ids, no
  MemoryError. JPEG encode + USB alongside detection: unmeasured (needs the builder's hook).

## Rev 2.2 (PE 009, Mon 10/26 2:48 PM PT; QE 011 nits on rev 2.1). Text only; set before the ladder / 10× runs.
- N3, CMA pre-run check (prevention for F8): before every bench or pool run the owner reads idle CmaFree; **below 20 MB** → the owner's normal
  recovery (a Pi reboot, then wait for idle) before the run, logged. (Today: clip 3 started at 13 MB idle and fell to 2 MB; idle drifted
  65 → 10 MB across the clips.) A recovery is not a retry and doesn't reset the 10/10 count; a run whose min CmaFree < 4 MB does (F8).
- N2 (stands): ladder rungs come only from rig main or QE-passed PRs (the PS5520 sizes on draft #139 are out).
- Rev 2 N1: if Nick picks the IMX708's 1080p rung from his table, the 10/10 count uses its validity floor (≥ 26.0 fps, gap ≤ 100 ms) and each
  run reports "30 not met"; otherwise the ≥ 28.5 bar applies.

## Rev 2.3 (PE 009, Mon 10/26 5:37 PM PT; owner 2's ladder baseline, 2026-10-26_bench_ladder/MANIFEST.md). No bar moves.
- **Finding:** at the planned rungs (rig main 224e5ce, N6 tag25h9 firmware; IMX708 720p30, N6 HD q70 15, AE3 VGA q70 10, tags OFF, 60 s),
  clip B0b took CmaFree 60 → 0 MB at ~2 MB/s (≈ the clip write rate, N6 ~1.27 + IMX708 ~0.84 MB/s) and held 0 for the last ~15 s; all
  frames delivered. Owner 2's hypothesis (untested): page cache of the clip files fills free CMA. F8's bar (< 4 MB = issue) **stands**
  (rev 2.1: never mitigate by changing the bars).
- **Must do 6, test the hypothesis first (owner, one uncounted diagnostic clip D1, no repo change):** same settings as B0b, plus a side loop
  under ~/NereusData that, every 5 s, runs `os.posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED)` on each clip file being written (no root
  needed for files the capture user owns) and logs CmaFree + Cached from /proc/meminfo. Read:
  - min CmaFree ≥ 4 MB and it tracks Cached → page cache confirmed → must do 7.
  - CmaFree still → < 4 MB with Cached held low → camera/driver buffers, not cache → step down a rung per the ladder (N6 quality or
    IMX708 size first), and a CMA raise (`cma=` / dtoverlay) becomes a NEEDED card for Nick (root + boot config, rev 2.1); never done
    without his own words.
- **Must do 7, the fix if D1 confirms (builder, rig PR → QE draft, then main per N2):** `sync_video.py` releases the written ranges as it goes
  (periodic `os.fdatasync` + `POSIX_FADV_DONTNEED` on the bytes written so far, ≤ 5 s cadence), with a test that a 60 s clip's files are
  byte-identical with and without it. The 10/10 count starts only on main with this in, at a rung that passes F8.
- **New row F9, N6 reboots mid-clip:** n = 1 (B0_tagsoff, frame 357, kernel disconnect 17:30:11 → DFU boot 17:30:12; tags off; cause
  unknown, reset_cause 0). Detect: USB disconnect / DFU line in dmesg, or N6 frame count short. Then: that run fails (resets 10/10). A
  second N6 reboot in the same session → stop captures; the owner saves dmesg + the N6 serial log and the PE re-plans. No cause is named
  and nothing is changed on n = 1.

## Rev 2.4 (PE 009, Mon 10/26 5:38 PM PT; QE 011 N1–N3 on rev 2.3, qe_reviews/QE011_spec_pool3_edge_tags_1f303d06b14b.md :34). Text only; no bar moves.
- **N1, must do 6 / D1 (replaces its loop text; lands before D1):** every 5 s the side loop opens each clip file by path and runs
  `os.fdatasync(fd)` then `os.posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED)` (DONTNEED drops only clean pages; the clip pages stay dirty
  until writeback). It logs CmaFree, Cached, Dirty and Writeback from /proc/meminfo each pass. Three outcomes:
  - Cached held low and min CmaFree ≥ 4 MB → page cache confirmed → must do 7.
  - Cached held low and CmaFree still < 4 MB → buffers, not cache → step down a rung; CMA raise = NEEDED card for Nick (rev 2.3).
  - Cached **not** held low (or Dirty climbs) → the loop didn't work: fix the loop and re-run D1; no rung change, no conclusion.
- **N2, must do 7:** on rig main 224e5ce the IMX708 file is written by rpicam-vid (`scripts/sync_video.py:301–304`, `-o imx708.h264`) and
  the board .mjpeg files by their own writer thread (:190–194). The release runs in **its own thread**, opening each file by path, off the
  USB readers and the rpicam-vid pipe. Must do 7's bench check includes the gap bars (an fdatasync of ~10 MB to SD can stall 100s of ms,
  :181–182), not only byte-identity.
- **N3, must do 7:** `host_stats.csv` (sync_video.py:490) adds `cached_mb` and `dirty_mb`, so every counted clip shows the mechanism.

## Rev 2.5 (PE 009, Mon 10/26 7:49 PM PT; Lead 005's ask on owner 2's rig #143 bench, 2026-10-26_bench_pr143/MANIFEST.md). No bar moves.
- **Data (n = 1 each, rig #143 @ dcd51ba1, rungs I2/N2/A3, 60 s):**
  - Q1 tags on (IMX708 follower decodes H.264 on the Pi, intra 30; boards N6 5/2, AE3 10/3), 5 s release: gaps IMX708 533 ms, N6 321 ms,
    AE3 203 ms (all three over 100 ms); min CmaFree 2 MB at the follower's start; IMX708 0.38 det/s at decode median 1256 ms (below the
    ≥ 1 det/s target). Board tags: N6 3.0 det/s, 99.4 % hit; AE3 1.0 det/s, 98.3 %.
  - Q2 tags off, 5 s release: N6 18 lost, gap 198 ms. Q3 tags off, 1 s release: every capture bar met during capture; CmaFree 3 MB only
    after capture, while finish() reads each file back for its sha256 (Cached 135 → 226 MB at t ≈ 74–80 s).
- **Ruling 1, release cadence (PE's call, no must do dropped):** 1 s becomes the default for counted runs (within must do 7's ≤ 5 s); set as
  the default in the rig PR (→ QE), not by a run flag. The 10/10 is its test; 5 s is out (Q2).
- **Ruling 2, new must do 7 item N4 (fix, not a bar change):** finish()'s sha256 read-back releases each file's pages as it hashes
  (fadvise DONTNEED after each file, as owner 2's rehash.py does), so min CmaFree stays ≥ 4 MB after capture too. Builder, same PR, → QE.
- **Options for the demo, IMX708 tags vs F7 (Nick decides through the Lead; A and B differ from his "all three cameras" line):**

  | Option | What runs Tue | Cost / risk | Drops a must do? |
  |---|---|---|---|
  | A. Board tags only, IMX708 records without tags (F7's existing fallback) | N6 + AE3 tags on board, IMX708 30 fps video | Needs one confirming clip: boards' gaps with the Pi follower off are **unknown** (Q1 broke them with it on) | No (F7 already says so), but differs from Nick's "all three running tag detection" → Nick |
  | B. A + IMX708 tags offline on the Mac after each clip | Same as A live; IMX708 det rate in Nick's table from the saved H.264 | Mac-side script, no Pi load; not live | Live IMX708 detection dropped → Nick |
  | C. IMX708 tags live from rpicam-vid's low-res YUV stream (no H.264 decode) | All three live | New code + QE + bench before Tue ~2 PM; Pi cost at low res **unknown** | No, if it passes; high schedule risk |
  | D. Keep the H.264 follower, sparser (e.g. intra 90) | All three live | Each pass still ~1.26 s of decode (Q1 median); Q1 broke all three gaps | No, if it passes; not supported by Q1 |
- **PE rec (conditional, not from n = 1):** A for the live demo, with B for the IMX708 number in Nick's table; C after Tue. A's first step is
  one clip "boards tags on, IMX708 tags off, 1 s release"; if it misses a capture bar, the boards' tag rates (N/K) step down per rev 2's ladder.
  The 10/10 then confirms whichever Nick picks. If Nick picks C or D, the 10/10 starts only on main with that code in (N2).

## Rev 2.6 (PE 009, Mon 10/26 7:50 PM PT; QE 011 N1 on rev 2.5, qe_reviews/QE011_spec_pool3_edge_tags_1f303d06b14b.md rev 2.5 section). Text only; no bar moves.
- **N1, must do 7 N4 (replaces "after each file"; lands before the builder codes N4):** finish()'s sha256 read-back releases **each chunk as
  it is hashed, ≤ 4 MB**: `posix_fadvise(fd, off, len, POSIX_FADV_DONTNEED)` inside the read loop, as owner 2's rehash.py :25–27 does.
  Per file is not enough: Q3's openmv_n6.mjpeg is 101 MB and imx708.h264 80 MB against 72–92 MB of CmaFree during capture. Test (builder,
  same PR): the advise calls cover each file end to end in steps ≤ 4 MB, and the hashes are unchanged.
