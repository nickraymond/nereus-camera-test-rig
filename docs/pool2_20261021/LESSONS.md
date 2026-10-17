# Pool test 2 prep — part A: Friday's videos and the combined lessons list

Spec: [SPEC.md](SPEC.md) rev 5 (must-dos 1–3; amendment rev 2 item 5). First written Sat 10/17 3:15 PM PT by the pool-2
prep session; updated through Tue 10/20 (lens results, part B). Source: run pool049 on nereus002 (rig main@56d49b5),
clips clip098–clip103, 2:57–3:03 PM PT Fri 10/16, raw files in
`~/NereusPool/20261016/nereus002/pool049_20261016T212527Z/video/`. Images and full tables:
`~/NereusData/nereus-camera-test-rig/2026-10-17_pool2_prep/partA/` (MANIFEST.md there).

## Summary for Nick (one page)

**What we learned**
1. **Friday's video worked on all three cameras.** 6 of 6 clips complete, 0 board frames lost, start skew 8–24 ms, the
   Pi never throttled. Lowest free CMA was 54 MB (clip100) and lowest free RAM 181 MB.
2. **The N6 and AE3 recorded MJPEG at 640×400 (JPEG quality 70), not H.264.** N6: 10.0 fps, 8.1–9.4 MB per 30 s.
   AE3: 9.32–9.34 fps, 7.2–7.4 MB per 30 s. The AE3 is slow because each frame takes it ~107 ms: its JPEG encoder is
   software, and the snapshot and the USB send come on top of it. It's not a timer problem, so a timing tweak alone
   won't reach 10 fps.
