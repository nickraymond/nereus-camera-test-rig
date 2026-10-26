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
