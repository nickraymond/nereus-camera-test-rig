# Spec card: 720p 5 s video, size-vs-quality curve + cut sheet for Nick (E7, d41) (draft rev 1, tier (a))

Tier (a) (TEAM_RULES §13): the study measures encode time and energy on nereus002's Pi (hardware). Home:
nereus-camera-test-rig `compression_study/video_cutsheet/SPEC.md`, committed first on the ticket's draft PR; QE spec PASS
before any code or run. Builds on PREREG v2 (`compression_study/video_100k/PREREG.md` @ 2c3750c) and rig #116.
Author: Principal Engineer, Fri 10/16 (time stamped on commit).

- **Ticket / board:** c_d41_cutsheet, decision d41 · **Goal / epic:** `mbari` / E7 · **Repo:** nereus-camera-test-rig
- **Why (Nick, verbatim):** "Cameras will be on moorings and backgrounds will change as mooring moves. Any decision about quality needs me to review a cut sheet and samples of videos to lock a spec." (board d41, Thu ~10 PM) · On the Reef Check moving-camera clip: "you should use that as the 5sec ref and then I can review how things look and you get a real field example" (~10:27 PM) · "I want to understand the smallest file size with the maximum quality for 720p. I'm building my own telemetry module in 2027 so this sets a foundation for desired file size to transfer, independent of the spotter." (Fri ~8:20 AM, PgM chat)
- **Acceptance is Nick's review** of the cut sheet and sample clips, Mon 10/19. The numbers below define what the study must deliver and how it is measured; they don't lock an encoder.
- **Must do (numbered, testable):**
  1. **Clips.** Primary: the Reef Check moving-camera footage (urchins + kelp), from Nick's authorised folders (Oct 2022, Oct 2025; copies on Nick_R_SSD `…/2026-10-15_reefcheck/video/` or the `~/Downloads` zips). Cut ≥ 3 different 5 s windows with camera motion, chosen by a rule written before any encode (e.g. fixed start times), not by eye. Mendocino Headlands is outside Nick's URLs: use it only if he says so. Secondary: a 5 s cut of Nick's 30 s diver clip (moving subject, static background) and the 6 pool clips of PREREG v2 (same 2.0–6.9 s cut).
  2. **Reference.** Scale every clip to 1280×720 at our camera's video frame rate (10 fps, `wide_720p`, per PREREG v2) and store it once as lossless FFV1. That file is both the encoder input and the VMAF reference. The Reef Check source is already compressed, so VMAF is relative to it; say so on the sheet.
  3. **Encoders:** p6 720p, p4 720p, p6 540p (upscaled bicubic to 720p for scoring), as in rig #116.
  4. **Size ladder per encoder and clip:** 25, 50, 75, 100, 150, 200, 300, 500 KB (each file within 95–100 % of the target where the rate knob allows). Metrics per point: VMAF mean and 5th-percentile frame, SSIMULACRA2 (10 frames), bytes.
  5. **The knee** (Nick's "smallest file size with the maximum quality"), defined now: per encoder, the smallest ladder size s such that **every** ladder size ≥ s has a worst-clip VMAF within 3.0 points of that encoder's worst-clip VMAF at 500 KB (monotone envelope; worst clip = the lowest mean VMAF at that size). Because each knee is relative to its own encoder's ceiling (p6 540p's will look early), the one-line recommendation compares encoders on **absolute** worst-clip VMAF at equal size. Report it per clip group (moving camera / diver / pool) and overall, plus where each curve crosses VMAF 70 (good) and 60 (watchable). Spotter-free: the ≤ 100 KB point is reported separately as the Spotter answer.
  6. **Pi cost on real hardware:** first commit a pre-registered test card for the nereus002 run (TEAM_RULES §9): cells, n = 3, bars, stop rules, cost; power rule: on charger or VBAT ≥ 3.4 V; delete only this run's own files on the Pi; the pool pull confirmed complete before the hand-over. Then, on nereus002's Pi (after the rig session hands it over, disarmed), for each encoder at the knee and at 100 KB on the worst moving-camera clip: encode wall time, peak RSS (≤ 250 MiB bar from PREREG v2), energy per clip from the UPS/INA readings if the rig logs them (else wall time × measured average power), SoC temperature + throttle flags; n = 3 runs each.
  7. **Cut sheet + samples** (before-after-report style artifact): per clip, source vs each encoder at the knee and at 100 KB; the worst frame (lowest per-frame VMAF) as stills; the size-vs-VMAF curves; Pi cost table. Playable mp4 samples at those points, linked from the sheet. One-line recommendation (encoder + size at 720p), separately for "Spotter ≤ 100 KB" and "own telemetry module".
- **Failure modes in the field (what the sheet must expose):**

  | # | What can go wrong on a mooring | How we detect it | What happens then |
  |---|---|---|---|
  | F1 | Mooring sway moves the whole background: bitrate spikes, quality collapses at a fixed size | Moving-camera clips' worst-clip VMAF and 5th-percentile frame vs the static clips | The knee moves up; Nick sees it on the sheet |
  | F2 | Smearing / blocking on moving texture (kelp, sand) that VMAF under-weights | Worst-frame stills side by side | Nick's eye decides, per his rule |
  | F3 | Motion makes the Pi encode slower and costlier | Real-Pi time + energy on the worst moving clip | Reported per encoder; a candidate past PREREG v2's ≤ 600 s or ≤ 250 MiB is marked infeasible |
  | F4 | Already-compressed source flatters VMAF | Stated on the sheet; pool clips (our own camera) as the control | Treat Reef Check VMAF as relative only |
  | F5 | Turbid or low-light water (particles encode like noise) | Include a turbid window if the footage has one; else say "not covered" | Gap stated |
  | F6 | Pi throttles on long encodes (p4 ~7 min/clip) | Temp + throttle flags per run | Flag, cool, re-run once; throttles again → the cell is marked "throttles" |

