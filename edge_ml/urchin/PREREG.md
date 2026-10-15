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
- Pre-labels: the zero-shot detector (model A below) at score ≥ 0.05, then NMS at IoU 0.5. The
  candidate crops are shown on contact sheets and each is marked urchin / not / ignore. Accepted
  boxes are tightened with SAM (`facebook/sam-vit-base`, box prompt), and the overlay is checked.
- **Test stills** also get a full-frame search for misses: each missed urchin gets a point, SAM
  makes the box from it, and the overlay is checked.
- **Training stills** get accept/reject only, with no search for misses. The resulting label
  noise lowers model B's recall, which works against "helped".
- Who labels: Claude, reviewing images, as described above. **Audit:** Nick checks the overlays
  of 10 random test stills (≤ 10 min) and we report the misses and wrong boxes he finds. If the
  audit is not done by Thu 10/22, the note says the labels have not been checked by a person.
- Known bias: test labels start from model A's candidates, which favours A on box overlap. This
  works against "helped".

## Held-out split (never random frames from the same clip)

- Group = site, if sites can be identified (folder or file names, or Reef Check's notes);
  otherwise recording day (`creation_time`); otherwise the video file.
- Eligibility screen (recorded in `labels/group_screen.csv`): for each group, up to 40 of its
  stills, evenly spaced in time (all of them if it has fewer), are checked for a purple urchin.
  A group with none in its screen is not eligible for the test set; it may still be used in
  training as negatives. The screen is fixed before the split is drawn.
- **≥ 4 eligible groups:** test = ⌈⅓⌉ of the eligible groups, at least 2, taken in ascending
  SHA-256 order of the group name (chosen without looking at content). The rest are training.
  Validation = one training group (lowest hash) if ≥ 3 training groups remain; otherwise the last
  20 % (by time) of each training video, with the 30 s before it discarded.
- **2–3 eligible groups:** leave-one-group-out. Each fold is reported, and the bar must hold in
  every fold. A fold's validation comes from its training groups only, by the rule above.
- **1 group:** split within each video by time: the first 70 % is training, the last 30 % is test,
  and 30 s between them is discarded (validation as above, inside the first 70 %). The outcome
  is "inconclusive (same dive)" whatever the numbers; if B beats A there, the note may describe
  it as promising, but it is not a "helped" result.
- Caps: ≤ 150 test stills and ≤ 300 training stills, seeded random samples drawn in proportion to
  each video's stills. The caps may change once, before labelling starts, via the amendment log.
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
pre-set smallest difference worth reporting. We make no claim about whether the detector is good
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
