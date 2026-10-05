# nrjxl message budget on Nick's TG-7 RAW set

2026-10-05, Nick via the EM. This supersedes the reef-JPEG study as the RAW basis; the reef
JPEGs stay only as a labelled scene-complexity check. Sheet:
https://claude.ai/artifact/AsdpE3RZ8QeAbwpyuNN7hn. Data: `results/tg7_budget.json`. Code:
`tg7_budget.py` (run) and `tg7_sheet.py` (sheet).

## Selection (51 ORFs from `data/tg7_channel_islands`)

Frames are picked by capture time within each folder, which spreads dives and depths.
Flash and diver-torch frames are excluded.

| folder | frames | how picked |
|---|---|---|
| 0_surface_card | 2 | A-mode |
| 1_reference_A_iso100 | 16 | evenly spaced |
| 2_underwater_preset | 4 | evenly spaced |
| 3_scene_card_offcenter | 14 | all |
| 4_no_card | 15 | A-mode, evenly spaced |

- Kelp and reef scenes (3_ and 4_) are weighted most: 29 of 51.
- 36 of the 51 have a card position (S1 auto-locate or the versioned manual clicks).
- 22 of those have ≥ 4 card patches inside the 1600×900 crop, which the ΔE uses.

## Mapping: "TG-7 raw, resampled to IMX708 geometry, an approximation"

- **Crop:** for each IMX708 ROI (w×h of 4608×2592), the TG-7 crop covers the same FOV
  fraction: w/4608 of the TG-7 width, 16:9, centred. For the 1600×900 ROI that is 1394×784
  TG-7 px.
- **Resampling:** each CFA plane is Lanczos-resampled to the IMX708 pixel count (×1.148). The
  TG-7's GRBG CFA is kept, and levels are normalised to 10-bit (black 64, white 1023).
- **Noise:** the TG-7's own noise stays (12-bit, 1.55 µm, ISO 100). No IMX708 noise is added,
  and resampling correlates the noise slightly.

## Analysis

- **Encoder:** production `rc_raw_jxl` on a 16-point distance grid.
- **Budget → distance:** byte-target point at 97 % fill, ±1.5 % vs the exact search.
- **pjpg:** made the production way from the same data: render → 1000×562 Lanczos → progressive
  JPEG on the quality ladder under 195 messages.
- **Quality vs the RAW render** (there is no noise-free reference for real RAW):
  - luma SSIM at today's delivered size (1000×562) and at native size;
  - a fine-detail ratio;
  - ΔE00 on the V2 card patches inside the crop.

## Results, 1600×900, all 51 frames (P50 / P90)

**Distance needed at each budget:**

| budget | d (P50 / P90) | frames over the d 10.4 floor |
|---|---|---|
| 180 msgs | 3.4 / 6.3 | 0 |
| 250 msgs | 2.3 / 3.9 | 0 |
| 500 msgs | 1.0 / 1.5 | 0 |

**Messages needed:**
- For d 4.5: **148 / 228**. The kelp/reef scenes alone need 118 / 205; the card sweeps
  188 / 231.
- To match today's pjpg SSIM at today's size: **341 / 406**. This is a strict bar: on these easy
  scenes today's pjpg runs at q 70–90, which is near-lossless at 1000×562.
- To match its fine detail at native size: 156 / 535. The detail metric is noisy here, and
  11 frames never match it.

**Colour (card patches):** median ΔE00 vs the RAW render, P50 (P90):
- nrjxl at 180 messages: **0.08** (0.17);
- today's pjpg: **0.26** (0.52).

**Other crops:**
- 2304×1296 at 180 messages: d 9.0 / 14.5, with 23 of 51 frames over the floor.
- 800×450 fits at d ≤ 1.1 at 180 messages.

## The range, with the real-IMX708 and the reef points

| scene set | 1600×900, messages for d 4.5 (P50 / P90) |
|---|---|
| TG-7 underwater (this set) | 148 / 228 |
| bmcam004 daylight foliage (real IMX708, unit's own two points) | 364 |
| reference reef JPEGs (JPEG-derived, sanity check only) | 541 / 681 |

The TG-7 underwater set (blue water, soft kelp) is the easy end; daylight foliage and sunlit
reef texture are the hard end. 180 messages already gives d ≤ 6.3 on 90 % of these underwater
frames. Holding d 4–5 on daylight or reef texture needs about 350–550 messages.

## Caveats

- **Approximation:** the RAW is a different sensor, cropped and upsampled ×1.148, which smooths
  it slightly. A real IMX708 frame of the same scene would carry its own noise, which costs
  bytes.
- **No clean reference:** native-size SSIM against the noisy render partly rewards reproducing
  sensor noise.
- **ISP processing:** pjpg is made from the plain render; the production ISP's denoise and
  sharpening are not modelled.