3. **H.264 on the N6 isn't available in any OpenMV build.** The firmware that "landed last week" is OpenMV's rolling
   Development Release (10/16); it has no H.264. H.264 exists only as an unmerged draft pull request
   ([openmv#3247](https://github.com/openmv/openmv/pull/3247), last touched 9/14). So no firmware update for Wed.
4. **N6 at HD (1280×800) at 10 fps looks feasible, with one doubt.** The N6 has hardware JPEG, and on 10/2 the
   previous N6 ran HD at quality 70 at ~30 fps (~97 kB per frame). At 10 fps that's ~1 MB/s over USB and ~29 MB per
   30 s (3.4× Friday's N6). The doubt: the rig notes call today's N6 a full-speed (12 Mbit/s) USB device, and ~1 MB/s
   is near that link's limit. Part B checks the link first, then measures HD with the other two cameras recording.
5. **The AE3 can't do HD at 10 fps.** Its software JPEG manages ~2.3 fps at HD (your S3 bench). It stays at 640×400;
   part B tries to get it from 9.3 to ≥ 9.5 fps there. On a simpler bench scene it already held 10.0 fps (10/14), so
   the pool scene is the harder case and the bench must use a busy scene.
6. **Field of view, from the card's tags:** the IMX708 sees ~2× wider than the N6, the AE3 ~1.1× wider than the N6.
   At 640×400 a target gets about 2/3 as many pixels on the N6 as on the IMX708 at 1080p. At HD the N6 would give
   ~1.3× **more** pixels on target than the IMX708 at 1080p.
7. **By eye, at matched moments (within 23 ms):**
   - The N6 has more colour and contrast. It shows rainbow fringes on bright ripples on the floor.
   - The AE3 is flatter and hazier, and slightly softer. The card's tags read on both.
   - The IMX708 is green (out of scope this round), the housing port vignettes its corners, and fast hands blur at
     its ~17 ms exposure.
8. **The Mac viewing copies drift.** They play at the nominal 10 / 30 fps, but the AE3 delivered 9.33 and the IMX708
   26.4. Over a 30 s clip the AE3 copy ends 1.9 s early, and the IMX708 copy plays ~13 % fast. Compare cameras with
   the matched frames, not by playing the copies side by side.

**Changes for Wed 10/21** (≤ 10; owners as the board names them)

| # | Change | Owner | Before Wed |
|---|---|---|---|
| 1 | N6 to HD 1280×800 MJPEG q70 at 10 fps, if it holds the bars in part B; Friday's 640×400 stays as the control | pool-2 prep session | yes |
| 2 | AE3 stays at 640×400. B0: measure snapshot / JPEG / USB time per frame and trim it to reach ≥ 9.5 fps; if that fails, the AE3 uses its relative bar and its cap is reported | pool-2 prep session | yes |
| 3 | N6 resync fix (aa06bc7: a USB byte loss costs one frame, not the rest of the clip) in the pool-2 build, before the resolution steps, if d53 says yes | Nick (d53, Sun AM) → nereus002 rig session's fix, deployed under the hand-over | yes, if d53 |
| 4 | Recording harness: per-board settings (the N6 and AE3 now differ); the gate scores video completeness separately (d52) and adds the process check (G-a) and SD readings (G-b, QE N1–N7) | pool-2 prep session (rig PR #121) | yes |
| 5 | Data budget from part B's measured sizes: an HD N6 clip is ~3.4× Friday's, so the SD check, the Mac's free space and the pull plan (pool Wi-Fi ~0.4 MB/s: fast pass only) are sized from real numbers | pool-2 prep session (numbers) → PjM 003 (envelope c_envelope_1021) | yes |
| 6 | Battery: charge to ≥ 3.4 V before leaving. Friday started on battery at 3.244 V and ended at 3.21 V, on the 3.20 V low-battery line | PjM 003 (readiness checklist), Nick | yes |
| 7 | Dive-autostart guard: `touch ~/.no_dive_autostart` before any reboot, check no ci-record runs before each recording, and leave the flag as the owner says at hand-back | every rig borrower; nereus002 rig session sets the hand-back state | yes |
| 8 | Lens corrections on the Mac after the dive, only for the cameras that pass B1–B6 (due Tue noon); if the N6 or AE3 fail corner coverage (B3), Wed's card adds close ChArUco re-poses | lens calibration session → pool-2 prep session (Wed card) | yes (decided Tue noon) |
| 9 | AE3 HD still "Compression Failed!" after surfacing: 4 failed stills (sets 113–115, 120), then benched to set 124. Stills only, not video. End the stills program before surfacing, or log it as known | nereus002 rig session (new card) | no |
| 10 | Viewing copies built from the measured timestamps (`imx708.pts`, board ticks), so side-by-side playback stays in sync | PjM 003 to assign (PgM 003 made Friday's copies) | no |

Not changed this round (your call, Sat 2:08 PM): the IMX708's green colour, rewiring, new glass.

## 1. Friday clip facts (must-do 1)

From the raw clips and `video_sync.json` only (`scripts/video_clip_table.py`; full table:
[fri_clip_table.csv](fri_clip_table.csv)). Ranges over the 6 clips:

| Camera | Codec | Resolution | fps asked → delivered | Frames lost | Frame period (median) | MB / 30 s | Start offset |
|---|---|---|---|---|---|---|---|
| IMX708 | H.264, Pi hardware | 1920×1080 | 30 → 26.24–26.67 | 100–113 dropped of ~900 (single frames; max gap 66.7–100 ms) | 33.3 ms | 33.0–33.6 | 0–24.3 ms |
| N6 | MJPEG q70, on the board | 640×400 | 10 → 10.0 | 0 | 100 ms | 8.13–9.41 (27–31 kB/frame) | 0–8.2 ms |
| AE3 | MJPEG q70, on the board | 640×400 | 10 → 9.32–9.34 | 0 | 107 ms (106–111) | 7.16–7.35 (25.5–26.3 kB/frame) | 8.2–17.1 ms |

- Start skew per clip: 24.3, 16.7, 8.2, 20.0, 17.1, 21.1 ms (all ≤ 25 ms).
- Host, per clip: min mem_avail 181–193 MB, min CMA free 54–60 MB, max CPU 54.8–56.4 °C, `throttled` 0x0 throughout.
- IMX708 MB / 30 s counts the whole `.h264` (it includes ~3 s of pre-roll) over its own duration; the file itself is
  37.8 MB on average.
- Fire → first frame: N6 8–9 ms, AE3 16–17 ms, IMX708 0.8–33.3 ms. The IMX708 starts on its next frame after the fire,
  so it adds a 0–33 ms term to the skew: half of Friday's clips sat at 20–24 ms against the 25 ms bar.

## 2. Matched frames, N6 vs AE3 vs IMX708 (must-do 2)

`scripts/matched_frames.py`, rev 5 method:
- **Clocks.** Every frame has a capture time on the Pi's monotonic clock:
  - IMX708: `SensorTimestamp`, near exposure start.
  - Boards: the snapshot's ticks, mapped to the Pi clock through the fire receipt.
- **Matching.** The reference is the AE3 frame, the slowest stream. A moment is kept only if all three frames fall
  within 50 ms of each other.
- **Kept:** 7 of 7, with spans of 4–23 ms ([fri_matched_frames.csv](fri_matched_frames.csv)).
- **Why not host_rx.** `host_rx_mono` lags the capture by 101–135 ms (N6) and 215–218 ms (AE3), so it is kept only as a
  reference column.

Moments: clip098 10 s (diver with the ChArUco board), clip099 25 s (card close), clip100 5 s (card at mid range), clip101
15 s and clip102 10 s (card on the floor, ~2 m), clip103 0 s (hand passing close) and 27 s.

**Field of view** (mean tag edge in pixels at the same moment):

| Ratio | Moments with common tags | Value |
|---|---|---|
| AE3 / N6 (same 640 px width) | 3 | 0.89–0.95 → the AE3 sees ~1.1× wider |
| IMX708 (1920 px) / N6 (640 px) | 6 | 1.44–1.57 → per pixel the IMX708 resolves ~half as finely, i.e. ~2× wider |

**By eye** (human review, not a metric):
- **Colour and contrast:** the N6 is the most saturated. The AE3 is lower contrast and hazier.
- **Sharpness:** at ~2 m the card's tags read on both. The AE3 is slightly softer.
- **Noise:** similar at this light.
- **Artefacts:**
  - The N6 shows colour fringing on caustic highlights.
  - The IMX708 has port vignetting (dark circular corners) and the green cast (out of scope).
- **Motion blur:** a hand passing at ~0.5 m (clip103 0 s) is blurred on the IMX708 (exposure 16.9 ms), less on the
  boards.
- **Framing:** the boards point slightly differently. The AE3 view sits to the right of the N6's, so Wed's
  side-by-side crops are taken at equal ground footprint (F6).

## 3. What the documented past work already says

| Topic | Finding | Source |
|---|---|---|
| Resolutions | QVGA 320×200, VGA 640×400, HD 1280×800 (max) on both boards (our allowlists, verified 7/14–7/15). Per OpenMV's source, the N6 also accepts custom sizes up to HD (ISP-scaled from HD) and the AE3 only the three native ones; not tried on our boards | `openmv/*/board_config.py`; OpenMV `drivers/sensors/pag7936.c`, `boards/OPENMV_N6/board_config.h` |
| N6 HD speed | HD q70 29.6 fps uncapped, 96.6 kB/frame, 2.86 MB/s; snapshot alone 8.5 ms, JPEG q60–q90 25–31 ms | 10/2 codec bench (rig PR #88, not on main) |
| N6 H.264 | Not in v5.0.1 or the 10/16 Development Release; only draft PR openmv#3247 (`codec.H264Encoder`, N6 only, Annex-B) | GitHub API, checked Sat 3:08 PM |
| AE3 JPEG | Software (`OMV_JPEG_CODEC_ENABLE 0`); ~2.3 fps at HD (S3 bench); 98 ms per 4 grey JPEG planes vs 26 ms on the N6 | `scripts/sync_video.py:31`; `compression_study/REPORT.md`; OpenMV `boards/OPENMV_AE3/board_config.h` |
| AE3 at 10 fps | 10.0 fps (3000 frames, 0 lost) in a 5 min bench run in air (10/14); ~9.6 fps on the 10 s gate clips (10/15); 9.32–9.34 on Friday's underwater clips. So its rate depends on the scene (inferred: bigger, busier JPEGs take longer to encode and send). Friday's ~107 ms period is work, not sleep (no slack in the 100 ms loop) | `docs/rig_gate_20261014/RESULTS.md` (5 min run table, gate007 row); this table |
| N6 USB speed | RESULTS.md calls the N6 a full-speed (12 Mbit/s) device; HD at 10 fps needs ~1 MB/s (~8 Mbit/s), near full speed's practical limit. The previous N6 moved 2.9–6.1 MB/s on 10/2, which needs high speed. Part B checks `lsusb -t` first | `docs/rig_gate_20261014/RESULTS.md` ("N6 USB limit"); 10/2 bench |
| Start skew | gate007's 25 video clips: median 14 ms, p90 38 ms, max 55 ms. A 25 ms bar would have failed several of them on the IMX708's frame phase alone | `docs/rig_gate_20261014/RESULTS.md` (gate007 row) |
| N6 byte loss | 4 of ~119 bench clips lost the rest of the stream after a USB byte loss (Pi reader stall); fix aa06bc7 on `fix/n6-video-resync`, not in a build | `docs/rig_gate_20261014/RESULTS.md`; d53 |
| AE3 cold-boot wedge | 1 in 5 cold boots before the usb-storage quirk; fixed with `usb-storage.quirks=37c5:…:i`; 25/25 gate007, strict 25 in gate008 | same RESULTS.md; post_pool_ae3_wedge |
| Firmware updates | Done by Nick with OpenMV IDE on his PC; recovery from a bad bootloader needs hands at the bench | `docs/open_questions.md` |

## 4. Combined lessons list (video, lens, pool day; active sessions)

| # | Lesson | Evidence | Owner | Before Wed |
|---|---|---|---|---|
| L1 | Friday's 3-camera video is complete and synced, so it is a good baseline and Wed's 640×400 control | §1 | pool-2 prep session | — |
| L2 | AE3 fps is work-bound (~107 ms per frame underwater; 10.0 fps on a simpler bench scene), so measure per-frame time before trying to fix it, on a scene as busy as the pool's | §1, §3 | pool-2 prep session (B0) | yes |
| L3 | No N6 H.264 off the shelf; a build from the draft PR would be custom firmware work | §3 | Nick (option only) | no |
| L4 | N6 HD MJPEG is the realistic "best N6" for Wed | §3 | pool-2 prep session (part B) | yes |
| L5 | N6 USB byte loss can cut a clip short; the pause bar (> 5 % of clips at a setting) is set before data | d53 | Nick → nereus002 rig session | yes, if d53 |
| L6 | The start-skew bar (25 ms) sits next to the IMX708's 0–33 ms frame-phase term: Friday 8–24 ms, gate007 p90 38 ms. Part B records every camera's fire → first-frame delay so a skew miss can be traced | §1, §3 | pool-2 prep session; flagged to the PE Sat 3:20 PM | yes |
| L7 | Compare cameras on matched capture times, never on receive times or nominal-rate viewing copies | §2, summary 8 | pool-2 prep session; PjM 003 for copies | — |
| L8 | Lens corrections are Mac-side and per camera, gated on B1–B6; the IMX708 corners are masked by the port vignette | prop_lens_calib_pool049 | lens calibration session | Tue noon |
| L9 | Battery margin was thin (ended at 3.21 V on battery) | `power.csv` | PjM 003, Nick | yes |
| L10 | Pool Wi-Fi ~0.4 MB/s and lossy: fast pass at the pool, full pull at home; check the Mac's `df` first (13 GiB on Friday until the Trash was emptied) | c_envelope_1021, c_storage | PjM 003 | yes |
| L11 | Disarming re-enables Nick's dive autostart; every borrower sets `~/.no_dive_autostart` before a reboot | c_led_selfcheck 9:38 PM | every borrower | yes |
| L12 | AE3 HD stills fail ("Compression Failed!") in bright air at quality 95 | `pool.log` sets 113–124 | nereus002 rig session | no |
| L13 | Depth: the surface reference read 823.96 mbar against 1015.22 at the start, so absolute depth is ~+1.96 m off; relative depth works | `run_manifest.json` | nereus002 rig session | no |
| L14 | E7's board × encoder table and this study answer the same question for the N6 (no H.264 today); share the finding | prop_e7_cutsheet | E7 video screen session | no |
| L15 | The PgM's Friday retro (`retros/2026-10-16_pool.md`) isn't written yet; this list can seed it | team repo | Program Manager 004 | no |

## 5. Notes and deviations

- **Matching clock:** must-dos 2 and 12 were amended in rev 5 to capture time after this study found host_rx lags by
  100–220 ms (PE 002 agreed, Sat 3:00 PM).
- **Card tags:** Friday's card uses tag25h9 (the V3 layout), not tag36h11.
- **Data not used:** the Mac MP4 copies (viewing only, nominal rates).
- **Not measured here:** objective sharpness or noise metrics; part B and Wed's matched frames carry those.
