# Spec: pool test 2 prep (Wed 10/21): Friday video review + N6/AE3 resolution-at-10-fps bench study + Wed test card (PE rev 19, tier (a); rev 2–15 amendments at the end; rev 12 replaces the conditional revs 10–11 and is binding; rev 13 = QE 007's rev 12 fixes, in place; rev 14–15 = CMA headroom before Wed, appended; rev 15 = QE 008's rev 14 fixes, in place; rev 16 = undistorted tag corners for range, appended; rev 17 = W1 detector checkpoint + threshold 0.3 as values, appended; rev 18 = rig #133 readings 10–16, appended; rev 19 = the Wed program pinned, appended; rev 19.1 = its QE nits; rev 19.2 = "most clips" on Wed = ≥ 4 of 6; rev 19.3 = fps bars for the 4:4:4 phases; rev 19.4 = AE3 4:4:4 ≥ 9.27 fps; rev 19.5 = program re-pinned, W5 = w5:2x60)

Tier (a): part B runs on nereus002 hardware and part C sets a field-day card. QE spec PASS before the chip; home after
PASS: nereus-camera-test-rig `docs/pool2_20261021/SPEC.md`, the session's first commit. Board: c_pool1016_video_review,
timeline e_pool1021, card prop_pool2_prep. Author: Principal Engineer, Fri 10/16 4:45 PM PT; rev 1 9:46 PM PT
(QE 004 FAIL on rev 0: R1–R6 + S1–S4 applied).

- **Why (Nick, PgM 003 chat, Fri ~4:22–4:36 PM, verbatim on the board):** "This warrants a session to dig into the videos,
  distill their own summary of what we learned from this, what can be made better for the next pool test next week" ·
  "now I really want to see how and if we can do higher resolution on both cameras and maintain that 10fps ... will also
  start to test the difference of the N6 vs AE3". His reads: AE3 "very much in the running" (more so with a glass M12 lens
  of similar or wider FOV); N6 "did ok, still in the running". Epic: E6 shoot-out (gen2 scorecard: media quality, media type).
- **Facts at start (PgM 003 + QE 004, clip098–103 video_sync.json):**
  - N6 and AE3 recorded **MJPEG, not H.264**, at 640×400, JPEG q70, 0 frames lost.
  - N6: 10.0 fps, 8.45 MB / 30 s.
  - AE3: **9.32–9.34 fps** (board_fps 10, frame period ~108 ms), 7.18 MB / 30 s. So the AE3 does not reach 10 fps today.
  - IMX708: hardware H.264 1080p, 26.24–26.67 fps delivered of 30 asked, max gap 66.7–100 ms, 37.8 MB / 30 s.
  - start_skew_ms over n = 6: 8.2–24.3 ms.
  - Host in water: min mem_avail 193 MB, min CMA free 59 MB, max CPU 54.8 °C.
  - Nick's "~6 Mb" was the Mac MP4 re-encodes, not camera output.
- **Must do (numbered, testable):**
  - **A. Friday video review (desk; Sat):**
    1. Per camera and clip: codec, resolution, fps asked vs delivered, frames lost, MB / 30 s, start_skew_ms. Taken
       from the raw clips and `video_sync.json` (`~/NereusPool/20261016/nereus002/pool049_*/video/`), never from the Mac
       MP4 copies.
    2. Matched-frame stills, N6 vs AE3 vs IMX708: the nearest `host_rx_mono` within ±50 ms (the rates differ: 10 / 9.33 /
       26.4 fps). Compare field of view, sharpness, colour, noise and motion blur.
    3. A ≤ 1-page summary for Nick: what we learned; ≤ 10 changes for Wed, each with an owner and "before Wed: yes/no".
  - **B. Bench study on nereus002 (Sun 10/18 – Tue 10/20 noon, only after the rig owner's explicit hand-over):**
    4. **B test card first** (templates/test_card.md, TEAM_RULES §9). It is committed on the rig draft PR after the SPEC
       and QE-checked before the first bench clip. It carries n, the bars below, the stop rules, the cost, and the deployed
       rig sha + board script / firmware hashes. post_pool_ae3_wedge lists pending rig fixes (N6 resync aa06bc7, the
       AE3/IMX708 sync lead): any deploy mid-study means re-running the 640×400 control before comparing.
    5. **Dive-autostart guard:** the rig session disarmed nereus002 at 9:38 PM Fri, which re-enabled Nick's dive autostart
       (ci-record starts on all three cameras at the next boot unless `~/.no_dive_autostart` exists).
       - Set the stand-down flag as the rig owner's hand-over says, before any reboot.
       - Before each setting, check that no ci-record process runs.
       - At hand-back, leave the flag as the owner says.
    6. **B0, AE3 pacing:** try to get the AE3 to ≥ 9.5 fps at 640×400 with a pacing fix in the rig's board script (rig
       PR, QE). If it can't, the AE3 uses the relative bar below and its cap is reported.
    7. For N6 and AE3, list every capture resolution the firmware supports, from the board docs / firmware. Check whether
       the N6 firmware offers H.264 (a hardware encoder) and cite the source.
    8. MJPEG at q70: step up from 640×400 through each supported resolution to the sensor maximum. If N6 H.264 exists,
       test the highest MJPEG-holding resolution and the next one up.
    9. Per setting: n = 3 clips of 30 s, synced with the IMX708 as on Friday. Record the camera's delivered fps, frames
       lost, MB / 30 s, start_skew_ms, IMX708 fps and max_gap, host `throttled` (host_stats.csv), mem_avail, CMA free,
       board temperature if reported, and any wedge or USB reset.
    10. Answer per camera: the highest setting that holds, where every lower step also holds (results may not be
        monotonic).
  - **C. Wednesday test card (by Tue 10/20 3 PM, for Nick's approval in the PjM's batch):**
    11. Setup per camera at its part-B winner.
    12. An N6-vs-AE3 comparison design: same scene, distance and light; synced; side by side; matched frames ±50 ms.
    13. n clips, numeric bars, stop rules.
    14. Close ChArUco re-poses for N6/AE3 if the pool049 calibration fails their corners.
    15. Write the **rig section** (units, verbs, scripts, stop rules) of the PjM's §6 approval envelope (c_envelope_1021);
        the PjM drafts the envelope itself. Observe the §10 freeze (Tue night / Wed on the pool path).
