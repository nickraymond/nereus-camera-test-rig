# Spec: N6 + 5MP eval, bench → pool (PE 007 rev 1, Thu 10/22 8:01 PM PT, tier (a); rev 0 7:55 PM; rev 1 = QE 010's R1–R3 + N1–N6, before any data; rev 1.1 8:54 PM = QE 010's N7–N9, text only, tighter)

Tier (a): it runs on hardware (nereus001 + its spare N6) and ends in a pool day. QE spec PASS before the chip. On the build PR,
this file is committed first as nereus-camera-test-rig `docs/specs/n6_eval.md`.

- **Ticket / board card:** c_n6_eval (proposal prop_n6_eval) · **Goal / epic:** `edge` / E6 (sensor shoot-out → gen2 scorecard;
  rig issue #9 down-select metrics) · **Repo / branch:** nereus-camera-test-rig / `feat/n6-eval` → main.
- **Source plan (Nick's approved doc):** https://claude.ai/artifact/P1F1FFybQF3DVMHB9yQJZK ("N6 + 5MP eval plan", rev 34, read
  Thu 7:50 PM), its section "Handoff to PE 007", and Appendix A of https://claude.ai/artifact/2ooqVxmhMrZx9TBrdsu7kQ (tests 0–11).
- **Nick's words (Docs `via: frame` rows on that doc, Thu 10/22 PT, verbatim):**
  - 7:36 PM, row 050ce9ac: "I agree - please hand off to PE007 for the next steps".
  - 7:04 PM, row 0a4c36a3 (pool Tue 11/3): "yes I agree with this - this new effort can't interfere with the boards or NOAA software update."
  - 7:10 PM, row b0cf4883: "The preference is HD and 10fps or greater. Not less - since humnans also want to see these videos."
  - 7:27 PM, row 0a2974f3 (the clip bar): "All four tags detected and the three center grey/black color panels detectable (since
    those are needed for white balance color correction later)".
  - 7:22 PM, row ecd20ae4: the lens test ("Please add that to the test") and "we should be running tests outdoors not in the office".
  - 7:25 PM, row ac98c23e: "only I can do the tests, the should be limited to 30min or less".
  - 7:35 PM, row 290bcd08: the 5MP sensor's dynamic range "at the similar resolution to the 1MP sensor".
  - 7:28 PM, row 97650144: "we can alot 15% of the weekly tokens to this effort".
  - 7:32 PM, row 0a9bad77: "nothing to do with Bristlemouth transport layer for this work."
  - Hardware (Lead 003's chat, Thu ~7:50 PM, recorded on card ready_n6_eval_plan): nereus001 "is on wifi, it has the same power
    monitor I used prior (I2C adafruit board) and it has the other N6 plugged in" … "This is the hardware for this development".
- **Why:** in production the N6 or the Pi camera runs, never both, so the N6 must make its own uplink clip: 5 s in ≤ 100 kB. This
  finds the N6 setting (sensor, lens, mode) whose 100 kB clip is HD and still useful, then scores it underwater against the IMX708.

## Hardware and ownership
- The builder is the **sole owner of nereus001 and the spare N6 on it** (PAG7936, then the PS5520 module), handed over on its card.
  It never touches nereus002 or its N6/AE3, any bmcam, any Spotter, or the 10/27 NOAA update (Nick, row 0a4c36a3).
- No hardware step runs without a committed test card and Nick's OK line on it in the builder's own chat (§9, §6).

## Must do (numbered, testable)
**Phase 0, desk + spare-N6 checks (from the chip; done by Sun 10/25 6 PM):**
1. **Mode list:** on the spare N6 with stock v5.0.1 (version string recorded), list the PAG7936's real framesizes, windowing,
   binning and pixformats; the same for the PS5520 at Phase 2 start. Each entry = a framesize set and a frame captured at that size
   (not the docs).
2. **Run scripts** in the rig code tree: per-frame exposure (µs) + gain (dB) logged beside each frame; the power logger for
   nereus001's I2C power monitor (poll rate named on the test card: ≤ 10 Hz if SPEC_n6_probe showed nothing else on the bus; if it found another I2C user,
   e.g. a LiFePO4wered, a lower rate the test card states and justifies against that bus's budget, N9);
   a self-recording ~2 min walk per setting (start, record, stop, sha256) needing no laptop and no network (must do 7a).
2a. **Measuring tools pinned before data (QE R2):** the test card, committed before any desk-proxy score or Phase 1 data, names
   the tag detector (library + version, tag25h9, decimation and refine settings) and the clip encoder (program + version, every
   option, and the N6-encoder limits it imitates). A change to either after data exists is §13's "change after data" → Nick.
3. **Power check before any mW is reported:** the monitor reads a known load (resistor or a reference board, named on the card)
   within ±10 % of its expected current. Otherwise mW is "not measured" (F1).
4. **On-N6 AprilTags:** confirm `find_apriltags` runs on the N6 on v5.0.1 (tag25h9 board, ≥ 1 decode at 1 m in air); record ms per
   frame at each Phase 1 framesize. Missing → Pi-side detection only, stated (F2).
5. **Desk proxy (Mac; waits for the Lead's compute all-clear):** from existing pool-2 footage (pool055 clip011 and ≥ 2 other N6
   clips from ~/NereusData/nereus-camera-test-rig/2026-10-21_pool2/, named in the MANIFEST), render HD 1280×800 clips of 5 s at
   10, 15, 20 and 30 fps, each landing **90–100 kB (kB = 1,000 bytes: 90,000–100,000 bytes)** (two-pass or a size search; outside
   that is not scored, F7), with the encoder pinned per must do 2a. Score each clip with must do 8's bar (its 3 m bin = that clip's
   frames whose tag range, from the pool-2 rig's calibrated intrinsics, is 2.75–3.25 m) and post the clips for Nick to view.
6. **Faster firmware search:** look for Nick's faster-encoding N6 build in ADIN_SPI_OpenMV (all branches and tags), any openmv
   fork under his account, and .dfu/.bin files in ~/NereusData and ~/Downloads. Read-only. Found → its path + sha256; not found →
   Phase 1 test 3 is skipped and stated (F3). No ask to Nick for it.

**Phase 1, PAG7936 outdoors (Nick's session 1 Mon 10/26, ≤ 30 min; nothing asked of Nick on Tue 10/27):**
7. Appendix A tests 0–5, one change per walk, all in one session: 0 baseline (HD q70 4:2:0, 10 fps, stock v5.0.1); 1 AE + AWB
   locked after the 2 s warm-up; 2 JPEG q70 / 85 / 90; 3 stock vs the faster build (if found); 4 frames to SD on the N6 vs USB to
   the Pi (PoE out of scope); 5 full colour 4:4:4. Daylight, moving scene: Nick walks the tag board toward and away from the camera
   past tape marks at 1, 2, 3, 4, 5 m, pausing ~3 s at each. A baseline walk runs first and last (F5).
7a. **Running alone outdoors (QE R3):** the whole session's walk sequence is pre-loaded on nereus001 and runs from its own schedule
   with no network once started (the builder arms it and confirms it armed before Nick goes out). Each walk starts on a fixed
   countdown; **Nick sees whether it is recording from a status LED pattern** (named on the test card: e.g. steady = recording,
   fast blink = no frames, off = between walks), checked visible at 5 m outdoors in daylight during the builder's arm step (N8). The script checks that frames are being written 5 s into each walk; if not, it shows
   the no-frames pattern, logs it and moves to the next setting. The builder pulls and checks every walk after the session (F15, F16).
7b. **Binning by distance (QE R1):** bench walks are binned by the **tape-mark pauses**, not by computed range: a pause = ≥ 2 s
   in which the median side length of all decoded tags changes ≤ 5 %; the pauses are matched to the marks 1–5 m by decreasing tag
   size, not by count (rev 1.1, QE N7). The **3 m bin = the frames of the 3 m pause**; a walk with no identifiable 3 m pause
   **fails** must do 8 (a), unless it already failed must do 9's capture bar. Computed range (needs fx/fy) is used only where a calibration
   for that exact sensor + lens exists, named on the test card, and is then reported beside the pause bin, never instead of it.
8. **Scoring = the 100 kB tag test** (Nick's bar, rows b0cf4883 + 0a2974f3). Each walk's frames are encoded N6-style to a 5 s clip
   of 90,000–100,000 bytes at **HD 1280×800 and ≥ 10 fps (never lower)**. A clip **passes** when, in its 3 m bin (must do 7b):
   (a) all 4 tags decode in ≥ 90 % of frames; (b) the 3 centre grey/black panels, located from the tags, keep the card's order with
   adjacent panel means ≥ 8 luma codes apart in ≥ 90 % of frames; (c) Nick's viewing call is yes. The 90 % and 8-code numbers are
   the PE's reading of "detected / detectable"; Nick confirms or changes them on prop_n6_eval before any data.
9. **Capture bar for a setting to count (QE N2):** at HD, ≥ 50 unique frames in every 5 s window the clip uses (≥ 10.0 fps
   delivered, no duplicated frames to fill), 0 USB disconnects, no N6 reset. A setting that misses it is reported, not scored.
10. **Reported with each setting (no pass/fail):** grey-patch luma and R/G change per frame (report proposal: ≤ 0.26 / ≤ 0.002),
    8×8 blockiness (proposal ≤ 1.5), bytes per frame, mW (if must do 3 passed), exposure and gain per frame.
11. **Result page A** (a board Ready card linking the session, one yes/no for Nick): the best PAG7936 setting = passes must do 8 and
    9 with the highest 3 m all-4-tag rate; a tie (within 5 points) goes to the lower mW, or, if mW wasn't measured (F1), to the
    higher delivered fps, then the fewer bytes per frame. None passes → page A says so, and Phase 2
    still runs (the sensor or lens may be the fix).

**Phase 2, PS5520 + lens outdoors (Nick's 5-min module swap + session 2, Wed 10/28 → Thu 10/29, ≤ 30 min incl. lens swaps):**
12. PS5520: delivered fps at VGA / HD / 1080p, and 5MP stills (the N6 encoder tops out at 1920×1088); mW; edge 10–90 % in mm;
    on-device tag detection rate and ms per frame. Same walk method.
13. **Matched-HD head-to-head** (row 290bcd08): PS5520 at ~1280×800/720 (binned or scaled, whichever must do 1 found) vs the
    PAG7936 HD, same scene: clipped-highlight and clipped-shadow % (bright sky + a shaded subject), the grey panels, delivered fps,
    tag reads, must do 8's 100 kB tag test, Nick's viewing call.
14. **Lens arm** (row ecd20ae4): stock lens vs Nick's M12 on the PAG7936 and the PS5520: edge 10–90 % in mm at centre and corner,
    n ≥ 10 frames per position, plus tag reads.
15. **Result page B** (one yes/no): the sensor + lens + mode for the pool, by must do 11's rule.

**Phase 3, pool (Tue 11/3; decided, row 0a4c36a3):**
16. A separate pool test card (bars below copied, never looser) at QE PASS by **Fri 10/30 6 PM**, and the pool day in the Lead's
    approval envelope. The winning N6 setup + the IMX708 on the rig, the board walked 1–5 m as on 10/21, frames matched within
    20 ms, power logged the whole dive. The N6 records at high quality (q90, or 4:4:4 if Phase 1 held must do 9).
17. **Pool bars (set now):** on-N6 tag detection within 10 points of the Pi-side detector on the same frames, out to 3 m;
    0 false tag IDs; ms per frame and mW per mode (idle, stream, stream + detect, detect only) reported; grey-patch stability:
    luma and R/G change per frame ≤ the **stricter** of 2× the same-day IMX708 and the fixed 0.26 / 0.002 (Appendix A test 1;
    QE N1). Pool distance bins use the rig's calibrated underwater intrinsics, as on 10/21. **Result page C** (one yes/no).
18. **Token cap (row 97650144):** every result page and the builder's card show the plan's running share of the weekly budget.
    At 12 % the builder stops before starting anything new and asks Nick on its card; at 15 % it parks. If the session can't read
    the weekly meter, it reports its own use and says so.
19. Outputs go to `~/NereusData/nereus-camera-test-rig/<date>_n6_eval_<phase>/` with a MANIFEST (source sha, host nereus001,
    N6 firmware string, sensor, lens); code in the rig repo by PR → QE; nothing from a run in git (§11).

## Failure modes (field = the outdoor sessions and the pool)
| # | What goes wrong | How it's detected | Then |
|---|---|---|---|
| F1 | Power monitor reads 0 or wrong (the old shield channel read 0 mA) | Must do 3's known-load check | mW "not measured"; fix the wiring on the desk before session 1, else run without mW |
| F2 | `find_apriltags` missing or too slow on v5.0.1 | Must do 4 | Pi-side detection only; pool bar "on-N6 within 10 points" is N/A and stated |
| F3 | The faster build isn't found | Must do 6 | Test 3 skipped; stock v5.0.1 for everything |
| F4 | Nick's session overruns 30 min | Builder's script clock | Stop at 30 min, keep what's recorded; missing settings move to session 2, never a third session without Nick asking |
| F5 | Light changes during a session (cloud) | First vs last baseline walk: median exposure × gain (must do 2's log) differs > 25 % (QE N5: AE holds the panels' luma) | Flag the session; settings compared only against the nearer baseline |
| F6 | N6 hangs or USB drops mid-walk | Capture log gap > 1 s, no frames | Builder power-cycles the N6 on nereus001 (its own unit) and repeats that walk once; 2 in a session → stop and report |
| F7 | The proxy encoder undershoots the 100 kB target (10/22: aimed 100, landed ~57 kB) | Clip size | Two-pass or size search; a clip outside 90–100 kB isn't scored |
| F8 | The PS5520 can't bin to HD | Must do 1 | Scaled HD, stated on page B |
| F9 | PS5520 rolling-shutter skew on the moving board | Tag decode failures with skewed quads | Reported beside its tag rate |
| F10 | Two N6s + IMX708 don't fit the pool rig (Nick is checking) | Nick's fit check before Fri 10/30 | Pool card can't be written → a decision card for Nick; the builder never moves nereus002's N6 or AE3 |
| F11 | Mac compute clash with E5/E6 jobs | Lead's compute all-clear | Must do 5 waits; Phase 1 doesn't |
| F12 | Token share reaches 12 % / 15 % | Must do 18 | Stop-and-ask / park |
| F14 | No calibration for a sensor + lens (PS5520, the M12): range scale unknown | Test card has no intrinsics file for it | Binned by tape-mark pause only (must do 7b), stated |
| F15 | Wi-Fi drops outdoors, so the builder can't start or watch walks | No ssh from the Mac | The pre-loaded schedule runs anyway (must do 7a); the builder pulls after; if it never armed, Nick sees no LED pattern and the session moves |
| F16 | A walk silently records nothing | 5 s frame check → no-frames LED pattern + log | Script moves on; that setting moves to session 2; 2 in one session → stop the session |
| F13 | A session drifts toward nereus002, bmcam units, Bristlemouth transport or N6 H.264 firmware | Scope check on the PR (QE) | Out of scope; stop that item |

## Pass / fail
- **Pass** = must dos 1–19 done with evidence, result pages A, B and C posted, and must do 17's bars judged on the pool day (the
  worst bin decides). A setting "wins" only by must dos 8–9 and 11's rule. Pinned at the spec-PASS sha; a test card may only
  tighten. Loosening a number, dropping a must do or a row, or any change after data exists goes to Nick.
- **Fail / stop:** any kill line below.

## Kill lines (check times)
- Sun 10/25 6 PM: must dos 1–4 not done → Phase 1 runs only tests 0 and 1 on Mon.
- Session clock 30 min (F4). Token share 12 % / 15 % (F12).
- Fri 10/30 6 PM: no pool test card at QE PASS → no pool on 11/3; the Lead puts the date to Nick.

## Test plan / cost
- Builder ~2 h desk (Phase 0) + ~2 h per bench phase + scoring; QE (spec, PR, pool card). Hardware: nereus001 + the spare N6 only,
  Mon 10/26 → Thu 10/29 and the pool day. Mac: one heavy slot for must do 5 (Lead's all-clear). Nick: session 1 ≤ 30 min (Mon
  10/26), a 5-min module swap + session 2 ≤ 30 min (Wed/Thu), 3 result-page yes/nos, test-card OK lines in the builder's chat, pool
  day 11/3. None on Tue 10/27. Tokens ≤ 15 % of the weekly budget.

## Out of scope
N6 firmware H.264, N6 clips over Bristlemouth (row 0a9bad77), PoE transport, IMX708 full-res mode, nereus002 and its cameras, the
NOAA units, the email to OpenMV (Nick's own item, Fri 10/23).

## Done
TEAM_RULES §4, where item 6 is every must do and failure-mode row above checked off with evidence by the PE.
