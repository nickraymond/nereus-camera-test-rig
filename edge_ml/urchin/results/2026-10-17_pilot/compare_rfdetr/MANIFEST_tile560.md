# Rerun: Nick's RF-DETR with 560-px tiles on the 100 pilot stills (rule written before the run)

- **Why:** Nick in the urchin session's chat, Sun 10/18 ~9 PM: "rerun my model with 560 px tiles on the 100 stills".
  The model's input is 560 × 560; the 1080-px tiles were shrunk about 1.9× and the whole frame 6.9 × 3.9× (source: training_config.json resolution 560; rfdetr predict's default (resolution, resolution) resize).
- **Only change from the first run (MANIFEST.md):** tile side = min(short side, 560) px instead of 1080, so each tile is fed about 1:1. Same ≥ 20 % overlap, NMS 0.5, detections ≥ 0.05, same checkpoint (sha256 ae9aa853d9d7…), same 100 stills.
- **Scoring:** unchanged, against Claude's purple-urchin calls: AUC (yes vs no, and yes vs non-red no); stills flagged at 0.3 and 0.5 per group (yes / no / red-only no / other no / unsure); purple stills flagged at 0.5 per video. Reported next to the 1080-px, whole-frame and OWLv2 rows.
- **Also reported:** candidates per still at ≥ 0.05 and ≥ 0.1 (relevant to the pre-label volume rule), and run time.
- **Not a PREREG result; it changes no rule.** The full-run pre-labeler stays the 1080-px tiling unless the PE/QE amend #128.
