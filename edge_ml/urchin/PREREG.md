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
- E3. Blur drop: drop a still whose sharpness is below 0.5 × the median sharpness of its video.
- E4. Duplicate drop: drop a still whose 64-bit dHash is within Hamming distance 6 of the previous
  kept still from the same video (camera not moving).
- E5. Output: JPEG quality 95, named `<video>_t<tenths of a second, 6 digits>.jpg`, plus
  `stills.csv` with one row per candidate (video, time, sharpness, kept or the drop reason).
  The videos are never changed.

**Q1 measure.** Yield per video: stills per minute, and the share dropped by E3 and E4. Then a
review sample: 100 stills (seed 0, drawn in proportion to each video's stills), each judged
*usable* (seafloor in view, sharp enough to tell an urchin) and *purple urchin present*
(yes / no / unsure). Proportions are reported with Wilson 95 % intervals and their n.

**Q1 bar.** "Worked" if ≥ 60 % of the reviewed stills are usable **and** the labelled set meets
the Q2 minimums (below). If the yield is fine but too few urchins are labelled, the answer is
"worked partly: too few urchins to test a detector".

## Labels

- Class: live purple sea urchin (*Strongylocentrotus purpuratus*). Red urchins, empty tests and
  everything else count as background.
- Box: tight around the visible test plus spines.
- **Ignore boxes** (a detection on one is neither a TP nor an FP, and it is not a miss): less than
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
- A group whose reviewed stills (Q1 sample) show no purple urchin is not eligible for the test set.
  It may still be used in training as negatives.
- **≥ 4 eligible groups:** test = ⌈⅓⌉ of the eligible groups, at least 2, taken in ascending
  SHA-256 order of the group name (chosen without looking at content). The rest are training.
  Validation = one training group (lowest hash) if ≥ 3 training groups remain; otherwise the last
  20 % (by time) of each training video.
- **2–3 eligible groups:** leave-one-group-out. Each fold is reported, and the bar must hold in
  every fold.
- **1 group:** split within each video by time: the first 70 % is training, the last 30 % is test,
  and 30 s between them is discarded. The result is labelled "same dive, weak", and "helped" may
  only be called "promising".
- Caps: ≤ 150 test stills and ≤ 300 training stills, seeded random samples drawn in proportion to
  each video's stills. The caps may change once, before labelling starts, via the amendment log.
- The test still list is written (`labels/test_stills.txt`, SHA-256 in the MANIFEST) before any
  model is trained or tuned.

## Models

- **A — without Reef Check stills:** OWLv2 zero-shot (`google/owlv2-base-patch16-ensemble`,
  Apache-2.0), single text query `"purple sea urchin"`. This is the "no stills" baseline.
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
  IoU with an unmatched label box is ≥ 0.5. Detections that match an ignore box are dropped. All
  other detections are FPs, and unmatched label boxes are FNs.
- **Primary: precision and recall at IoU 0.5 at the frozen confidence threshold**, plus their F1.
  Secondary: AP50 (all-point interpolation), which does not depend on a threshold.
- Uncertainty: 95 % bootstrap (2000 resamples) over contiguous 30 s blocks of the held-out videos,
  because stills from one dive are correlated.
- Every table states n: stills, label boxes, ignore boxes, videos and groups.

## Bar for "the stills helped" (Q2)

Minimums for any claim: ≥ 2 held-out groups (or folds) and ≥ 100 test label boxes.

"**Helped**" if **all** of:

1. B's F1 ≥ A's F1 + 0.10 (absolute), for every one of the three seeds;
2. the 95 % block-bootstrap interval of F1(B) − F1(A) excludes 0, for every seed;
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
| — | — | — | — |
