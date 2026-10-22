# V3 card truth: capture procedure (c1, beside the SpyderCheckr)

Purpose: measure every patch of the printed V3 card `c1` through the IMX708, calibrated on the
Datacolor SpyderCheckr 24 under the same light, and write the result into
`configs/cards/nereus_v3_c1.yaml` as its `measured:` block. Nick's ruling, 2026-10-05.

Dry tonight. Wet later, with the same steps and condition `wet`.

## You need

- One V3 `c1` card and the SpyderCheckr 24 (the 24-patch side).
- Two high-CRI lights:
  - the 5300 K panels are fine if they light both cards evenly;
  - no daylight and no room lights (blinds closed, lights off).
- nereus002 with the IMX708. Stop Nick's workbench recipe first, because it shares the cameras:

  ```bash
  curl -X POST http://nereus002:8088/api/stop
  ```

- A flat matte white or grey board, at least as big as both cards together, for the flat-field
  shots.
- **The SpyderCheckr reference values exported from the Spyder software.** Format: one CSV row
  per patch, with
  - the patch name as printed on the chart;
  - L\*, a\*, b\*;
  - the illuminant (D50 expected) and observer (2°) stated.

  Also take a phone photo of the chart showing which corner is which.

## Set up

1. **One Nereus card in the frame, never two `c1` copies.** Every `c1` carries the same tag IDs
   0–3, so two copies make the card position ambiguous. Leave the V1 and V2 cards out of frame
   too.
2. **Same plane.** Tape both cards flat on one board, side by side, ~2 cm apart. The SpyderCheckr
   goes to the right of the V3 card, upright, with the same edge at the top as in your photo.
3. **Geometry.**
   - Camera roughly perpendicular to the board, 0.6–0.8 m away (0.5–1 m is fine).
   - Both cards inside the middle ~60 % of the frame. The IMX708 RAW is darker toward the
     corners; the flat-field shots correct what is left.
4. **Light.**
   - One panel on each side, at about 45° to the board, at least 1 m away, at the height of the
     cards.
   - **Check glare from the camera's position:** look at the card surfaces from just beside the
     lens. No shiny patch on either card. If you see one, move that light further out to the
     side.
   - **Evenness:** a quick test JPEG (`rpicam-still -o /tmp/t.jpg`) should show no visible
     gradient across the two cards.

## Capture (on nereus002, from the repo root)

1. **Cards: 5 frames at one locked exposure, lowest gain.** Metered on the V3 card, so its
   brightest patch lands at 80 % of full scale:

   ```bash
   .venv/bin/python scripts/capture_raw_imx708.py --card configs/cards/nereus_v3_c1.yaml --target 0.8 --stops 0 --repeat 5 --gain 1.0
   ```

   It exits 0 when every shot passed its read-back checks, and prints its folder
   (`results/raw_imx708/<UTC>/`).
2. **Flat-field: 3 frames.**
   - Without moving the camera or the lights, cover both cards with the matte board in the same
     plane.
   - Run:

     ```bash
     .venv/bin/python scripts/capture_raw_imx708.py --target 0.8 --stops 0 --repeat 3 --gain 1.0
     ```

   - Optional but recommended: the tool works without it and says so in the provenance.
3. Note on paper: the light (panel model, CCT setting, brightness), the distance, the room
   temperature, and anything odd.

## Wet (later)

- Same set-up and the same commands. Only the V3 card is wet: dip it, let it drain about 10 s,
  and capture within a minute.
- The SpyderCheckr stays dry; it is the calibration.
- The tool writes `measured_wet:` instead of `measured:`.
- **Open question for Nick:** in-water (through a tank wall) is a different measurement from a
  wet surface. Say which one you want.

## Hand-off to the Mac

Copy the two folders. On the Mac, in the repo root:

```bash
rsync -a pi@nereus002:~/nereus-camera-test-rig/results/raw_imx708/<cards UTC>/ data/v3_truth/c1_dry_<date>/cards/
```

```bash
rsync -a pi@nereus002:~/nereus-camera-test-rig/results/raw_imx708/<flat UTC>/ data/v3_truth/c1_dry_<date>/flat/
```

Save the Spyder CSV as `data/v3_truth/spydercheckr_24_reference.csv`. Then fill in a session
file, copied from `configs/calibration/sessions/v3c1_truth_TEMPLATE.yaml`, and run:

```bash
python -m host_tools.card_truth configs/calibration/sessions/v3c1_truth_dry_<date>.yaml
```

It prints:
- how many SpyderCheckr and V3 patches it found;
- the fit's held-out ΔE00 on the SpyderCheckr patches, for 3×3 and root-polynomial;
- the light evenness across the two cards.

It writes the `measured:` block into `configs/cards/nereus_v3_c1.yaml`, with provenance (chart,
reference file and hash, light, date, frames, fit and held-out ΔE). It also writes an overlay PNG
per frame so the patch boxes can be checked by eye.

## Failure signs

| sign | likely cause | what to do |
|---|---|---|
| `card not located` | tags too small, glare, or blur | Move closer, check focus, remove glare. |
| `chart: N clean patch candidates` | the SpyderCheckr wasn't found automatically | Give its 4 corner-patch centres in the session (`chart.quad_raw`; the overlay PNG shows the pixel grid). |
| A patch clip fraction > 0 | `--target` too high | Re-run with `--target 0.7`. |
| Evenness worse than ±5 % between the cards | uneven light | Move the lights back or out. |
| Held-out ΔE00 median > 2 | chart misread, wrong reference file or orientation | Check the overlay PNG and the patch names in the CSV. |
