# Spec: pool test 2 prep (Wed 10/21): Friday video review + N6/AE3 resolution-at-10-fps bench study + Wed test card (PE rev 5, tier (a); rev 2–5 amendments at the end)

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
