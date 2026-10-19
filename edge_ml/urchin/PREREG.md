# PREREG — Purple-urchin detector on Reef Check video (E6)

**Status:** pre-registered 2026-10-15, before any Reef Check video was downloaded or viewed.
**Owner:** session "Urchin ML: Reef Check stills + note (E6)". **Branch:** `feat/e6-urchin-reefcheck`.
Changes after this date go in the amendment log at the bottom, each dated and made **before** the
numbers they affect exist.

## Questions

The status note to Reef Check (due Fri 10/23) answers two questions:

- **Q1. Did turning their video into stills work?** Does the extraction rule below give enough
  usable stills with purple urchins to train and test a detector?
- **Q2. Did the stills help a purple-urchin detector?** On held-out Reef Check video, does a
  detector trained on their stills beat the same task done without them?

The mid-November model (edge-sized, run on the nereus002 rig sensors) gets its own addendum. This
PREREG covers the note.

## Prior art reused

**Correction 2026-10-18:** Nick's own urchin detector exists outside this repo (RF-DETR, see the amendment log);
the search below missed it. As first written:

None. `nickraymond/UrchinCam` (@ `d8776c2`) holds only the KiCad camera-expander PCB, and
`nickraymond/urchin_topside` (@ `4dc6370`) is a 30-line Flask stub. Neither has urchin images,
labels, a label format or a model. Labels start from zero.

## Data

- Source: the Reef Check Google Drive folder Nick shares. The videos are unlabeled.
- Stored under `~/NereusData/nereus-camera-test-rig/<YYYY-MM-DD>_reefcheck_urchin/`
  (`video/` kept read-only, `stills/`, `labels/`, `runs/`), with a `MANIFEST.md` listing what each
  file is, its source, its SHA-256 and the host. Nothing large goes in git.
- E0. Inventory: `ffprobe` on every file gives duration, fps, resolution, codec, `creation_time`
  and the SHA-256. An unreadable file is listed and skipped.

## Frame extraction rule (Q1)

- E1. Candidates: one full-resolution frame every 0.5 s of video.
- E2. Sharpest per window: in each 2.0 s window, keep the candidate with the highest variance of
  the Laplacian (grey image scaled to 960 px on its long side). This gives one still per 2 s.
- E3. Blur drop: drop a still whose sharpness is below 0.5 × the median sharpness of that
  video's E2 window-best stills (not of all its candidates).
- E4. Duplicate drop: drop a still whose 64-bit dHash is within Hamming distance 6 of the previous
  kept still from the same video (camera not moving).
- E5. Output: JPEG quality 95, named `<video>_t<tenths of a second, 6 digits>.jpg`, plus
  `stills.csv` with one row per candidate (video, time, sharpness, kept or the drop reason).
  The videos are never changed.

