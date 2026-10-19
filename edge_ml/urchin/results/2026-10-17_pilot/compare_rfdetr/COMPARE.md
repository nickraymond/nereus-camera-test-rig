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

Files: `rfdetr_dets.json` (detections ≥ 0.05, whole + tiled), `compare.json`, `run_rfdetr.py`, `score_compare.py`, `run.log`.
