# Nick's RF-DETR urchin model vs the pilot's calls and OWLv2 (Sun 10/18 7:29 PM PT, written before any RF-DETR run)

- **Why:** Nick in this session's chat, Sun 10/18 evening: "I want to know how my model does relative to your results."
- **Model:** RF-DETR Base (DINOv2 windowed-small encoder, Apache-2.0), one class "urchin" (any species).
  - Checkpoint: `~/nereus_ml/runs/rfdetr_gate/checkpoint_best_total.pth` (Aug 28; 30 epochs; rung-A mAP50 0.789 on the urchinbot test set).
  - Code: `ADIN_SPI_OpenMV/ml/rfdetr_gate/` (read only). Environment: `~/nereus_ml/venvs/rfdetr`.
- **Data:** the 100-still pilot sample (`pilot_sample.txt`).
  - **Reference:** Claude's image-level calls on PURPLE urchins (`pilot_sample_calls.csv`): yes 59 / no 29 / unsure 12.
  - There are no box labels yet, so this compares images only, never boxes.
- **Runs** (detections down to score 0.05):
  - (a) whole frame, as the model was built and evaluated: `model.predict(image)`, which resizes to 560 px;
  - (b) tiled like OWLv2: squares of min(short side, 1080) px with ≥ 20 % overlap, merged by NMS at IoU 0.5.
- **Scores per image:** the maximum detection score.
- **Primary metric:** ROC-AUC of that score, yes vs no (unsure left out), for RF-DETR (a), RF-DETR (b) and OWLv2.
- **Fixed thresholds, chosen now:** 0.3 and 0.5 for every model. Reported at each:
  - share of "yes" stills flagged;
  - share of "no" stills flagged, also split into "no, red-type urchins only" (call note mentions red) and other "no";
  - share of "unsure" stills flagged.
- **Caveat stated up front:** the model is trained for "urchin", not "purple urchin". Firing on red urchins is correct for its own task and counts against it only on the purple question, so it is reported apart.
- **Not a PREREG result.** The pilot sample is not the PREREG test set, and this does not change the PREREG.