- **Failure modes (what goes wrong, how it's caught, what happens):**
  - F1 Higher resolution overruns USB or the encoder, dropping frames or fps. Caught by the bars. That setting fails;
    the step below wins.
  - F2 The AE3 wedges. The known wedge is cold-boot only (1/5 cold boots, 0/~750 in-boot resets; post_pool_ae3_wedge).
    A wedge, or a ladder USB reset during a setting's clips, fails that setting. The normal per-clip board reset
    (reset_s 3.4–5.0 s) doesn't count. Cold-boot wedges count toward the stop rule: 3 in a day → stop AE3, report.
    Recovery is a power-cycle, but only after the must-do-5 guard.
  - F3 The rig battery runs low on the bench. Caught by UPS VBAT. Run on the charger, or with VBAT ≥ 3.4 V.
  - F4 Bench results don't carry to the pool (light, water temperature, USB cable). Caught by Wed's own bars. Wed keeps
    one 640×400 control clip per camera.
  - F5 Rig changes during the freeze. Caught by §10. No rig changes after Tue 10/20 6 PM unless Nick accepts the risk.
  - F6 Different FOV or lens confounds "which sensor". Record each lens (the AE3 has an M12; a glass M12 swap is Nick's
    call). Matched-frame crops are taken at equal ground footprint.
  - F7 Dive autostart grabs the cameras after a reboot (ci-record), the study's clips fail, the SD fills. Must-do 5.
  - F8 Bigger board frames starve the IMX708 or the host (all three record on Wed through the same Pi; the bench is in
    air, the pool runs warmer). Caught by the IMX708 and host bars below.
- **Pass / fail (set now).** A setting **holds** when all 3 clips meet every bar:
  - **fps:** delivered ≥ 9.5 fps over 30 s, or for the AE3 if B0 fails, ≤ 0.2 fps below its own 640×400 rate;
  - 0 frames lost;
  - 0 wedges or ladder USB resets (per F2);
  - start_skew_ms ≤ 25 ms per clip (from video_sync.json);
  - IMX708 ≥ 26.0 fps with max_gap ≤ 100 ms;
  - host `throttled` = 0x0. A throttled clip is void: cool down and re-run it; 2 in a row → stop.

  Done:
  - part A by Sat;
  - part B's per-camera answer, with the table, by Tue 10/20 noon;
  - part C's card by Tue 3 PM;
  - QE has checked each;
  - Nick has approved the card and the envelope before Wed.
- **Cost and disk:**
  - Time: A ~2–3 h desk (Sat, after the pulls). B ~4–6 h on nereus002, Sun–Tue, after the E7 Pi slot (Sat noon–2:30 PM).
    C ~1 h. Nick ~15 min Tue.
  - Disk: stop below 2 GB free on the Pi SD (clips land in /home/pi/nereus-rig-poolcam/results first) or below 8 GiB on
    the Mac data root; ≤ 5 GB written there.
- **Exit:** if both boards hold above 640×400, Wed runs the N6-vs-AE3 comparison at those settings, and the results feed
  the gen2 shoot-out (sensor choice in Jan). If not, Wed runs at 640×400 with the limit documented (USB, encoder,
  firmware or pacing) and an option list for Nick (e.g. N6 H.264, AE3 pacing). Rig PR (spec, test card, scripts,
  RESULTS, the Wed card) → QE → PjM merges.
- **Out of scope:** the urchin model; the 5 s video encoder (E7); buying lenses (Nick); changes to the IMX708 settings.

## Amendment rev 2 (PE, Sat 10/17 2:39 PM; Nick approved 2:08 PM with direction, verbatim on prop_pool2_prep)
1. **Lens corrections:** Wed's new video uses Friday's lens corrections (prop_lens_calib_pool049) if that run passes; the
   lens-calibration session is sequenced to finish by Tue 10/20 noon. If it fails or is late, Wed records uncorrected video
   (as Friday) and corrections are applied afterwards; Wed's card says which.
2. **Best quality per camera; settings may differ.**
   - N6: H.264 at native 1200×800 @ 10 fps, if last week's OpenMV firmware release supports it. A firmware update is a
     rig change (TEAM_RULES §9): back up the current firmware, write the restore command, verify after, and finish before
     the Tue 10/20 6 PM freeze; do it only with the rig owner's hand-over.
   - AE3: the best it can achieve (Nick doubts HD @ 10 fps).
   - Part A starts by reviewing the documented past work (rig docs, prior study RESULTS, post_pool_ae3_wedge).
3. **Ready-for-pool gate (new):** 25 cold boots in a row, each booting, recording 30 s on all 3 cameras and shutting down,
   with memory clean (no leaked processes, mem_avail back to its post-boot level) before the pool. Any failure resets the
   count. Cold boot because it fixed the USB hangs after warm starts. It goes on the B test card; it must pass before the
   Tue freeze.
4. **Out of scope (Nick):** the IMX708 green colour; any rewiring; any new glass (so no M12 lens swap for the AE3).
5. **One combined lessons list** across this video study, the lens work and Friday's pool-day pain points, accounting for
   the active sessions (the nereus002 rig session, the E7 cut sheet, lens calibration), owners named, in part A's summary.

## Amendment rev 3 (PE, Sat 10/17 2:56 PM; QE 004 R1–R6 + nits on rev 2). Overrides rev 1 and rev 2 where they conflict.
- **25× gate per d52 (Nick, 2:23 PM, thread 545bdfb8: "go with A and D, do not couple the video completeness req with the
  "must run 25x reliably" req"):**
  - A cycle passes on boot + recording on all 3 cameras + clean shutdown. 25 passing cycles in a row; a failed cycle resets
    the count.
  - Frame completeness is scored and reported separately per cycle and never resets the count.
  - Harness: the rig's `scripts/gate_cycle.py`, which handles `~/.no_dive_autostart`. No hand-rolled reboot loops.
- **Memory clean, measured 30 s after each clip ends:**
  - mem_avail within 50 MB of that cycle's post-boot reading;
  - CMA free ≥ 50 MB (Friday's in-water minimum was 59 MB);
  - leaked processes (ci-record, rpicam-*, capture scripts) = 0;
  - Pi SD free space reported.

  Nick's "memory should be clean" is read as RAM + processes; SD space is reported too. The PjM confirms with Nick in his
  batch.
- **N6 mode:** Nick's "1200×800" is the PAG7936's HD **1280×800** (max; rig docs/open_questions.md:19).
  - Overriding rev 1 must dos 8/10 for the N6: candidates are HD 1280×800 MJPEG and HD H.264 (if the firmware has it) at
    10 fps, plus the 640×400 control.
  - If both HD settings hold, "best" = H.264 (smaller files), unless its frame completeness or start skew is worse; then
    MJPEG.
  - AE3: "best" = the highest resolution that holds under the bar, including B0 and the relative fps bar (no "HD at 7 fps").
- **Order (fixes the timing):**
  1. By Sun night: any N6 firmware update, the N6 H.264 check and the AE3 pacing fix (B0).
  2. Mon → Tue noon: the resolution steps on the final firmware and build.
  3. Tue afternoon, last: the 25× gate on Wed's exact build and settings, before the Tue 6 PM freeze.
  4. Any change after the gate means the gate re-runs. If d53 (the N6 resync fix; Nick's answer is due Sun AM) is approved,
     it deploys before step 2.
- **Firmware update rules (§9):**
  - Afterwards, re-run the 640×400 control and update the B card's firmware hashes.
  - Stop rule: if recovery would need hands at the bench (DFU, replug) while Nick is travelling, roll back if that can be
    done remotely, else stop and report. Don't improvise.
- **Lens corrections on Wed:** Mac-side post-processing only; nothing undistorts on the rig.
  - "Passes" = that camera's B1–B6 in SPEC_lens_calib_pool049.
  - Mixed result: correct only the cameras that pass; the rest stay uncorrected, labelled so.
- **Nits:**
  - The Exit line's "options for Nick" no longer lists N6 H.264; it's in scope now.
  - The lessons list is first written Sat and updated through Tue (lens results included).
  - Disk for the gate: 25 cycles × 3 cameras × 30 s ≈ 50–100 MB per cycle, ≈ ≤ 2.5 GB on the Pi SD, so clear the gate
    clips after each cycle's check or keep the SD stop (< 2 GB free) in force.

## Amendment rev 4 (PE 002, Sat 10/17 2:58 PM; Nick's answer on "memory clean"). Overrides rev 3's "Memory clean" block.
- **Nick, PE chat ("Principal Engineer", local_8693e8ed), Sat 10/17 ~2:55 PM, verbatim:** "memory meant SD card space in
  that context". It answers the question rev 3 left open ("read as RAM + processes … the PjM confirms with Nick"), so
  rev 3's RAM reading is replaced, not loosened by the PE. No gate data exists yet (the gate runs Tue PM).
- **The ready-for-pool gate has two verdict lines; both must pass.** Kept separate, as in d52 ("do not couple"):
  - **G-a, 25 in a row (unchanged from rev 3):** boot + recording on all 3 cameras + clean shutdown, 25 passing cycles in a
    row; a failed cycle resets the count. Added to the cycle pass: 30 s after the clip ends, 0 recorder processes still
    running (ci-record, rpicam-*, sync_video.py / capture scripts). Why it stays a gate: it's the "stop" half of Nick's
    start/stop bar, a recorder left running at poweroff is the warm-start USB-hang pattern cold boots fixed, it catches
    dive autostart grabbing the cameras (F7), and it can't false-fail.
  - **G-b, SD clean (new):** measured with `df -B1` on the SD filesystem that holds `results/`. "Outside use" = SD used
    minus `du -sb results/gate/<run>/` (the gate's own clips and logs are evidence, so they don't count as leaks).
    1. Readings: gate start (before cycle 1), then each cycle after boot (before recording) and 30 s after its clip ends.
    2. No cycle grows outside use by more than 100 MB.
    3. Outside use at the last reading is ≤ 250 MB above the gate-start reading (nothing accumulates across 25 cycles).
    4. SD free ≥ 2 GB at every reading. Before arming, SD free ≥ 2 GB + 25 × the per-cycle run-folder size measured at
       Wed's settings in part B; if not, pull and clear first.
    5. **Clean before the pool:** after the gate, the run folder is pulled to
       `~/NereusData/nereus-camera-test-rig/2026-10-20_gate25/`, its SHA256SUMS verify on the Mac, and only then is it
       cleared from the Pi. SD free is then within 250 MB of the gate-start reading, ≥ 2 GB, and ≥ 2× the SD use Wed's
       card (part C) plans.
    - G-b fails → find the cause. A stray file only: remove it and re-check 5. A code or config fix: the gate re-runs
      (rev 3 order rule 4).
- **RAM is report-only:** mem_avail and CMA free per cycle, 30 s after the clip. RESULTS flags any CMA free < 50 MB or
  mem_avail < 150 MB (Friday in water: 59 MB, 193 MB). Why not a gate: every cycle is a cold boot, so RAM can't carry
  from one cycle to the next, and a camera starved of RAM fails its recording, which already fails G-a.
- **New failure-mode row F9:** the SD fills before or during Wed (clips not cleared, autostart clips, log growth). Caught by
  G-b readings 1–5 and the existing < 2 GB stop. Then: stop, pull and clear, and re-check G-b 5 before the freeze.
- **Harness:** `scripts/gate_cycle.py` today records no SD or process readings and clears nothing. Adding them (plus the
  CSV columns) is a rig change on the session's draft PR, with QE, before the gate; it's part of Wed's build, so it lands
  before rev 3 order step 3.

## Amendment rev 5 (PE 002, Sat 10/17 3:02 PM; the pool2 session's clock finding). Overrides must dos 2 and 12's clock.
- **Finding (pool2 session, PR #121 scripts/matched_frames.py; PE spot check on clip098):** `host_rx_mono` is when the Pi
  finished reading a board frame (capture + JPEG + USB), not when it was captured. It lags capture by ~101–135 ms on the N6
  and ~207–218 ms on the AE3 (clip098: medians 123 ms and 207 ms). Matching on it pairs frames 1–2 frames apart at 10 fps.
- **Must dos 2 and 12 now match on capture time, within ±50 ms (the bar is unchanged, now on the right clock):**
  - boards: `ticks_ms` mapped to the Pi clock by the fire receipt (`first_frame_host_mono` + (ticks − first ticks));
  - IMX708: its SensorTimestamp, on the same clock as `first_frame_host_mono` in video_sync.json.
  - A triplet whose capture times span more than 50 ms is dropped and counted in the report, never used.
  - The report keeps the `host_rx_mono` offsets as a reference column.

## Amendment rev 6 (PE 002, Sat 10/17 3:13 PM; the pool2 session's firmware + JPEG findings). Overrides rev 2 item 2 and rev 3's N6 mode where they conflict.
- **No N6 H.264 this round.** H.264 is in no OpenMV build: not v5.0.1 (our boards run 1.28.0 = v5.0.1), not the 10/16
  Development Release, and master has no codec module. It exists only as draft PR openmv/openmv#3247 (open since 8/22,
  last updated 9/14). PE checked with the GitHub API at 3:13 PM. Rev 2's condition ("if last week's firmware supports it")
  is false, so the HD H.264 candidate is empty and there's no N6 firmware update. Rev 3's firmware rules stay in case one
  is needed later. The B results say it in one line for Nick.
- **N6 candidates:** HD 1280×800 MJPEG q70 + the 640×400 control.
  - Pre-registered prediction (B card, before any HD clip): HD MJPEG q70 needs ~0.97 MB/s (97 kB/frame, 10/2 bench). That is
    at or above what the N6's full-speed USB link carries in practice, so expect fps or frame loss. `lsusb -t` is recorded
    first.
  - **Fallback, only if HD fails:** 960×600 MJPEG q70 (a custom size the N6 ISP scales to; untested on our boards).
    First a 1-shot probe: frames decode at 960×600 and the stream runs 30 s. If the probe passes, n = 3 under the same bars.
    If the probe fails, the N6 answer is 640×400, with the limit stated.
- **AE3 chroma (B0):** Friday's AE3 frames were JPEG 4:4:4 (OpenMV's software encoder picks 4:4:4 at q ≥ 60), and the
  N6's are 4:2:0 (hardware encoder, 4:2:0 only).
  - B0 runs the AE3 at 640×400 q70 in both 4:4:4 and 4:2:0 (`subsampling=image.JPEG_SUBSAMPLING_420`), n = 3 each, on the
    fixed busy scene, with per-frame timing (snapshot / JPEG / USB ms) added to the stream (a board-script change on the
    rig PR; after it deploys, the 640×400 control re-runs).
  - Format for the AE3 ladder: 4:4:4 if it reaches ≥ 9.5 fps (with any other pacing fix); else 4:2:0 if that reaches
    ≥ 9.5 fps; else 4:4:4 under the relative fps bar.
  - Every row and the Wed card name each camera's chroma format. The B results include a matched-frame crop pair of the
    AE3 in 4:4:4 vs 4:2:0. Nick rules on the trade (half the colour resolution for speed, and a like-for-like 4:2:0
    comparison with the N6) when he approves the Wed card (part C).

## Amendment rev 7 (PE 002, Sat 10/17 3:28 PM; Nick's d57 + corrections). Overrides earlier revs where they conflict.
- **d57 (Nick, Sat 10/17 3:26 PM PT, board thread 8b58f7d2, verbatim: "Approved."; it answers d57 v1 `ask`, which replaces
  d53):**
  1. The N6 resync fix (rig aa06bc7) goes into the build at the start of the bench tests, before B0. After it deploys, both
     boards' 640×400 controls re-run (rev 1 must do 4), and the B card records the new rig sha. Rev 3 order rule 4's "if d53
     is approved" is replaced by this.
  2. No pause bar (d53's "> 1 in 20 clips" is not adopted) and no USB root-cause work.
  3. **N6 frame bar:** "0 frames lost" (rev 1 pass/fail) is replaced, for the N6 only, by the usable-clip share.
     - A clip is usable when it has ≥ 95 % of its expected frames (d52).
     - An N6 setting holds when ≥ 2 of its 3 clips are usable ("most clips") and every other bar holds.
     - Each N6 setting reports its usable share, and the lost-frame count per clip.
     - Wed runs the highest N6 setting that holds.
     - The AE3 keeps "0 frames lost"; d57 names the N6 only.
  4. The 25 cold-boot ready gate (d52, rev 4 G-a + G-b) stays the reliability check. Video completeness stays separate
     and is reported, as in d52.
- **Carried onto the spec from QE 005's rev 6 nits (already binding on the B card):**
  - R1: Nick's AE3 chroma call is decision d56, needed Mon 8 AM before the AE3 ladder. B0's fps for both formats and the
    crop pair go in d56's `evidence` field Sun night. With no answer, rev 6's rule stands, and the Wed card says which
    format ran.
  - N9: the 960×600 probe clip doesn't count toward its n = 3.
  - N10: a board-script change both boards share re-runs both 640×400 controls.
  - N11: the Exit's option list for Nick names N6 H.264 for when openmv#3247 lands.
- **Correction to rev 6's prediction rationale:** `lsusb -t` on nereus002 (pool2 session, Sat 3:27 PM) shows both boards as
  cdc_acm at 480M (high-speed). "Virtual Comm Port in FS Mode" is only the N6's USB product string; rig RESULTS
  10/14:344 is wrong on this. HD MJPEG at ~0.97 MB/s fits the link. The pre-registered prediction is now "HD holds 10 fps;
  the risk is byte loss when the Pi's reader stalls", as corrected on the B card before any HD clip (rig #121 @ f54ecf6).
  No bar moves.
- **CMA flag (report-only, rev 4):** idle CmaFree is 42 MB on nereus002 (the page cache borrows CMA), so "< 50 MB" means
  nothing on its own. The flag becomes "CmaFree more than 20 MB below that boot's idle reading". RESULTS shows the idle
  baseline next to each reading.
- d55 (start-skew bar) is still with Nick (Mon 8 AM). Its answer goes in rev 8.

## Amendment rev 8 (PE 002, Sat 10/17 3:30 PM; QE 005 R2 + R3 on rev 7, required before B0). Overrides earlier revs where they conflict.
- **R2, the N6 resync fix has to be reviewed before it deploys.** aa06bc7 exists only on a local branch
  (fix/n6-video-resync, committed 10/15) and has no PR or QE review. It changes the board-stream decode in
  `scripts/sync_video.py`, which rig #121 also changes (f672126). Before it deploys to nereus002:
  1. bring it onto the pool2 branch (rig #121; it's d57's work, so it belongs to this ticket), rebased on f672126;
  2. add a unit test of the combined decode: a synthetic byte loss costs one frame, the stream resyncs, and the
     timing columns stay right;
  3. QE PASS on that push;
  4. then deploy, re-run both 640×400 controls, and record the rig sha on the B card.
  It still goes in before B0 (rev 7, d57 item 1). If QE can't pass it by Sun night, B0 and the ladder run without it, and
  the N6's usable share shows the cost. Tell the PE and PjM.
- **R3, which bars an unusable N6 clip still has to meet (implements d57 as approved):**
  - **Expected frames** = board_fps × clip seconds (10 × 30 = 300). A truncated stream can't shrink it.
  - A clip is **usable** when frames received ≥ 95 % of expected (≥ 285 at 10 fps / 30 s).
  - An N6 setting holds when ≥ 2 of its 3 clips are usable, and the usable clips meet every other bar (fps ≥ 9.5,
    start skew, IMX708, host). An unusable clip counts only in the usable share and the lost-frame count.
  - Any clip with a wedge or a ladder USB reset (F2) still fails the setting.
  - A throttled clip is still void (cool down, re-run).
- The rev 7 block says d55 goes in rev 8. It now goes in **rev 9**.

## Amendment rev 9 (PE 002, Sat 10/17 3:34 PM; Nick's d55 + d56, and the lens result). Overrides earlier revs where they conflict.
- **d55 (Nick, Sat 10/17 3:32 PM PT, board thread 10b6d36a, verbatim: "aproved"; answers d55 (a) + (b) as written):**
  - (a) The "start skew ≤ 25 ms per clip" bar now applies to **N6 ↔ AE3 only**: |N6 first frame − AE3 first frame| from
    video_sync.json (the same capture clock as rev 5).
  - (b) **IMX708 start check:** its first frame ≤ 34 ms (one frame period) after its fire, i.e. first_frame_host_mono −
    fire_host_mono ≤ 34 ms, per clip. It's its own verdict line, reported per setting, and does **not** decide whether an
    N6 or AE3 setting holds. That decoupling is d55's point. Host starvation by bigger board frames (F8) stays covered by
    the IMX708 fps ≥ 26.0 and max_gap ≤ 100 ms bars.
- **d56 (Nick, Sat 10/17 3:32 PM PT, board thread e0fb1d33, verbatim):** "yes, good call. AE3 can run with reduced color like
  the N6. Hopefully this enables higher rez at 10 fps for AE3. Please also add a test then when underwater to record same
  scenery with full and half color for the AE3 so I can see for myself the impact, or if this can be done in air thats also
  ok".
  - Rev 6's B0 format rule stands as approved.
  - Added, from his "can run with reduced color … hopefully this enables higher rez": if the AE3 ladder runs in 4:4:4 and
    its first rung above 640×400 fails, that rung re-runs in 4:2:0 (n = 3). If it holds, the AE3 ladder continues in
    4:2:0. No bar moves; it adds at most one rung.
  - **His side-by-side test (in air, on the bench):** B0's same-scene 4:4:4 and 4:2:0 clips go to him as a viewing pair:
    two QuickTime-playable copies, plus 100 % crops of matched frames. They're under
    `~/NereusData/nereus-camera-test-rig/<date>_ae3_chroma/`, with a MANIFEST and the path in d56's `evidence`. The
    viewing copies are labelled "re-encoded for viewing" and the originals stay untouched. The re-encode runs only under
    PjM 003's compute all-clear.
  - Optional on Wed: one underwater 30 s pair of the same scene, if the Wed card has room (part C decides; Nick approves
    the card).
- **Lens calibration result (c_charuco, Sat 3:33 PM; provisional until QE passes bm #184's tool code):** IMX708, N6 and AE3
  all fail B3 and B5c. The board never reached the bottom ~40 % of any frame. So per rev 3, Wed records uncorrected video
  on all three cameras, labelled so, and corrections are applied afterwards only to cameras that later pass.
- **Conditional, decision d58:** if Nick approves the lens session's lower-half calibration block (~15 min of pool time,
  ~20 poses, all three cameras at once; the rig raised ~40 cm, tilted up ~25° or rolled 180° for that block only, then
  restored), part C's Wed card includes it per the recipe on c_charuco. This extends must do 14 to the IMX708. The rig
  change for that block is announced (§10) and written into the envelope's rig section (must do 15).

## Amendment rev 10 (PE 003, Sun 10/18 9:15 AM PT; Nick's ask for detection on Wed). CONDITIONAL: binding only on Nick's yes. Overrides earlier revs where they conflict.
- **Source (Nick, Sun 10/18 8:50 AM PT, board comment on the Sun 10/18 start-of-day update, thread b37485c5, attributed
  "the user (owner)", verbatim):** "I'd like to review a report or other doc in more detail with the PE to understand what
  we found from the Friday test, what did we learn, what needs to be added to the Wednesday testing to evaluate the cameras
  and being able to detect something underwater. I want a focus session to understand what is going into these decisions."
- **Status:** proposed in the debrief doc (board card c_pool1_debrief, decisions 1–2). Nothing in this block applies unless
  Nick says yes to decision 1 in his own words (focus session, PjM chat forward, or his board comment) by **Tue 10/20 noon**.
  With no yes, Wed runs rev 9 unchanged. Rev 9's d58 conditional block is void (d58 decided NO, Sat 3:39 PM).
- **Why:** rev 9 measures frame rate, completeness, sync and a by-eye comparison. Nothing measures whether a camera can see a
  target of known size at a known range in real pool water. Geometry alone (bm #184 fx; centre of frame): an 80 mm urchin
  at 2 m is 65 px (N6 1280×800), 44 px (IMX708 1080p), 33 px (N6 640×400), 28 px (AE3 640×400). Turbidity, blur and
  contrast loss are what the pool adds, and only a target shows them.
- **Must do 16, W1 detection ladder:**
  1. **Target (rig session makes it; Nick prints it).** A script in the rig code tree writes a print-ready PDF to
     `~/NereusData/nereus-camera-test-rig/<date>_w1_target/` (MANIFEST: source frames, sha256; the PDF is not committed).
     At 100 % scale on one 11×17 in (or A3) sheet: two urchin photos at 80 mm test diameter and two at 50 mm, cropped
     from Reef Check stills in the urchin pilot's run folder (frame names in the MANIFEST); a black square ≥ 100 mm on
     white, rotated ~5° (slanted edge); a 200 mm black-and-white quartered disk; a 100 mm scale bar. Laminated and
     weighted (Nick). **Print check:** Nick measures one 80 mm photo with a ruler; within ±2 mm, else reprint; the
     measured width goes on the Wed card.
  2. **Placement.** On the floor tape, centred between the N6 and AE3 axes, facing the rig, at 3.0, 2.0, 1.5, 1.0 and
     0.5 m from the rig port to the target face, in that order, then 1.0 m again at the end (F12). At each stop, one 30 s
     clip on all three cameras at Wed's settings, with the diver out of the frame for ≥ 20 s of it.
  3. **Scoring (after the dive, on the Mac; matched frames on the rev 5 capture clock, ±50 ms).** Per camera per stop, 5
     frames evenly spaced in the diver-free segment:
     - pixels across each 80 mm and 50 mm photo (the 100 mm scale bar cross-checks px/mm);
     - detector: the urchin pilot's zero-shot detector (same model, checkpoint and score threshold, recorded), prompt
       "purple sea urchin"; a hit = a box with IoU ≥ 0.5 against the hand-drawn photo box;
     - MTF50 from the slanted edge, in cycles/pixel and in line pairs/mm at the target;
     - Michelson contrast of the quartered disk.
  4. **Pass/fail (set now, before any data).** A camera *sees an urchin at d* when the 80 mm photo is ≥ 30 px across
     **and** the detector hits in ≥ 4 of 5 frames. Its **detectable range** is the largest d where that holds and it also
     holds at every smaller d. Reported for the 50 mm photos too. N6 vs AE3: the longer detectable range wins; a tie goes
     to the higher MTF50 at 1.0 m.
     - The 30 px line is the PE's working value (debrief decision 2). Nick may set another number before any Wed data
       exists; after data it can only tighten (§13).
     - **Pre-registered fallback:** if the detector hits in < 4 of 5 frames at 0.5 m on every camera, it is the wrong tool
       for a printed target. The detector criterion is then replaced by a blind person's yes/no on shuffled crops (not
       told the camera or distance; ≥ 4 of 5 yes), and RESULTS says so.
- **Must do 17, W2 visibility (report-only):** one dive-log line: the horizontal distance from the rig at which the
  quartered disk disappears to the diver's eye (nearest 0.25 m), plus a note on the water. Also the disk contrast at each
  W1 stop.
- **Must do 18, W3 colour card (report-only):** the v3 card on the tape facing the rig at 1.0 m and 2.0 m, one 30 s clip
  each, all three cameras. Scored after: CIEDE2000 per patch against the card's reference values on uncorrected camera
  output; median and worst patch per camera. It feeds the shoot-out's colour pillar; no bar.
- **New failure modes:**
  - F10 Target at the wrong distance or angle (diver). Caught by px/mm from the scale bar vs the fx prediction: off by
    > 15 % → that stop is excluded and listed.
  - F11 Target cut by a frame edge (the cameras point slightly differently; the AE3 view sits right of the N6's). That stop
    is scored only for cameras that see the whole target; listed.
  - F12 Water clears or clouds during the block (silt). The repeat 1.0 m stop at the end: disk contrast differs by > 20 %
    from the first 1.0 m stop → flagged in RESULTS; ranges stand but carry the flag.
  - F13 Print scale wrong. Caught by the print check (must do 16.1).
  - F14 The block eats pool time. W1–W3 come after rev 9's N6-vs-AE3 clips on the card, so they never displace them,
    and are time-boxed to 25 min; at the box, stop and keep what is recorded.
- **Card, envelope, code:** part C's Wed card lists W1–W3 after the N6-vs-AE3 clips. The envelope's rig section gains no
  verbs (recording only, 8 extra 30 s clips). Scoring code goes on the rig PR with QE before any W1 result is reported.
  The detector runs on the Mac under the PjM's compute all-clear.
- **Cost:** pool ~20 min (W1 ~15, W2 ~1, W3 ~3); rig session ~2 h desk (target script, scoring); Nick ~20 min (print,
  laminate, weight). Disk: 8 clips × ~75 MB (IMX708 ~38 + N6 HD ~29 + AE3 ~7) ≈ 0.6 GB, added to Wed's planned SD use in
  G-b item 4 and the Mac pull.
- **Out of scope:** detector accuracy on real urchins (E6, Reef Check video); lens correction (d58); any rig change.

## Amendment rev 11 (PE 003, Sun 10/18 9:12 AM PT; QE 006 FAIL on rev 10: R1 + N1–N3). CONDITIONAL with rev 10. Overrides rev 10 where they conflict.
- **R1, W1 pass/fail (replaces must do 16.4's first paragraph; the 30 px note and the detector fallback stay):**
  - (a) **Both photos.** Each of the two 80 mm photos is scored on its own. A camera *sees an urchin at d* only when
    **both** 80 mm photos are ≥ 30 px across **and** each is hit by the detector in ≥ 4 of 5 frames. The 50 mm report uses
    the same both-photos rule. (Under the fallback, "hit" = the blind person's yes, per photo.)
  - (b) **First pass only.** The range uses the first 1.0 m stop. The repeat 1.0 m stop is only for F12 drift and never
    changes a range.
  - (c) **An excluded stop breaks the chain.** Stops are taken in order of distance (0.5, 1.0, 1.5, 2.0, 3.0 m). The
    detectable range is the largest d such that every stop from 0.5 m up to d was scored **and** held. A stop excluded
    by F10 or F11 counts as not held. Example: AE3 1.5 m excluded for F11 → its range is at most 1.0 m, whatever 2.0 m
    shows. If 0.5 m fails or is excluded, the range is "none at 0.5 m" (reported, not interpolated).
  - (d) **Tie-break unit.** MTF50 in **line pairs per mm at the target**, at the first 1.0 m stop (comparable across
    resolutions). Cycles/pixel is reported beside it, never used to rank.
- **N1:** the Wed card writes the urchin pilot's model checkpoint name and score threshold as **values**, copied from rig
  #123's pilot config at its merged sha. W1 scoring reads those values, not a live setting.
- **N2 (F10's prediction):** predicted px/mm = fx × (1 / distance in mm), with fx from bm #184's fit (**NOT VALIDATED**
  as a lens model, but its centre focal length is stable: JPEG vs DNG fx within 0.03 %). IMX708: 1080p is taken as the
  full-FOV binned mode scaled to 1920 wide, so fx = 2628.3 × 1920/4608 ≈ 1095 px. If **every** IMX708 stop is off by the
  same factor (> 15 %, all within ±5 % of each other), the mode assumption is wrong, not the placement: no IMX708 stop is
  excluded for F10, and RESULTS records the factor. The 30 px test always uses **measured** pixels, never the prediction.
- **N3 (reference boxes):** the rig session draws the reference box for each photo on the sampled frames **before** the
  detector runs, without seeing any detector output, and commits the box file (its sha in RESULTS) before the detector
  run. IoU uses those boxes.
- **N4 (noted):** the quoted comment is the reason for rev 10, not an approval. Only Nick's yes on debrief decision 1 binds
  revs 10–11; the executing session verifies it at the source (§6).

## Amendment rev 12 (PE 003, Sun 10/18 5:18 PM PT; Nick's decisions in the pool test 1 debrief doc). Replaces revs 10–11 (W1–W3) in full; binding.
**Rev 13 (PE 004, Sun 10/18 8:04 PM PT), edited in place, text only: QE 007's rev 12 FAIL (R1 pass choice + tie statistic +
check-pass setting; R2 W1 chain starts at 1.0 m) and nits N1–N5. No bar is loosened; no data exists yet.**
- **Source: Nick's own comments in the Claude Doc "Pool test 1 debrief"** (https://claude.ai/artifact/4x3hTr8GsKWesoQs9tXUUg; read by
  PE 003 with the docs connector `query`, author = the user, Nick; times PT), verbatim:
  - 10:33 AM, thread 468e59ea, on W2: "I like this idea, sadly there is algea on the walls and floor of the pool so any time I
    move i kick up sediment. I don't think this is the best use of time as written, instead I think I should swim with the
    reference board in my hands starting from far away and then move towards the cameras, then the video can see when it can
    detect the april tags and measure the board size to get a reference for size and distance. That can act as my seiki disk."
  - 10:35 AM, thread 64518e06, on W4: "Yes lets add this, this can be part of the 1m, 2m, 3m distance ladder and we should shoot
    1/2 and full color for the N6 and AE3 in still images first to see." Then 10:35 AM, thread d911e020: "oh I see, vidoe even
    better!"
  - 10:36 AM, thread 563cce3b, on W5: "Yes I like this too".
  - 10:39 AM, thread b1cb6a07: urchin cutouts taped in the white space around the v3 colour card, "so we also get the v3
    color card in frame".
  - 10:44 AM, thread 6781294a, on decision 1: "Yes add W1-W5 please".
  - 10:45 AM, thread 31803990, on decision 2: "Yes, 30px for the 8cm target. I'd like you to prepare 4x samples that I can print
    and laminate. I'll get these printed locally and mount them to my existing board. Also good to have a standard 8cm and 5cm
    black and grey target (grey so I dont get clippng) and I'll print these also".
  - 10:45 AM, thread 5cf67378, on decision 3 (W4): "Yes include please".
  - In the PE chat, ~5:15 PM: "I'll start the new PE once we finish the spec".
  This rev specs the test; Nick still approves the Wed card and the envelope on Tue (rev 1 Done).
- **Why:** rev 9 measures fps, completeness and sync. W1–W5 answer Nick's 8:50 AM ask: can each camera see an urchin, and at
  what range, in real pool water. The floor stops of rev 10 are dropped (sediment).
- **The board (must do 16):** Nick's existing AprilTag reference board, i.e. the v3 colour card with its corner tags (tag25h9,
  LESSONS §5). In its white margin, laminated and taped:
  - the print sheet's four urchin samples (2 × 80 mm, 2 × 50 mm; diameter = spine tip to spine tip);
  - 80 mm and 50 mm black-and-grey quartered disks (no white);
  - one slanted-edge square (black on mid-grey, ≥ 50 mm, rotated ~5°).
  Print sheet: the 2-page US Letter PDF attached in the debrief doc (or a regenerated equivalent). The rig session records its
  sha256 in the run MANIFEST. **Print check:** Nick measures the 10 cm bar and one 80 mm sample with a ruler, ±2 mm, else
  reprint. **Before the dive:** Nick measures one tag's black-square edge in mm; it goes on the Wed card and sets the range.
- **Must do 17, the swim (W1 + W2 + W3 + W4 in one swim):** after rev 9's N6-vs-AE3 clips, never before them.
  1. **Pass 1 (full colour):** the N6 and AE3 record full colour (4:4:4); the IMX708 at its Wed setting. The diver holds the
     board face-on to the rig, centred between the N6 and AE3 axes, mid-water, never touching the floor or walls. Start ~4 m
     out, or past where the tags vanish. Swim slowly straight in to ~0.5 m, holding still ~5 s at about 3, 2 and 1 m (by eye
     off the floor tape; the tags give the true range). All three cameras record.
  2. **Pass 2 (half colour):** the same swim with the N6 and AE3 at half colour (4:2:0) (W4); the IMX708 unchanged. Under F15
     the N6 runs half colour on both passes.
  3. **Check pass** at the end: one more swim-in at pass 1's settings, written as its own phase in the program string (F12
     drift check; scoring happens after the dive, so no "winning" setting is known at the pool).
  4. The chroma change between passes is a phase in the Wed program string (the passthrough PR's per-phase args), never a
     manual command. Each clip records its actual settings (passthrough condition 2).
- **Must do 18, W5 swim-past:** after the swims, two crossings of the frame at ~1 m mid-water with the board face-on, one slow
  and one at swimming speed.
- **Range and speed:** every frame's range comes from the board's tag pose, using bm #184's centre focal lengths (fx/fy; NOT
  VALIDATED as a lens model, but centre-stable) and Nick's measured tag size. bm #184's fx/fy are at 1280×800. They are
  scaled to a Wed resolution only if that mode is a full-field scale: the N6's 960×600 is (TEST_CARD_B); for the AE3's
  640×400 the rig session states, from the sensor-mode table, whether it is binned (scale) or windowed (no scale; the
  principal point shifts). The fx/fy values used are written on the Wed card before Tue noon. Speed comes from the pose change between frames on
  the capture clock (rev 5). Frames where no tag decodes have no range and are never scored for W1.
- **Scoring (on the Mac, after the dive; code on the rig PR with QE before any W result):**
  - **W1, per camera, on the pass that ran that camera's Wed part-B setting (the chroma of its part-B "holds" row on the Wed
    card).** If both passes ran the same setting for that camera (always the IMX708; the N6 under F15; any camera under F18),
    W1 uses pass 1. Range bins of 0.5 m centred on 0.5, 1.0 … 4.0 m. In each bin, 5 frames where the board is whole in that
    camera's frame (frame rule below).
    - Per frame: pixels across each 80 mm and 50 mm sample, taken from the tag homography and the samples' measured positions
      on the board (Nick, or the rig session from a flat photo of the mounted board, before the dive), never drawn by hand.
    - Detector: the urchin pilot's zero-shot model, prompt "purple sea urchin", checkpoint and score threshold written as values
      on the Wed card from rig #123's merged pilot config. A hit = IoU ≥ 0.5 against the reference box, which is each sample's
      outline projected through the same tag homography (no hand-drawn step). The box file is written and committed before the
      detector runs, its sha in RESULTS.
    - MTF50 from the slanted edge, in line pairs per mm at the board.
    - Colour error (CIEDE2000) per v3 patch on uncorrected output.
  - **W1 pass/fail (set now, before any data):**
    - A camera sees an urchin in bin d when **both** 80 mm samples are ≥ 30 px across **in each of the bin's 5 frames**
      **and** each is hit in ≥ 4 of 5 frames.
    - **Which 5 frames:** hold frames are those with board speed < 0.1 m/s (from the tag pose). If a bin has ≥ 5 hold frames,
      its candidates are its hold frames; otherwise all its whole-board frames. Over the N candidates in time order, take
      indices round(i·(N−1)/4), i = 0…4.
    - Its detectable range = the largest d where that holds and also holds in every closer bin from the chain's first bin. A
      bin with fewer than 5 scorable frames breaks the chain.
    - **The chain starts at the 1.0 m bin for every camera (rev 13, R2).** The 0.5 m bin is report-only: at ~0.5 m the board
      with its samples doesn't fit whole in the N6 or AE3 view (bm #184 underwater fx/fy at 1280×800: the N6 covers 0.79·d ×
      0.49·d, the AE3 0.92·d × 0.57·d; the 420 × 270 mm card alone is whole only from d ≈ 0.55 m / 0.47 m). Before Tue noon
      the rig session computes, from those fx/fy at Wed's resolution and Nick's measured board outline (with the samples, and
      the board centred between the two axes), whether the whole board fits each camera at 1.0 m. If it doesn't for a camera,
      that camera's chain starts at the first bin centre where it does, written on the Wed card before any data.
    - The same is reported for the 50 mm samples.
    - N6 vs AE3: the longer range wins; a tie goes to the higher MTF50 (lp/mm) in the 1.0 m bin, where a camera's MTF50 is
      the median over that bin's 5 W1 frames.
    - **Fallback:** if the detector hits in < 4 of 5 frames in the chain's first bin (1.0 m unless the Wed card says
      otherwise) on every camera, it's replaced by a blind yes/no on shuffled crops (≥ 4 of 5 yes per sample), and RESULTS
      says so. The blind viewer is a fresh session the PjM names, given only the crops under neutral names, with no range,
      camera or frame order.
    - The 30 px line is Nick's (10:45 AM). After data it can only tighten (§13).
  - **W2 (report-only), per camera:** the range at the first frame where ≥ 1 tag decodes on pass 1 (the "camera Secchi"
    reading). Repeated on the check pass.
  - **W3 (report-only):** CIEDE2000 per patch, median and worst, per camera, in the 1.0 and 2.0 m bins.
  - **W4 (report-only; Nick judges by eye):** for the N6 and AE3, matched frames at the 3, 2 and 1 m holds, full vs half
    colour side by side, with the same MTF50 and CIEDE2000 numbers. A W4 pass may run below 9.5 fps: allowed here only, with
    fps logged. It changes no part-B "holds" verdict.
  - **W5 (report-only):** the edge blur width (10–90 %, px) of the slanted edge and tag edges against measured speed, per camera,
    both crossings.
- **Failure modes (new rows; replace rev 10's F10–F14):**
  - F10 Tag range wrong (tag size mis-measured, fx provisional). Detected: the three cameras' tag ranges at the same instant
    disagree by > 10 %. Then: flag those frames; if > 25 % of a pass is flagged, its W1 range is "not measured".
  - F11 Board cut by a frame edge (the AE3 view sits right of the N6's). Detected per frame. Then: excluded for that camera;
    counts toward the < 5-frames chain break.
  - F12 Water changes during the block (silt). Detected: W2's check-pass range differs from pass 1 by > 20 %. Then: flagged in
    RESULTS; ranges stand with the flag.
  - F13 Print scale wrong. Caught by the print check.
  - F14 Pool time overrun. The block is time-boxed at 30 min. At the box, stop and keep what's recorded; pass 1 comes first.
  - F15 **The N6 can't record full colour:** its hardware JPEG encoder is 4:2:0 only (rev 6). Part B runs one 4:4:4 probe clip on
    the N6 before Tue noon. It is a new row on TEST_CARD_B (a rev 3 of that card on the pool-2 successor's next PR, not a push
    to the QE-passed rig #125, https://github.com/nickraymond/nereus-camera-test-rig/pull/125), with its bar: ≥ 5 fps over a
    30 s clip. If it fails, or drops below 5 fps, W4 runs the AE3 only, and the Wed card says so.
  - F16 Tags stop decoding before an 80 mm sample drops under 30 px. W1's range is then capped at the last decoded bin and
    reported as "≥ that range (tag-limited)". Every camera's chain end is reported with its reason: px, hits, < 5 scorable
    frames, or tag-limited. Tag-limited applies only when the chain held through the farthest bin that has tag-decoded
    frames.
  - F17 Swim motion blurs frames. Hold frames are used first (see the W1 frame rule); MTF50 is reported per bin.
  - F18 The chroma phase switch fails mid-dive. Caught by each clip's recorded settings (passthrough condition 2); that pass
    becomes W1-only.
- **Cost:** pool ~25 min (passes ~15, W4 ~2 extra, W5 ~3, check pass ~5), within the 30-min box. Rig session desk ~3 h
  (scoring code, the N6 probe). Nick ~20 min (print, laminate, mount, measure). Disk: ~8 × 30 s clips ≈ 0.6 GB (≤ 1 GB),
  added to Wed's SD and pull plan.
- **Card, envelope, owners:**
  - The pool test 2 session commits this text byte-identical (QE byte-check), puts must dos 16–18 on the Tue 3 PM card after
    the N6-vs-AE3 clips, and writes the scoring code (QE before results).
  - The PjM adds the program string's phases to the envelope's rig section. No new verbs; no manual commands.
  - Out of scope: detector accuracy on real urchins (E6); lens correction (d58); per-m² counts (d33).

## Amendment rev 14 (PE 004, Sun 10/18 8:51 PM PT), rewritten in place as rev 15 (8:55 PM PT; QE 008's rev 14 FAIL R1–R3): CMA headroom before Wed (must do 19, F19). Binding, before any Wed data.
- **Why:** B0 on nereus002, Sun 8:40–8:50 PM PT (build a9ac2af, lit bench), clip01 host_stats.csv (read by QE 008): CmaFree
  31 → 3 MB in one second at camera start (mem_avail 167 → 138 MB), i.e. real buffer allocation, then drift to 0–2 MB with
  mem_avail flat, i.e. page-cache borrowing (line 249). In-clip mem_avail 129–130 MB. No crash, no throttling. CmaFree alone is
  therefore not headroom; the risk to measure is drivers running out of CMA (the 10/13 hard-reset pattern).
- **Must do 19 (before the Tue 3 PM card; final build rig #125 + #126; the exact Wed program, N6 and AE3 at their Wed modes):**
  three 30 s clips per setting, in order (a) IMX708 `--buffer-count 4` (tonight's), (b) `--buffer-count 2`. Per clip, record:
  - driver-held CMA peak: `/sys/kernel/debug/cma/*/used` vs `count`, sampled ≥ 1 Hz (if debugfs isn't readable: "unknown",
    say so on the card, and judge on the next two bars only);
  - CMA / dma-buf allocation errors in `dmesg` for the clip window;
  - lost frames, the rev 9 fps / max_gap per camera, any host reset (uptime), and mem_avail (report-only, rev 4 flag 150 MB).
  - **The CMA bar for a setting, all 3 clips:** 0 CMA/dma-buf allocation errors AND driver-held CMA peak ≤ 90 % of the pool.
    (unknown: the driver-held CMA at the 10/13 resets; the rig session looks for it in that run's logs. If found, the bar
    becomes 10 % below that reading, written on the card before must do 19 runs.)
  - **The capture bar, all 3 clips:** 0 lost frames, rev 9 fps / max_gap holds, no host reset.
- **What happens (branch by the bar that failed):**
  - Both bars pass at (a) or (b) → the Wed program uses the first that passes.
  - Only the CMA bar fails at both → raise the CMA pool in the boot config (backup, restore command and the exact line on the
    card), reboot, re-run (a) then (b). It lands by the Tue 6 PM rig freeze and is announced on the board (§10).
  - The capture bar fails, or the CMA bar still fails after the raise → STOP: the data goes to the PE + Nick on the card. Any
    change to E1 (N6 at HD, his ask) is Nick's call (§13).
- **F19 (new row): CMA runs out during a Wed clip** → a hard reset or a lost clip. Detected: dmesg CMA/dma-buf errors and
  driver-held CMA per clip; a reset shows as an uptime reset in the host log. Then: that pass is "lost", the next pass runs, and
  the 30-min box (F14) holds.

## Amendment rev 16 (PE 005, Sun 10/18 9:30 PM PT): tag ranges from undistorted tag corners. Binding, before any Wed data.
- **Why:** pool-2 successor 3's dry run of the W scoring code on Friday's pool049 clips 099–103
  (`~/NereusData/nereus-camera-test-rig/2026-10-18_pool2_wscore_dryrun/`, F10_DRYRUN.txt, MANIFEST.md): with raw corners, F10
  flags 85 / 15 / 62 / 52 / 43 % of matched instants (≤ 50 ms), so 4 of 5 clips are past the 25 % "not measured" line; the N6
  reads 5–11 % farther than the AE3 (medians). With the corners undistorted by bm #184's own dist_coeffs: N6/AE3 1.054 → 0.982
  (clip100, n 278) and 1.110 → 0.985 (clip102, n 144); IMX708/AE3 0.996 → 0.997 (clip102, n 227). So the disagreement is the
  N6's barrel distortion (k1 −0.42) on off-centre tags, not a wrong range, and F10 as written would void W1 for it. Friday's
  board was not held as Wed's: this is evidence about the method, not Wed data.
- **Change to "Range and speed":** before the pose, every decoded tag's corners are undistorted (`cv2.undistortPoints` or
  equivalent) with the camera matrix and dist_coeffs of the same bm #184 fit whose fx/fy the Wed card cites (same file and
  sha, written on the card before Tue noon). K is mapped to the Wed resolution by the existing scale / window rule; the
  coefficients are unchanged (normalised coordinates). Range, speed, W2, F10 and the board pose use these corners.
- **The image is never undistorted.** The detector, MTF50, colour and every pixel-size test (the 30 px gate) use the raw frame.
  Sample boxes placed from the pose are projected back into raw-image pixels with the same K and coefficients
  (`cv2.projectPoints` with dist_coeffs), and their px are measured there.
- **Guard:** a camera whose Wed mode can't be mapped to the fit's K (a windowed mode with an unknown offset) keeps raw corners;
  the card says so, and F10 judges it as written.
- **Unchanged:** F10's 10 % and 25 % lines and every W1–W5 bar. F10 stays the check on the lens model, which is unvalidated in
  the bottom ~40 % of each frame (c_charuco: B3/B5c FAIL): a bad extrapolation there shows as cross-camera disagreement and is
  flagged.
- **Report-only:** RESULTS gives each pass's F10 share and per-pair median range ratios both raw and undistorted. Decisions use
  the undistorted ones.
- **Tests (rig PR, before any W result):** (1) a synthetic tag projected with k1 = −0.42 at 1.0 m, off-centre, gives its true
  range within 1 % after undistortion and is ≥ 5 % off without it; (2) a sample box projected back lands within 0.5 px of its
  raw-image position; (3) the guard path keeps raw corners and labels the camera.

## Amendment rev 17 (PE 005, Sun 10/18 9:43 PM PT): the W1 detector's checkpoint and threshold as values. Binding, before any Wed data.
- **Gap (pool-2 successor 3):** N1 and the W1 detector line say "checkpoint and score threshold … from rig #123's merged pilot
  config", but #123 (merged 4d6f7d9) has no single threshold: `edge_ml/urchin/pilot.py:51` `CAND_SCORES = (0.05, 0.3)` is a
  pre-label floor and a "strong" line. E6's frozen threshold (F1-max on validation) won't exist before Tue noon.
- **Pinned now:** checkpoint `google/owlv2-base-patch16-ensemble` at revision `cfd3195ba4ea9592eec887ded089f4c08eff231d` (the
  only cached snapshot on this Mac, checked 9:43 PM; the scorer refuses any other revision); score threshold **0.3** =
  `CAND_SCORES[1]` @ 4d6f7d9. Both go on the Wed card as values (N1). Why 0.3, not 0.05: at 0.05 OWLv2 "fires on nearly every
  still" (pilot RESULTS @ e127321, line 69), so an IoU ≥ 0.5 hit on the reference box would say little. The same value for all
  three cameras; W1 ranks N6 vs AE3, so the comparison doesn't hinge on the exact cut.
- **Report-only:** RESULTS also gives each camera's chain at 0.05 and 0.5, next to the 0.3 result. Decisions use 0.3.
- Unchanged: the 4-of-5 rule, IoU ≥ 0.5, the 30 px gate, the blind-viewer fallback, every bar.

## Amendment rev 18 (PE 005, Sun 10/18 11:03 PM PT): readings 10–16 from rig #133 pinned. Binding, before any Wed data.
Source: pool-2 successor 4's fix comment on rig #133 @ 305b81c (https://github.com/nickraymond/nereus-camera-test-rig/pull/133#issuecomment-6260772610),
items 10–16, raised by QE 008 (N4, N6, reading 5, R6). Readings 1–9 in the PR body stay with QE.
- **10, px across (30 px gate):** the **shortest** projected diameter of the sample's outline in raw pixels (strict; on a
  tilted board the detector sees the foreshortened width). The longest is reported beside it. Changed from the PR's longest.
- **11, frame-pick rounding:** half up, floor(x + 0.5).
- **12, tie-break MTF50 median:** over the bin's W1 frames that have an MTF50 value; if fewer than 3 of the 5 have one, the
  tie-break is "undecided (MTF50)".
- **13, tag-limited ranges (F16):** a tag-limited range is a lower bound. N6 vs AE3: a tag-limited camera wins only if its
  lower bound is longer than the other's range; otherwise the result is "undecided (tag-limited)", and a tie is never broken
  by MTF50 when either side is tag-limited.
- **14, F10 on 2-camera instants:** judged when the third camera has no ranged frame within 50 ms (> 10 % between the two
  flags it). 1-camera instants are not judged; their count is reported, and they don't enter the 25 % denominator.
- **15, IMX708 clip with no ScalerCrop:** full-sensor scale is assumed, and that clip's IMX708 ranges are flagged in RESULTS.
- **16, guard principal point:** the frame centre (offset unknown), stated in the lens table in RESULTS.
- **Files:** the card-values file and the box files live in `docs/pool2_20261021/` (`results/` is git-ignored, .gitignore:20),
  as `.json` / `.md` / `.csv` only (TEAM_RULES §11).

## Amendment rev 19 (PE 005, Mon 10/19 9:46 AM PT; sent by PE 006; rev 19.1 PE 006 9:58 AM = QE 009 nits N1–N3, text only): the Wed program pinned. Binding, before any Wed data.
Source: pool-2 successor 4's draft (parses with `pool_capture.parse_program` @ 920e58f, sha256 a605ebf1a8be…), after the
Mon 9:06–9:29 AM ladder + must do 19 run (`~/NereusData/nereus-camera-test-rig/2026-10-19_pool2_ladder/RESULTS.md`: n6_hd HOLDS 3/3,
ae3_hd FAILS 3.17 fps so the AE3 stays 640×400 4:2:0, md19_buf4 PASS).
- **Program (pinned; one line, copy it whole: sha256 of the string without a trailing newline = a605ebf1a8be…):**

  `video@ctl:1x30,video@best:6x30[--n6-framesize HD --ae3-subsampling 420],video@w_pass1:1x120[--n6-framesize HD --n6-subsampling 444 --ae3-subsampling 444],video@w_pass2:1x120[--n6-framesize HD --ae3-subsampling 420],video@w_check:1x120[--n6-framesize HD --n6-subsampling 444 --ae3-subsampling 444],video@w5:1x60[--n6-framesize HD --ae3-subsampling 420]`

  ~18 min from switch-on, IMX708 at 4 buffers (md19_buf4). The Wed card carries the string and its sha256 byte for byte.
- **1, must do 13's n for the N6-vs-AE3 clips: n = 6 × 30 s** at both cameras' part-B settings, scored per clip (median and
  range across the 6, never one clip).
- **2, long clips:** one 120 s clip per swim pass is pinned. Must do 19 tested 30 s clips only, so the one bench run of the exact
  program on the final build (before the Tue 6 PM rig freeze) also judges every clip, the 120 s and 60 s ones included, on must
  do 19's two bars (CMA: 0 alloc errors; capture: the rev 9 fps / max_gap bars, no reset). Any long clip failing either bar →
  that phase becomes 4 × 30 s (60 s → 2 × 30 s), and the bench run repeats. No separate md19 label. If that fallback fires, the new string and its sha256 are re-pinned (rev 19.x, QE re-check) before the Wed card is final.
- **3, W5:** one 60 s clip holding both crossings, slow then fast; the crossings are separated in scoring by the measured speed.
- **4, must do 14 (close ChArUco re-poses) is OUT for Wed.** Nick on d58 (board comment, Sat 10/17 3:39 PM, verbatim): "I can rerun
  the test, but first I need a better mechanical mount and a way to get data from the rig to the surface to check the alignment.
  So this is no an option for Wednesday. We should add to the backlock and check back in Nov" (c_calib_redive_nov).
- **5, card values:** part-B N6 HD 4:2:0, AE3 640×400 4:2:0, so W1 = pass 2 (rev 13 R3); guard cameras none (the session's reading
  that HD and VGA both map to the fit's K; QE checks it in the code); `first_bin` waits on Nick's board outline (blk_board_outline).
- **SD:** the estimate is now ≈ 1.36 GB (measured 64.8 MB per 30 s) (was ~0.6 GB in the cost line); the SD rules stand: ≥ 2 GB free at every reading, and before
  the dive SD free ≥ 2 GB + 2 × 1.36 GB (≈ 4.72 GB).

## Amendment rev 19.2 (PE 006, Mon 10/19 10:01 AM PT): d57's "most clips usable" for Wed's 6 `best` clips. Binding, before any Wed data.
- Asked by pool-2 successor 4 (rig #135 WED_CARD.md §6). d57 (Nick's "Approved.", thread 8b58f7d2, Sat 3:26 PM) says the N6 runs
  the setting "where most clips are usable" (each ≥ 95 % of frames, d52); rev 7 pinned "most" as a strict majority (≥ 2 of 3).
  Same rule on Wed: the N6's `best` phase is usable when **≥ 4 of its 6 clips** are usable, scored per clip (rev 19 item 1).
  The AE3 keeps its own bars (rev 7: d57 names the N6 only).

## Amendment rev 19.3 (PE 006, Mon 10/19 2:18 PM PT): which fps bar rev 19 item 2 applies to the full-colour (4:4:4) phases. No bar changes.
- Asked by pool-2 successor 4 before scoring the exact-program bench run (pool052, build 920e58f, program a605ebf1a8be). Rev 19
  item 2 says "the rev 9 fps / max_gap bars" for every clip. For `w_pass1` and `w_check` (the W4 full-colour passes) the spec
  already set other fps bars before any of this data: W4 passes "may run below 9.5 fps: allowed here only, with fps logged"
  (W4 line above); the N6 4:4:4 bar is **≥ 5 fps** over 30 s (F15); the AE3's fallback is **≤ 0.2 fps below its own 640×400
  rate** (the part-B fps bar), here its B0 640×400 4:4:4 rate, quoted from the B0 results file with its path. Those bars apply
  to those two phases. Item 2's other bars stay for every clip: 0 CMA alloc errors, AE3 0 frames lost, N6 usable (d52/d57),
  IMX708 ≥ 26 fps and max_gap ≤ 100 ms, no reset. Every other phase keeps ≥ 9.5 fps.
- This picks between bars already written before the run; it loosens no number. If QE reads it as a loosening, it goes to Nick.
- **Rev 19.4 (PE 006, Mon 10/19 2:19 PM PT; QE 009's 19.3 nit, before pool052 is scored): the AE3 4:4:4 bar is ≥ 9.27 fps**, measured
  as `fps_clip`. Source: the B0 4:4:4 rate 9.47 fps (`fps_clip`, rows 4 and 16, subsampling_measured 444, of
  ~/NereusData/nereus-camera-test-rig/2026-10-17_pool2_prep/bench/B0_clip_table.csv; also rig #135 WED_CARD.md:25) − 0.2.

## Amendment rev 19.5 (PE 006, Mon 10/19 2:29 PM PT): program re-pinned after the bench run (pool052). Binding, before any Wed data.
- Pool-2 successor 4 found on pool052 (920e58f, program a605ebf1a8be) that every video phase starts one period late:
  scripts/pool_capture.py:861–864 @ 920e58f puts the first clip at k = ceil(elapsed / period), so a set that begins even 1 s
  after the phase start lands on k = 1 (PE 006 read the code). W5's only slot then fell on the program end and was lost.
- **Fix, program only (no code change, so no gate re-run; rev 3 rule 4):** W5 becomes `w5:2x60`, whose k = 2 slot falls inside
  its own phase: one 60 s clip. Every other phase is unchanged. The Wed card's table uses the measured clip start times. The
  exact program is re-run once on the bench and judged on rev 19 item 2 / 19.3 / 19.4's bars before the card is final.
- **Program (pinned; one line; sha256 of the string without a trailing newline = 87ee096f158a…):**

  `video@ctl:1x30,video@best:6x30[--n6-framesize HD --ae3-subsampling 420],video@w_pass1:1x120[--n6-framesize HD --n6-subsampling 444 --ae3-subsampling 444],video@w_pass2:1x120[--n6-framesize HD --ae3-subsampling 420],video@w_check:1x120[--n6-framesize HD --n6-subsampling 444 --ae3-subsampling 444],video@w5:2x60[--n6-framesize HD --ae3-subsampling 420]`

  It replaces rev 19's a605ebf1a8be string. The late-start behaviour goes on a post-Wed card for pool_capture (not fixed now).