**Q1 measure.** Yield per video: stills per minute, and the share dropped by E3 and E4. Then a
review sample: 100 stills (seed 0, drawn in proportion to each video's stills), each judged
*usable* (seafloor in view, sharp enough to tell an urchin) and *purple urchin present*
(yes / no / unsure). Proportions are reported with Wilson 95 % intervals and their n.

**Q1 bar.** "Worked" if the share of reviewed stills that are usable is ≥ 60 % (point estimate)
with a Wilson 95 % lower bound ≥ 50 %, **and** the labelled set meets the Q2 minimums (below). If the yield is fine but too few urchins are labelled, the answer is
"worked partly: too few urchins to test a detector".

## Labels

- Class: live purple sea urchin (*Strongylocentrotus purpuratus*). Red urchins, empty tests and
  everything else count as background.
- Box: tight around the visible test plus spines.
- **Ignore boxes** (never a miss; see Metric for how detections on them are handled): less than
  50 % visible, purple vs red unclear, urchin vs not unclear, or short side < 16 px at native
  resolution.
- Pre-labels (**amended 2026-10-18: Nick's RF-DETR, tiled, replaces model A here**, see the log; at the 0.05 floor
  it gives ~30 % more candidates to review than A after the 300 cap). Volume rule (one switch): candidate review ends by Wed 10/21 12:00 PM PT, so SAM, the full-frame miss search, training B, evaluation and writing fit before the Thu 10/22 draft. The review rate is timed on 96 crops: every k-th candidate scoring ≥ 0.1 in candidate_ids order, k = floor(number of candidates ≥ 0.1 / 96), so they spread across videos and count under either floor. Projected review time = total candidates ÷ that rate (candidate review only; the miss search is in the time after the deadline). If the 0.05 projection ends after the deadline, the floor rises to 0.1 for ALL stills; if the 0.1 projection also ends after it, the session stops and tells the PE (any descoping is Nick's call). Decided from counts and time only, never from label content; the rate, both projections and the floor that ran are reported.
  As first written, with RF-DETR now in model A's place: the zero-shot detector (model A below) at score ≥ 0.05, then NMS at IoU 0.5. The
  candidate crops are shown on contact sheets and each is marked urchin / not / ignore. Accepted
  boxes are tightened with SAM (`facebook/sam-vit-base`, box prompt), and the overlay is checked.
- **Test and validation stills** also get a full-frame search for misses (validation sets the
  thresholds, so its labels must be as complete as the test labels): each missed urchin gets a point, SAM
  makes the box from it, and the overlay is checked.
- **Training stills** get accept/reject only, with no search for misses. The resulting label
  noise lowers model B's recall, which works against "helped".
- Who labels: Claude, reviewing images, as described above. **Audit:** Nick checks the overlays
  of 10 random test stills (≤ 10 min) and we report the misses and wrong boxes he finds. If the
  audit is not done by Thu 10/22, the note says the labels have not been checked by a person.
- Known bias: test labels start from model A's candidates, which favours A on box overlap. This
  works against "helped".
  (Amended 2026-10-18: candidates now come from Nick's RF-DETR, so A loses that edge, and B, trained on
  labels from the same candidate source, may gain on box overlap: the bias now leans towards "helped".
  The miss search on test and validation stills still applies; the note reports this.)

## Held-out split (never random frames from the same clip)

- Group = **site + dive date** from the file name (amended 2026-10-15, see the log), written
  `<site>|<YYYY-MM-DD>` with these site names: `South Wreck`, `Fullers Resto`,
  `Caspar South Restoration`, `Mendocino Headlands` (two-digit years read as 20YY). Dates at
  the same site within 7 days of each other (one trip) are merged into one group, named by the
  earliest date. A file without a site or date in its name falls back to recording day
  (`creation_time`), then to the video file.
- **Frozen map:** `edge_ml/urchin/groups_reefcheck.json`, SHA-256
  `b10b076d9519bcca79a3d4d2166c0eccb00346117c6a56f0c90fa70140cbfede`, 29 videos → 7 groups:
  `South Wreck|2021-07-30` (9), `South Wreck|2022-06-22` (2), `South Wreck|2024-07-14` (6),
  `South Wreck|2025-09-11` (6; 09-11 + 09-16 merged), `Fullers Resto|2022-10-15` (2),
  `Caspar South Restoration|2025-10-16` (3), `Mendocino Headlands|2025-08-29` (1). Every file
  has a site and date in its name; none needs the fallback.
- Secondary report (not part of the bar): F1 for held-out groups whose site has no training
  data ("new site") and for those whose site does ("same site, other day"), each with its n.
- Eligibility screen (recorded in `labels/group_screen.csv`): for each group, up to 40 of its
  stills, evenly spaced in time (all of them if it has fewer), are checked for a purple urchin
  (yes / no / unsure; **unsure counts as present**). A group with none in its screen is not
  eligible for the test set; it may still be used in training as negatives. The screen is done
  without looking at the group hash order, committed (SHA-256 in the MANIFEST), and only then are
  the hashes and folds computed.
- **≥ 4 eligible groups:** test = ⌈⅓⌉ of the eligible groups, at least 2, taken in ascending
  SHA-256 order of the group name (chosen without looking at content). The rest are training.
  Validation = the lowest-hash **eligible** training group only if ≥ 3 eligible training groups
  remain (so B keeps ≥ 2 groups with urchins); otherwise the last 20 % (by time) of each
  training video, with the 30 s before it discarded. Scoring stops with an error if validation has no label boxes.
- **2–3 eligible groups:** leave-one-group-out. Each fold is reported, and the bar must hold in
  every fold. A fold's validation comes from its training groups only, by the rule above.
- **1 group:** split within each video by time: the first 70 % is training, the last 30 % is test,
  and 30 s between them is discarded (validation is always the time split, inside the first
  70 %). The outcome
  is "inconclusive (same dive)" whatever the numbers; if B beats A there, the note may describe
  it as promising, but it is not a "helped" result.
- Caps: ≤ 150 test stills, ≤ 100 validation stills and ≤ 300 training stills, seeded random
  samples (seed 0) drawn in proportion to each video's stills. The caps may change once, before labelling starts, via the amendment log.
- The test still list is written (`labels/test_stills.txt`, SHA-256 in the MANIFEST) before any
  model is trained or tuned.

## Models

- **A — without Reef Check stills:** OWLv2 zero-shot (`google/owlv2-base-patch16-ensemble`,
  Apache-2.0), single text query `"purple sea urchin"`. This is the "no stills" baseline. OWLv2
  sees a 960 × 960 input, so small urchins would vanish on a whole frame: A runs on square
  tiles of side min(short side, 1080 px), spaced to cover the frame with ≥ 20 % overlap, and the
  tile detections are merged by NMS at IoU 0.5. The same tiled A makes the pre-labels.
- **B — with Reef Check stills:** torchvision `fasterrcnn_mobilenet_v3_large_fpn`, COCO weights
  (BSD-3), with a new 2-class head, fine-tuned on the training stills. Fixed recipe: SGD, learning
  rate 0.005, momentum 0.9, weight decay 1e-4, batch 4, 24 epochs with ×0.1 steps at epochs 16
  and 22, horizontal flip, brightness and contrast jitter ±0.2 (no hue jitter), torchvision default
  sizes (800 / 1333). **Seeds 0, 1 and 2.**
- Thresholds: each model's (and each seed's) confidence threshold is the score that maximises F1
  on validation. It is then frozen before the test set is scored.
- If B cannot train on this Mac (a missing MPS op, or > 2 h per seed on CPU), the fallback is
  YOLOX-nano (Apache-2.0) with the same data, split and metric. The switch is recorded in the
  amendment log before any test number exists.
- Licences: no Ultralytics code or weights (AGPL-3.0). Everything used is BSD, MIT or Apache-2.0.
  This is internal Mac study code: it is never imported from `src/`, and its dependencies are in
  `edge_ml/urchin/requirements.txt`, not `pyproject.toml`.

## Metric

- Per test still: detections are sorted by score and matched greedily; a detection is a TP if its
  IoU with the best-overlapping *unmatched* label box is ≥ 0.5. A detection that is not a TP is
  dropped (neither TP nor FP) if its IoU with any ignore box is ≥ 0.5; any number of detections
  may be dropped on the same ignore box. All other detections are FPs, and unmatched label boxes
  are FNs.
- **Primary: precision and recall at IoU 0.5 at the frozen confidence threshold**, plus their F1.
  Secondary: AP50 (all-point interpolation), which does not depend on a threshold.
- Uncertainty: 95 % percentile bootstrap (2000 resamples, seed 0) over contiguous 30 s blocks of
  the held-out videos, because stills from one dive are correlated. Blocks are resampled
  **within each held-out group** (stratified, each group keeping its block count), and a
  difference such as F1(B) − F1(A) is computed on the **same** resampled stills for both models
  (paired).
- Every table states n: stills, label boxes, ignore boxes, videos and groups.

## Bar for "the stills helped" (Q2)

Minimums for any claim: ≥ 2 held-out groups (or folds) and ≥ 100 test label boxes.

"**Helped**" if **all** of:

1. B's F1 ≥ A's F1 + 0.10 (absolute), for every one of the three seeds;
2. the paired, group-stratified 95 % block-bootstrap interval of F1(B) − F1(A) excludes 0, for
   every seed;
3. B's F1 ≥ A's F1 in every held-out group (or every fold) — never decided on one group.

Outcomes: "**helped**" (minimums met and 1–3 hold); "**not shown**" (minimums met, but 1–3 do not
all hold); "**inconclusive — need more labelled video**" (minimums not met). The +0.10 F1 is our
pre-set smallest difference worth reporting.

**Claim scope.** A held-out group is a *new site* if its site has no training group (the
validation group does not count as training). If no held-out group is a new site, a "helped"
outcome is worded "helped on other dives at sites in training". The new-site secondary is
reported whatever its direction. Site-day groups are the first choice in this design that leans
towards "helped" (same-site dives look alike), which is why the wording is limited. We make no claim about whether the detector is good
enough for Reef Check surveys; that is Reef Check's call.

Secondary, not part of the bar: a learning curve for B (seed 0) at 25 %, 50 % and 100 % of the
training stills, to show whether more video would still help.

## Compute and hygiene

- Mac only. Every heavy job runs at `nice 10` with ≤ 2 workers, after a go from the PjM, never at
  the same time as the video ≤ 100 KB encode study, and never on Fri 10/16 8 AM–6 PM PT.
- No hardware: nereus002, bmcam units and Spotters are not touched.
- Run folders hold the exact command, git SHA, package versions, seeds and outputs.

## Amendment log

| Date | Change | Why | Made before which numbers? |
|---|---|---|---|
| 2026-10-15 | QE review of #117 (before any video was downloaded or viewed): 1-group outcome is "inconclusive"; paired, group-stratified bootstrap; 30 s gap before validation, LOGO validation from training groups; E3 text matches the code (median of window-bests); test eligibility from a per-group screen; Q1 bar uses a Wilson lower bound; ignore-box rule spelled out. Also A runs tiled. | QE FAIL items 1–5 + nits | All — no data yet |
| 2026-10-15 | Clarifications while writing the code (no video downloaded yet): ignore boxes are left out of model B's training targets (treated as background, which works against "helped"); bar condition 3 is checked for every seed, like 1 and 2; minimums are pooled over folds (held-out groups and test boxes). | Ambiguities in the original text | All — no data yet |
| 2026-10-15 | Group = site + dive date instead of site. Decided after seeing only the Drive file list (names, dates, sizes) and before any frame was viewed or any group hash computed. Adds the new-site / same-site secondary report. | South Wreck holds 23 of 29 videos (91 % of the bytes) over 5 dive days in 4 years. With site groups, 2 of 4 sites are held out by hash, so half the time South Wreck would be test data and B would train on ~2 GB from small sites: the outcome would hinge on a coin flip, not on the stills. Site-day groups are still separate dives (other day, mostly other year and diver). | All — no frames viewed, no hashes computed |
| 2026-10-15 | QE ruling on #118 (before any hash or frame): validation = lowest-hash *eligible* training group, and scoring fails if validation has no boxes; the 29-row video → group map is frozen with its SHA-256; same-site dates within 7 days merge (South Wreck 2025-09-11 + 09-16), giving 7 groups; claim scope wording when no held-out group is a new site. | QE FAIL items 1–4 on #118 | All — no frames viewed, no hashes computed |
| 2026-10-15 | QE PASS on #118 delta, plus: "unsure" in the group screen counts as present; screen committed before hashes and folds are computed; validation capped at 100 stills and labelled like test stills (miss search), since it sets the thresholds. | QE nit + gap in the original (validation labelling was unspecified) | All — no frames viewed, no hashes computed |
| 2026-10-15 | Validation takes a whole eligible group only if ≥ 3 eligible training groups remain; else the time split. Same-dive case always uses the time split. | QE simulation on the 7-group map: with 1–2 eligible groups the previous rule left B with zero urchin groups in every fold ("not shown" by construction) | All — no frames viewed, no screen done, no hashes computed |
| 2026-10-15 | Both models are scored from detections down to score 0.01 (prediction files must say `min_score` 0.01). Outcome "incomplete" (no claim) if model B is not exactly seeds 0, 1 and 2, each a full 24-epoch run of the fixed recipe. | QE nit on #118: these rules lived only in code | All — no frames viewed, no screen done, no hashes computed |
| 2026-10-15 | Extraction decodes with the Mac's hardware decoder (`--hwaccel videotoolbox`) to cut CPU heat; both passes of a video use the same decoder, so frame n is the same frame; the run's decoder, ffmpeg version and rules are written to `extract_run.json`. Hidden `._` files (exFAT) are skipped. | Heat rule; exFAT AppleDouble files would fail as videos | All — no frames viewed, no screen done, no hashes computed |
| 2026-10-15 | **The decoder is part of the method.** For 8-bit sources VideoToolbox and software decoding give different pixels (QE: 0/8 frames identical, up to ~200 levels at colour edges on 6 % of pixels; 10-bit HEVC identical), which can flip near-tie picks. Default and PREREG decoder = VideoToolbox, forced hardware frames (a fallback to software fails loudly); one decoder per output folder (a different one is refused); decoder recorded per video in `stills.csv`. | QE FAIL on #119 | All — no frames viewed, no screen done, no hashes computed |
| 2026-10-17 | **Night pilot** (PE spec `SPEC_e6_night_pilot.md` rev 3, sha256 `59ae86e0cf03…`, tier b): Claude views the stills of the n ≤ 6 videos Nick marked "urchins: yes" in his pre-screen (pilot set and his marks, verbatim, in the run MANIFEST `~/NereusData/nereus-camera-test-rig/2026-10-17_reefcheck_urchin_pilot/`), plus their model-A pre-labels. Its 100-still sample is the **pilot sample**: never reused as the full-run Q1 sample. Any later cap change cites this row. No PREREG rule or bar changes. | The pilot asks whether the footage is good enough before the full run; it views Reef Check stills that may later fall in test groups | Row written before any pilot frame was viewed; no PREREG number exists yet (no screen, no hashes, no labels) |
| 2026-10-18 | **Prior art correction.** Nick's own urchin detector exists: RF-DETR Base (DINOv2 encoder, Apache-2.0), one class "urchin" (any species), checkpoint `~/nereus_ml/runs/rfdetr_gate/checkpoint_best_total.pth` (Aug 28; mAP50 0.789 on its fenced urchinbot test set), code in Nick's `ADIN_SPI_OpenMV` repo (`ml/rfdetr_gate/`, `ml/urchin_data/`, `docs/urchin_corpus_plan.md`), corpus `~/nereus_ml/datasets/rfdetr_corpus_v2` (~20k images). It is not used in any PREREG step. An informal image-level comparison on the night pilot's 100-still sample is in `results/2026-10-17_pilot/compare_rfdetr/COMPARE.md`. Adding it as a model (baseline C) or as the pre-labeler needs its own amendment, made before any test label or number exists. | The 2026-10-15 "Prior art: none" searched only the rig repo, UrchinCam and urchin_topside; Nick pointed to his model on 10/18 | No PREREG number exists yet (no screen, no hashes, no labels); the pilot comparison is not a PREREG result |
| 2026-10-18 | **Pre-labeler = Nick's RF-DETR, tiled** (Nick's decision in the urchin session's chat, Sun 10/18 evening: "add my model tiled as the pre-labeler for the full run"). Pre-labels for the full run come from `edge_ml/urchin/rfdetr_prelabel.py`: checkpoint `~/nereus_ml/runs/rfdetr_gate/checkpoint_best_total.pth`, the same tiling as model A (squares of min(short side, 1080) px, ≥ 20 % overlap, NMS 0.5), score ≥ 0.05 (or 0.1 for ALL stills under the volume rule below), ≤ 300 per still, same output format, so the candidate sheets and SAM steps are unchanged. Its one class is "urchin" (any species): purple vs red is decided in review, as before (red = background, unclear = ignore box). Models A (OWLv2 zero-shot baseline) and B, the split, the metric and the bar are unchanged. The box-overlap bias in test labels no longer favours A and may favour B (trained on labels from the same candidate source), so it now leans towards "helped"; the note says so. | Night-pilot comparison (results/2026-10-17_pilot/compare_rfdetr), 100 stills. **At the 0.05 pre-label floor** both flag 59/59 purple-urchin stills; tiled RF-DETR gives 6,628 candidates uncapped and 6,296 as the tool outputs them (2 stills exceed the 300-per-still cap: 484 → 300, 448 → 300; the lowest scores are dropped) vs OWLv2's 4,835 (+30 % capped, +37 % uncapped), so review work grows. Volume rule (one switch): candidate review ends by Wed 10/21 12:00 PM PT, so SAM, the full-frame miss search, training B, evaluation and writing fit before the Thu 10/22 draft. The review rate is timed on 96 crops: every k-th candidate scoring ≥ 0.1 in candidate_ids order, k = floor(number of candidates ≥ 0.1 / 96), so they spread across videos and count under either floor. Projected review time = total candidates ÷ that rate (candidate review only; the miss search is in the time after the deadline). If the 0.05 projection ends after the deadline, the floor rises to 0.1 for ALL stills; if the 0.1 projection also ends after it, the session stops and tells the PE (any descoping is Nick's call). Decided from counts and time only, never from label content; the rate, both projections and the floor that ran are reported. Pilot at 0.1: 2,440 candidates, still 59/59 purple-urchin stills flagged. At ≥ 0.5 it flags 53/59 vs 44/59, so it ranks urchins higher. Checkpoint sha256 `ae9aa853d9d73354f318e03e710f517d032408791721f206f55c01f3ca0c915f`. Tier (a): the PE adopts this row at a sha and QE gates it before any full-run pre-label is made. | Made before any label exists (no screen, no hashes, no labels) |
| 2026-10-18 | **Clarification, made before any screen, hash, label or Q1 number** (PE 005 ruling, Sun 10/18 9:22 PM PT, in SPEC_e6_unpack_tierb rev 2, sha256 `5e445572ecb5…`): the full-run Q1 review sample (100 stills, seed 0, drawn in proportion to each video's stills) is drawn from the full-run stills minus the 100 night-pilot sample file names (`pilot_sample.txt` in the pilot run folder, sha256 `658ae335a8a4…`), with the proportions computed after that removal. | The 2026-10-17 row says the pilot sample is "never reused as the full-run Q1 sample", but the full run re-extracts the pilot's videos with the same deterministic rule, so those stills reappear under the same names; the row did not say how to keep them out. | Made before any screen, hash, label or Q1 number exists |