- **Pass / fail (for the study; set now):** done = every must do delivered with numbers; the window rule committed before the first encode; every ladder point on every clip scored (missing points listed with reasons); Pi cost measured n = 3 per cell, or "not measured: <why>"; the sheet published before Nick's Mon 10/19 review. The encoder choice is Nick's after the review.
- **Test plan / gates:** (1) QE spec PASS. (2) Desk encodes on the Mac only after the PjM's heavy-compute all-clear (pool pulls first). (3) nereus002 Pi only after the rig session's explicit hand-over (planned Sat noon–2:30 PM, pull done, rig disarmed); never touch the rig otherwise. Stop rules: before each extraction, free disk ≥ 8 GiB + the largest source video, else stop (clean only this run's own intermediates); read Reef Check sources straight from Nick_R_SSD when it's mounted; Pi throttle → as F6; whole-process-tree RSS > 250 MiB → that cell is infeasible (deliberately stricter than PREREG v2's 250–400 MiB "zram/swap, flagged" band). **Disk:** the Mac has ~12 GiB free: extract only the needed source videos (one at a time), keep only the FFV1 cuts, delete your own intermediates, never anything else. Outputs under `~/NereusData/nereus-camera-test-rig/<date>_video_cutsheet/` with a MANIFEST; the PR carries `.md/.csv/.json` only.
- **Out of scope:** locking an encoder (Nick); cached-background route (icebox, p_bgvideo); Spotter integration; camera-side code.
- **Done means:** TEAM_RULES §4 for a study: sheet + samples + RESULTS against this spec; QE PASS on the merged sha; Nick's review recorded on d41; PE + QE accept item by item.

## Amendment rev 2 (PE, Sat 10/17 2:40 PM): re-scoped as a SCREEN study (Nick 2:14 PM, verbatim on prop_e7_cutsheet; d41 folded in, Nick 11:51 AM)
Overrides must dos 3–7 where they conflict:
- **Off-the-shelf encoders and settings only**; nothing custom.
- **Summary table (the main deliverable):** rows = IMX708 (Pi) / N6 / AE3 × encoder, split into what each board encodes
  on-board vs what needs the Pi. Columns: resolution, fps, "feasible now" vs "needs custom work". Capability comes from
  docs / firmware notes and quick checks only, no builds. A board that can't do an encoder is a finding, not a failure.
- **Frame rates (Nick, PE chat ~2:35 PM):** each board at its rated rate: AE3 10 fps, N6 15 fps, IMX708 30 fps, at 720p.
  He accepted that this triples the work and may miss Monday.
- **Quality aim (Nick):** the knee (must do 5, monotone envelope).
- **Sizes (Nick):** 100 KB, 500 KB and 1 MB per 5 s clip first (= total transfer per clip; ≈ 160 kbit/s, 800 kbit/s,
  1.6 Mbit/s). The full 25 KB–1 MB curve only if time remains.
- **Pi cost (Nick):** estimate now from the calibrated 10/15 study (labelled "estimate"); real-Pi runs after pool test 2,
  so must do 6's nereus002 run moves out of this session.
- **Anti-spiral:** first look by **Sun 10/18 noon** (the table + 1 sample clip per board, at the knee or 500 KB) for Nick's
  phone comments; a **hard cap of ~6 h** of session time, then stop and show what exists.

## Amendment rev 3 (PE, Sat 10/17 2:49 PM; QE 004 R1–R4 + nits on rev 2). Overrides must dos 2, 5, 6 and the rev-1 pass/fail where they conflict.
- **References (overrides must do 2):** one FFV1 reference per frame rate used (10, 15, 30 fps), each scaled to 1280×720
  from the same 5 s source window. State each source's native frame rate. If a source is below 30 fps, its 30 fps row is
  "not covered", with no frame interpolation.
- **How each row gets its samples:**
  - **Desk emulation** with an off-the-shelf encoder of the board's own codec, run on the Mac: AE3 → MJPEG; N6 → H.264 if
    its firmware has it, else MJPEG; IMX708 → H.264 (Pi hardware-encoder settings as on Friday).
  - Every emulated cell is labelled "desk emulation"; the on-board capability column stays "from docs / firmware notes".
  - The AE3's 10 fps at 720p is hypothetical: it managed 9.33 fps at 640×400 with its software JPEG encoder on Friday, and
    720p is 3.6× the pixels. Mark that cell so.
- **Knee (overrides must do 5):** the ceiling is the largest size encoded (1 MB). With 3 sizes (100 KB / 500 KB / 1 MB)
  the knee is only bracketed between two sizes; the sheet says so. The full ladder narrows it only if time remains.
- **Pi cost (overrides must do 6):** a labelled estimate from the calibrated 10/15 study. No nereus002 run in this session.
- **Done for the screen (overrides the rev-1 pass/fail):**
  - by Sun 10/18 noon: the board × encoder table + 1 sample clip per board (desk emulation, at 500 KB);
  - at the ~6 h cap: whatever exists, with unfinished cells listed, and that counts as done;
  - the encoder choice stays Nick's after his review.
- **Tier:** now desk-only, but stays (a) unless Nick lowers it.
