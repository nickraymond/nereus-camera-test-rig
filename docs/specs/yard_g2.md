# Spec: G2, bounded on-board tag search on nereus002's N6 + AE3 for the yard test (PE 010 rev 0, Mon 10/26 9:20 PM PT)
- **Card:** c_yard_test (gap pick nick_pool3_gap_bar; freeze nick_yard_g2_freeze) · **Goal / epic:** `edge` / E6 · **Repo:** nereus-camera-test-rig,
  new draft PR stacked on rig #143's head (branch from the #143 sha QE passes, today 7f56c0ef unreviewed), so #143 can pass and merge alone.
- **Builder:** pool3 edge tags builder (local_31acc789). **Unit owner:** nereus002 owner 3 (local_90ce9372; board c_yard_test v11).
- **Tier:** (c) once Nick accepts the §10 risk for G2 on nick_yard_g2_freeze (spec, code and QE in parallel; spec PASS before any clip is
  scored). Until his words exist, code and QE may run on the desk, but nothing deploys to nereus002. His 9:15 PM line in owner 3's chat
  names rig #143 only; whether it covers this PR is his call on that card, not ours.
- **Nick's words (read by PE 010 as his own `[user]` turns in Lead 006's transcript, local_d5f3f11a…, ~9:17–9:19 PM):** "I want G2" and
  "If you cant get G2 working by 1am tonight, fall back to G1 for tomorrow test. I can't turn the lights on".
- **Why:** with board tags on, no flag met the 2-frame-period gap bar (SPEC_pool3_edge_tags rev 1 N4 :73; Q5 N6 263 ms, AE3 213 ms; rev 2.7
  Ruling 2). Today's #143 code already searches small boxes around found tags (track) but sweeps full-width bands to acquire
  (`openmv/common/capture_service.py` @ 7f56c0ef :465–530), and those bands are what cost the long gaps.

## Facts at start (read 9:19 PM)
- #143 @ 7f56c0ef: `_find_tags` = track boxes (each kept tag's bbox grown 1.0× its size) else one of B horizontal bands (full width, + h/8
  pad). Search only on every Nth frame and only while the stream is on schedule (`_tag_due`). Known limit (QE 011 N4, code comment :424):
  while one card id is tracked, the card's other tags are not re-acquired.
