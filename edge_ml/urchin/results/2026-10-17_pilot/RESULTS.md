# E6 night pilot: are the Reef Check videos good enough to work? (RESULTS)

- **Spec:** `~/NereusData/ops/pe_specs/SPEC_e6_night_pilot.md` rev 3, sha256 `59ae86e0cf03…` (QE 004 PASS). PREREG row dated 2026-10-17.
- **Run:** Sun 10/18 8:43–9:30 AM PT on Nicks-MacBook-Pro.local. Code `066f467` (extract, OWLv2, samples, score).
  The audit sheets were re-drawn with the per-row layout fix in this push; their content is unchanged.
- **Run folder (not in git):** `~/NereusData/nereus-camera-test-rig/2026-10-17_reefcheck_urchin_pilot/` (MANIFEST, stills, sheets, logs, `run.json`).
- **Result page for Nick:** https://claude.ai/artifact/CT3mFU2wdHpRL6WHGqMsTX (private).
- **Files here:** `quality.csv` (must-do 4), `verdict.json`, `pilot_sample_calls.csv` (must-do 5), `scale_sample_calls.csv`, and `marks.json` (Nick's marks).

## Verdict (usable-stills half of PREREG Q1 only)

**Good enough to work: usable 98 of 100 (98 %, Wilson 95 % 93–99 %).** The bar is ≥ 60 % with a lower bound ≥ 50 %.
- This is **for Nick-screened urchin footage only**: 5 of the 8 pre-screen videos. His screen also dropped murky or empty footage.
- **Not a Q1 pass.** The Q2 label minimums are not tested; there are no labels yet.

Purple urchin present in the sample: yes 59 % (49–68 %), unsure 12, no 29. This share is not representative of Reef Check footage overall, because Nick picked videos with urchins.

## Nick's marks (verbatim; QE N2)

- **Source:** his own board comment on `p_e6_reefcheck`, thread `7314c96c-2738-47bc-9151-244119ad329b`, attributed "the user (owner)", 2026-10-18T15:28Z (Sun 8:28 AM PT).
- **Read** by this session at 8:42 AM and recorded in the run MANIFEST before `select` ran (8:43 AM).

| # | Video | His note | Reading |
|---|---|---|---|
| 1 | Oct 2022 leadline | "not many urchings, there is a rock at 18:42:42" | yes (few) |
| 2 | Oct 2022 leadline2 | "more, at 18:44:13 and afterwars." | yes |
| 3 | Oct 2025 Caspar c1 | "not many, and murky water which makes this a great test. some at 11:34:13 and after." | yes |
| 4 | Oct 2025 Caspar c2 | "very murky and i did not see any purple urchin" | no |
| 5 | Oct 2025 Caspar c3 | "murky I did not see any urchin" | no |
| 6 | South Wreck f4 | "lots and lots, and the video goes from a green cast to a blue cast over the drive so a great reference for different color cast" | yes |
| 7 | South Wreck f3 | "urchin at the start, then less at the last 1/3 of video" | yes |
| 8 | South Wreck f18 | "I did not see urchin" | no |

**Yes, by folder:** Oct 2022 2/2, Oct 2025 1/3, South Wreck 2/3.

**Pilot set (rev-3 rule):** #1, #2, #3, #6, #7. n = 5 (fewer than 6, so all yes videos ran).

## Per video (`quality.csv`)

| # | Video | Format | Length | Stills/min | Blur · dup drop | Median sharpness | Model A ≥ 0.05 · ≥ 0.3 | Scale ref | Usable (sample) | Video call |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | leadline | 3840×2160 HEVC 29.97 | 68.3 s | 19.3 | 31 % · 6 % | 232 | 100 % · 23 % | 0/20 | 8/8 | too few to say |
| 2 | leadline2 | 3840×2160 HEVC 29.97 | 181.6 s | 22.5 | 24 % · 1 % | 392 | 100 % · 43 % | 0/20 | 24/24 | good enough |
| 3 | Caspar c1 | 3840×2160 HEVC 29.97 | 62.6 s | 25.9 | 16 % · 0 % | 299 | 100 % · 52 % | 20/20 (tape) | 9/9 | too few to say |
| 6 | South Wreck f4 | 1280×960 H.264 59.94 | 288.6 s | 20.6 | 30 % · 2 % | 52 | 95 % · 89 % | 0/20 | 34/34 | good enough |
| 7 | South Wreck f3 | 5312×2988 HEVC 59.94 | 185.5 s | 23.3 | 23 % · 0 % | 99 | 100 % · 75 % | 0/20 | 23/25 | good enough |

How each column is computed:
- **Drop shares** are of the E2 window-best stills (kept + blur + duplicate). QE interpretation (b).
- **Median sharpness** is over the kept stills (d). f4's low value comes from its 1280×960 source.
- **Model A** is OWLv2 on tiles: 288 stills, 12,922 candidates at ≥ 0.05, ~14 min of GPU time.
- **Scale ref** is the count among 20 evenly spaced stills per video.
- **Video call:** a video needs ≥ 10 sampled stills for its own call.

Extraction totals: 288 stills, 314 MB, 0 decode failures. The first-2-videos stop rule was not hit (19.3 and 22.5 stills/min).

## Recommendation (per folder: needs ≥ 2 videos and ≥ 10 sampled stills; interpretation c)

- **Oct 2022 (Fullers Resto): process in full.** Usable 32/32, from 2 videos.
- **South Wreck: process in full.** Usable 57/59, from 2 videos.
- **Oct 2025 (Caspar): no recommendation**, because only 1 video was in the pilot.
  - Nick saw no urchins in c2 or c3.
  - The c1 sample shows long-spined red-type urchins and no confirmed purple one (8 no, 1 unsure of 9).
  - It is the only video with a scale reference: a transect tape with legible cm marks near the camera.

## Other findings

- **Per m² (d33).** Only Caspar c1 has a scale reference (tape in 20/20 stills). The other four videos have none; Fullers Resto shows only an unmarked lead line. So per-m² is possible as filmed only at Caspar.
- **Model A.** It fires on nearly every still at the 0.05 pre-label floor. At 0.3 it also fires on red urchins (52 % of Caspar stills, where no purple urchin was confirmed). This is the zero-shot baseline a fine-tuned model has to beat.
- **Footage varies.** It ranges from 1280×960 (2021) to 5.3K (2024). The colour cast drifts within the f4 dive.
- **More marked clips.** Nick's question, PgM 005's answer in the thread. More would help if targeted:
  - red urchins and lookalikes next to purple ones;
  - mid-density clips;
  - videos with a tape or quadrat.
  More dense South Wreck footage is not needed.

## How the calls were made (QE N4 and process notes)

- **Usable is yes/no only** (no "unsure"). Urchin is yes/no/unsure. A long-spined red-type urchin alone is "no" when clearly red, "unsure" when the colour cast hides it.
- **Who judged.** Claude, at full resolution and blind to the model boxes, in two fresh review contexts (subagents) with a fixed rubric:
  - the 100-still pilot sample: usable, and purple urchin present;
  - the 20-per-video scale sample.
- **Disagreement and how it was resolved.** This session spot-checked sheet 0 and had called murky Caspar stills 4–5 not usable at sheet scale. At full resolution the near substrate is sharp (tape cm marks legible), so the reviewer's "usable" stands.
- **Borderline calls.** The reviewer flagged 8 lenient usable calls: 4, 6, 14, 49, 50, 58, 64, 94. With all 8 flipped, usable would be 90 %, still over the bar.
- **Nick's audit.** He checks 24 seeded stills of the sample (A01–A24, on the result page).
- **Thermal (P3).** `pmset -g therm` was checked 10 times (before each video and each model-A batch). No thermal or performance warning level was recorded. On arm64, pmset prints warning levels, not `CPU_Speed_Limit`.
- **Inputs.** Only read, from the SSD (#1 original, #2/#3/#6/#7 prescreen copies), checked by SHA-256 in `inventory.csv`. No zip was unpacked.
- **Disk.** Free space stayed ≥ 92 GiB throughout.

## Failure-mode rows (spec)

- **P1 disk:** OK; ≥ 92 GiB free throughout.
- **P2 compute:** PjM 003's all-clear Sat 2:59 PM. The E7 encodes were done at 3:26 PM. PjM 004 got a line at heavy start (8:53 AM) and heavy done (9:07 AM).
- **P3 heat:** no limiting recorded.
- **P4 decode:** 0 failures.
- **P5 turbid footage:** no; usable 98 %.
- **P6 scale:** yes; only Caspar has one.
- **P7 SSD:** mounted throughout; checked before each video and every 5 s during extraction.
