# Nick's RF-DETR vs OWLv2 on the 100-still pilot sample (image level; run Sun 10/18 7:30 PM PT, 81 s on MPS)

**Reference:** Claude's image-level calls on PURPLE urchins: yes 59, no 29 (4 noted as red-type only), unsure 12.
- There are no box labels, so this compares images only.
- This is not a PREREG result.
- Rule written first: `MANIFEST.md` here.

| Model | AUC yes vs no | Purple stills flagged at 0.5 | "No" stills flagged at 0.5 | Unsure flagged at 0.5 |
|---|---|---|---|---|
| RF-DETR (Nick), whole frame 560 px | 0.78 | 44/59 (75 %) | 7/29 (3 of the 4 red-only) | 7/12 |
| RF-DETR (Nick), tiled ≤ 1080 px | 0.79 | 53/59 (90 %) | 12/29 (3 of the 4 red-only) | 6/12 |
| OWLv2 zero-shot "purple sea urchin", tiled | 0.94 | 44/59 (75 %) | 3/29 (2 of the 4 red-only) | 1/12 |

Purple stills flagged at 0.5, per video:

| Video | n | RF whole | RF tiled | OWLv2 |
|---|---|---|---|---|
| leadline | 2 | 0 | 1 | 1 |
| leadline2 | 11 | 2 | 9 | 6 |
| South Wreck f4 | 28 | 28 | 28 | 23 |
| South Wreck f3 | 18 | 14 | 15 | 14 |

How to read it:
- **RF-DETR does a different job.** It was trained on one class, "urchin" (any species). It fires on red urchins and on most "unsure" stills, which this purple-urchin reference counts against it.
  - Of its 4 whole-frame hits on non-red "no" stills, one (#87) is a long-spined urchin, so it is correct for its own task.
  - The other 3 (#2, #50, #94) are small boxes on hazy dark spots or kelp: false alarms.
- **Whole frame misses small urchins in 4K frames:** Fullers Resto leadline2 is 2/11 whole vs 9/11 tiled. On dense South Wreck f4 it catches 28 of 28.
- **Tiled, RF-DETR finds the most purple-urchin stills** (53/59). It also raises the most alarms on stills with no purple urchin (12/29).
- **OWLv2 separates best here** because its prompt says "purple".

Files in the run folder: `rfdetr_dets.json` (detections ≥ 0.05, whole + tiled), `rfdetr_dets_rerun.json`, `compare.json`, `run.log`.

## Provenance

- **Rule file first:** `MANIFEST.md` in the run folder was written at 7:29:41 PM PT. The run started at 7:29:47 PM, and `rfdetr_dets.json` was written at 7:31:11 PM (file times).
- **Code:** `edge_ml/urchin/compare_rfdetr.py` @ `20b726b` (`run` / `score`).
  - The first run used the data-folder copy of the same logic.
  - A rerun from the committed code at 7:35 PM gave identical detections (top score and count on all 100 stills, whole and tiled).
  - `score` reproduces `compare.json` byte for byte.
- **Request:** Nick's own line in the urchin session's chat, Sun 10/18 evening: "I want to know how my model does relative to your results."

## Rerun with 560-px tiles (Nick's ask, Sun 10/18 ~9 PM; rule in `MANIFEST_tile560.md`, written 9:21 PM before the run)

Tile side min(short side, 560) px, so each tile is fed about 1:1 to the model's 560 × 560 input. Everything else is unchanged.
Run 9:22–9:25:44 PM PT, 199 s (2.0 s/still), code `edge_ml/urchin/compare_rfdetr.py` @ `5ed86d6` (`--tile 560 --no-whole`).

| Model | AUC yes vs no | AUC yes vs non-red no | Purple flagged at 0.5 | No flagged at 0.5 (red-only) | Unsure at 0.5 |
|---|---|---|---|---|---|
| RF-DETR whole frame | 0.78 | 0.84 | 44/59 | 7/29 (3/4) | 7/12 |
| RF-DETR 1080-px tiles | 0.79 | 0.81 | 53/59 | 12/29 (3/4) | 6/12 |
| **RF-DETR 560-px tiles** | **0.87** | **0.88** | **54/59** | 13/29 (2/4) | 9/12 |
| OWLv2 tiled | 0.94 | 0.96 | 44/59 | 3/29 (2/4) | 1/12 |

Purple stills flagged at 0.5, per video (560 / 1080 / OWLv2): leadline 1 / 1 / 1 of 2; leadline2 9 / 9 / 6 of 11; South Wreck f4 28 / 28 / 23 of 28; South Wreck f3 16 / 15 / 14 of 18.

Candidates after the 300-per-still cap, 100 stills: 560-px tiles 7,935 at ≥ 0.05 and 2,937 at ≥ 0.1; 1080-px tiles 6,296 and 2,440; OWLv2 4,835 and 1,699. 2 stills hit the cap in each RF-DETR run.

Reading: feeding the model at its own scale ranks urchin stills clearly better (AUC 0.79 → 0.87) and finds 1 more purple still at 0.5, but at the fixed 0.5 threshold it also flags 1 more no-purple still, and it gives 26 % more candidates to review at 0.05 than 1080-px tiles. Still below OWLv2 on the purple question (0.94), partly because the "no" group holds red-type urchins that are correct for its class. Not a PREREG result; the full-run pre-labeler stays at 1080-px tiles unless the PE/QE amend #128.