- Measured ROI costs: AE3 640×400 ROI 24–25 ms vs full 140–155 ms (pool3 spec :109, n = 20); N6 HD GRAYSCALE 32 ms with per-tag ROIs vs
  182 ms full (pool3 spec :13, nereus001's spare N6). N6 cost per acquire tile on nereus002: **unknown** (must do 3 measures it).
- Bar (unchanged): board gap ≤ 2 frame periods with tags on: N6 at 15 fps ≤ 133 ms, AE3 at 10 fps ≤ 200 ms.
- The bench is dark tonight (Nick). Dark frames give few quads, so dark search times can **understate** lit cost (G-F1).

## Must do
1. **Acquire in tiles, not bands.** Replace the full-width band with a grid of tiles (rows × cols, each grown by a pad so a tag up to the
   pad size lies wholly in one tile), visited in turn. Tile size per board is a setting (`tags.tiles` = [rows, cols]), chosen from must do 3
   so one tile's search plus that frame's normal work stays inside 2 frame periods. Track stays as today.
2. **Re-acquire while tracking (fixes QE 011 N4):** every M-th search (setting, default 4) while tracking also searches the next acquire
   tile, still inside the same time budget; the frame header records `mode` = track / tileK / track+tileK.
3. **Time budget, measured not guessed:** per board, the builder or owner times one search per tile size on nereus002 on a **lit** stored
   frame (a JPEG from today's lit clips, e.g. Q5, loaded on the board with `image.Image(path)`, grayscale as the hook does) and on a live dark
   frame; the PR names the tile size picked and both timings. If loading a stored frame isn't possible on a board: say so; lit cost stays
   unknown until Tue's lit clip.
4. **Guard:** if one search's ms exceeds the budget, the hook skips search on the next `every` frames (already deferred by `_tag_due` when
   late; make it explicit for an over-budget search) and counts it (`over` in the header); never blocks capture.
5. **Off and rollback by flags:** no `tags.tiles` = today's #143 behaviour; tags off = no search. The arm line names the flags. No firmware
   change: board Python only (a reflash stays Nick's, F9 / Y-F1).
6. Host side logs `mode`, `ms`, `over` per search frame and the sweep time (all tiles visited) per clip; the report prints them per board.

## Failure modes
| # | What can go wrong | How we detect it | Then |
|---|---|---|---|
| G-F1 | Dark bench times understate lit cost | must do 3 lit stored-frame timing; Tue's lit Y2 clip | Lit gap > 2 periods → G1 for the yard |
| G-F2 | Board walks out of the track box (Nick moving) and the sweep is slow to re-find it | sweep time per clip; track→tile transitions in the header | Report; smaller/fewer tiles only if the gap bar still holds |
| G-F3 | Tiles cut a tag at a seam | pad ≥ largest expected tag; ids per tile in the header | Raise the pad (budget re-checked) |
| G-F4 | Extra heap (tile copies) beside the JPEG encoder on the AE3 | MemoryError / board reset in the stream log | Hook off on the AE3 (flag); G1 on the N6 alone if needed |
| G-F5 | New code raises N6 mid-clip reboots (F9) | reboot count per clip vs owner 2's 0/10 dark | 2nd reboot stops captures (F9); G1 |
| G-F6 | G2 not QE-passed + bench-passed by 1:00 AM Tue | clock | Yard runs #143 alone with G1's limits (Nick's words) |
| G-F7 | Un-reviewed head reaches the yard | QE on the exact deployed sha | Only a QE-passed sha runs in the yard; others are learning data |

## Pass / fail
- **Tonight (dark, by 1:00 AM Tue):** QE PASS on the G2 PR's exact head, deployed by owner 3, then 3 × 60 s clips at the yard arm line +
  the G2 flags, tags on. Per board, worst clip: max gap ≤ 2 frame periods (N6 ≤ 133 ms, AE3 ≤ 200 ms); the card's capture bars hold
  (N6 ≥ 14.25 fps, AE3 ≥ 9.5 fps, 0 lost, N6 usable ≥ 95 %, skew ≤ 25 ms); 0 MemoryError, 0 board resets, 0 N6 mid-clip reboots;
  ≥ 1 search/s per board. Detection can't be scored dark. All met = "G2 works (dark)" → yard uses G2. Any miss or 1:00 AM passes = G1.
- **Tue, lit (the Y2 clean clip, yard spec rev 1.5):** the same gap bar plus ≥ 1 detection/s per board with ≥ 1 card id in ≥ 50 % of detection
  frames (pool3 pass line). G2 gaps over the bar in lit → the yard runs with G1's limits (N6 ≤ 350 / AE3 ≤ 250 ms around a search frame),
  reading Nick's fallback as "G2 not working". The Lead tells Nick which one ran.
- Both cases n = 3 (tonight) or the Y2 clip(s) (Tue): no recommendation beyond the yard test from these.

## Test plan
desk (builder, now → ~11:30 PM: code + unit tests for tile cover, budget guard, re-acquire) → QE 011 on the head → owner 3 deploys after
Nick's §10 words for G2 → must do 3 timing + 3 dark clips (~20 min) → owner reports by 1:00 AM → Lead tells Nick G2 or G1 (board, not chat).

## Out of scope
Firmware, IMX708 (Pi-side tags), new bars, any unit but nereus002, merging #143 (Lead's desk).

## Done
TEAM_RULES §4; PE checks off must dos 1–6 and G-F1–G-F7 against the PR, the timing table and the clips' MANIFEST.

## Rev 1 (PE 010, Mon 10/26 9:22 PM PT; QE 011 FAIL R1 + N1–N3, qe_reviews/QE011_spec_yard_g2_d3474398f305.md). Text only, before any data.
- **R1, scoring under tiles (replaces the Tue line's "≥ 50 % of detection frames" and, for G2 clips, yard spec rev 1 N1's hit %):**
  - **Hit % counts only frames whose searched region held the card:** track frames, plus acquire/probe frames whose tile contains the card's
    last known position on that board (its last kept corners; before the first detection in a clip, no frame counts). Hit % = those frames
    with ≥ 1 card id ÷ those frames. The report shows that n next to each hit %.
  - **Per mark, also reported:** time to first detection (from window start, or "none in window") and the clip's sweep time (all tiles
    visited once, from must do 6).
  - Tue lit pass line becomes: ≥ 1 detection/s per board **and** ≥ 50 % hit % by the rule above. G1 clips keep yard rev 1 N1's rule
    (whole-frame search, every hook frame counts). G2 vs G1 ranges are compared only by time to first detection and the R1 hit %.
  - The yard spec's report section carries the same rule (yard spec rev 1.6).
- **N1, freeze (replaces the Tier line's "Until his words exist … not ours"):** Nick, Lead 006 chat (verbatim, his own `[user]` turn read by
  PE 010): "Yes, Accept the §10 risk for the new G2 code on nereus002." That covers this PR on nereus002. His 9:15 PM line in owner 3's chat
  still covers rig #143 only.
- **N2, how board code reaches the boards (adds to must do 5):** copying board Python with `host_tools/deploy_openmv.py` (mpremote over USB;
  board files flat on the board's filesystem, main.py last) is **not** a firmware flash, so Nick's G2 §10 line covers it and he isn't asked
  again for it. A firmware reflash stays Nick's own words in owner 3's chat. How the AE3's files get there on nereus002 (USB like the N6, or
  another path): **unknown**; owner 3 states it before deploy. **Rollback:** redeploy the board files from rig 224e5ce (or the #143 sha on
  the Pi) with the same tool; the owner verifies each board file's sha256 against the repo after deploy and after rollback, and logs them.
- **N3, sweep vs the 4 s pause:** tonight's pass adds **sweep time ≤ 3 s per board** (logged per clip). Nick pauses ~4 s per mark (yard spec
  :24); a sweep longer than the pause can't acquire a far mark inside its window. Sweep > 3 s → fewer/larger tiles if the gap bar still holds;
  else G1. The yard reads acquisition latency from the sweep time and time to first detection.
