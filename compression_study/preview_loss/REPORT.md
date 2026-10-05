# nrjxl stills under real chunk loss: preview + robustness desk study

2026-10-05, for Nick / the EM. Desk study; no bm_cam_legacy or backend code changed.
Visual sheet (what the user sees after a typical and a bad real wake, per method):
https://claude.ai/artifact/EksY52TQATixbromncSLaw

**Problem.** An nrjxl still (four Bayer planes as separate JPEG XL codestreams in one
container, sent as keyed chunks) shows nothing until every chunk arrives, and a heal takes
2–3 wakes (~3 h). Any chunk can be lost, not only the tail.

## Recommendation

1. **Erasure coding (D) first.** Add m Reed–Solomon parity chunks. Any k of the k + m chunks
   rebuild the whole file with no heal.
   - **+18 parity (10 % of 180):**
     - Real first sends: complete on 88 % (today 42 %).
     - 100 % complete for a single 8- or 16-chunk run anywhere, and for i.i.d. 5 % loss.
   - **+27 (15 %):** 94 % complete. **+36 (20 %):** 98 %.
   - **Cost:**
     - Quality: the image codes at a higher distance; SSIM 0.973 vs 0.975, ΔE00 0.30 vs 0.27.
     - Camera: about 0.1 s on the Pi Zero 2 W.
     - Backend: about 0.1 s to decode.
   - Heals get simpler: the backend asks for "N more chunks", and the camera sends new parity.
2. **Plane-aware backend decode now (free, no camera change).** Decode every plane whose
   chunks all arrived.
   - 96 % of real first sends then show something.
   - It is grey when R or B is missing (colour only when complete, 42 %).
   - It needs only chunk 0 (the container header), or the plane offsets in START.
3. **Add the redundant preview (B) if a colour image on every wake is required.**
   - A 320×180 colour JPEG XL rendered on the camera: 10 messages (2.9 kB), sent first and
     again last.
   - With FEC +18, the first send shows colour on 100 % of real first sends and on a 40-chunk
     tail loss; 90 % arrive complete.
   - It costs 20 more messages (SSIM 0.969 vs 0.973 for FEC +18 alone) and 1.4 s on the Pi.
4. **Sync collisions (bm_cam_legacy #126): fix them at the source; FEC alone does not cover them.**
   A send that starts during the Spotter's hub.sync loses 31–38 chunks in a row, and START with
   them. FEC +18 and +27 cannot absorb that; +36 covers 31, and +45 (25 %, SSIM 0.967) covers 38.
   - The post-sync settle proposed in #126 removes the pattern at no byte cost.
   - Until then, the preview's END copy still shows colour.
   - Because START can be lost, put k, m, the plane boundaries and the preview's chunk range in
     every chunk header, not only in START. Today a lost chunk 0 also hides the planes that did
     arrive.
5. **Not recommended:**
   - **A (one progressive codestream):** stock libjxl decodes nothing until chunk ~100 of 180,
     because the squeeze low-pass sits in the first half of the stream. Any hole cuts
     everything after it.
   - **C (polyphase descriptions):** +50–66 % bytes at the same distance. The complete image
     drops to SSIM 0.926, ΔE00 0.73.

Suggested combination, in order:
- plane-aware backend now;
- FEC with m = 18–27 (START carries k and m);
- the preview pair, only if grey-or-nothing on ~6–12 % of wakes is unacceptable.

## Real loss (HIL consoles)

Every chunk the Spotter accepts is hex-dumped on its console (`<I{key}.{i}/{n}>`). A chunk of
a clip whose START and END both appear in the log, and which never shows up between them, was
lost on the first send. Where both exist, these losses match the backend's heal ranges exactly
(`g4_outdoor12h_20261002/analysis/heals.csv`).

- **Sources:** 50 first sends from `g4_outdoor12h_20261002` (25), `r1fix_cmdres_20261003` (16)
  and `c1_comms_20261003` (9).
- **How often:**
  - 29 of 50 lost chunks.
  - Median 7.1 % of the clip when lossy, max 22 %.
- **Shape:**
  - 45 loss runs: 40 in the middle, 5 at the tail.
  - Run lengths: 8 chunks × 31, 5 × 6, 1 × 3, 3 × 1, and one each of 19 / 30 / 33 / 42.
  - The 8-runs are `MS_Q_CELLULAR_ONLY is full` on the Spotter.
  - The long tails are from the 190-message era of G4.

So the typical loss is one or two holes of 8 chunks **anywhere** in the burst.

## Table

Median over 4 frames. All methods fill the same 180 messages of 288 B.

| method | extra msgs | complete: SSIM / ΔE00 | real traces: visible / colour / complete | one 8-run anywhere: complete | one 16-run anywhere: complete | tail 40: colour shown | i.i.d. 5 %: complete | Pi Zero 2 W (camera) | backend | wire contract |
|---|---|---|---|---|---|---|---|---|---|---|
| base (today) | 0 | 0.975 / 0.27 | 42 / 42 / 42 % | 0 % | 0 % | 0 % | 0 % | — | today | — |
| plane-aware | 0 | 0.975 / 0.27 | 96 / 42 / 42 % | 0 % | 0 % | 0 % (grey 100 %) | 0 % | none | decode complete planes; grey from G | none |
| A progressive | 0 | 0.976 / 0.30 | 64 / 64 / 42 % | 0 % | 0 % | 100 % | 0 % | same time; cjxl peak 68 → 123 MB | djxl `--allow_partial_files` on the prefix | new payload layout |
| B preview ×2 | 20 | 0.973 / 0.30 | 100 / 100 / 42 % | 4 % | 0 % | 100 % | 0 % | +1.4 s | decode 2.9 kB preview | preview chunk kind + range in START |
| B preview ×3 | 30 | 0.970 / 0.33 | 100 / 100 / 42 % | 5 % | 0 % | 100 % | 0 % | +1.4 s | same | same |
| C polyphase ×4 | 0 | 0.926 / 0.73 | 100 / 100 / 42 % | 0 % | 0 % | 100 % | 0 % | same time, +50–66 % bytes | 4 decodes + interpolate | new container |
| D FEC +9 | 9 | 0.974 / 0.29 | 99 / 74 / 74 % | 100 % | 0 % | 0 % | 50 % | +0.1 s | RS decode | k, m in START; parity = index ≥ k |
| D FEC +18 | 18 | 0.973 / 0.30 | 100 / 88 / 88 % | 100 % | 100 % | 0 % | 100 % | +0.1 s | RS decode | same |
| D FEC +27 | 27 | 0.971 / 0.32 | 100 / 94 / 94 % | 100 % | 100 % | 0 % | 100 % | +0.1 s | RS decode | same |
| D FEC +36 | 36 | 0.969 / 0.34 | 100 / 98 / 98 % | 100 % | 100 % | 0 % | 100 % | +0.1 s | RS decode | same |
| D FEC +45 | 45 | 0.967 / 0.36 | 100 / 100 / 100 % | 100 % | 100 % | 100 % | 100 % | +0.1 s | RS decode | same |
| B+D preview ×2 + FEC +18 | 38 | 0.969 / 0.34 | 100 / 100 / 90 % | 100 % | 100 % | 100 % | 100 % | +1.5 s | both | both |
| B+D preview ×2 + FEC +27 | 47 | 0.967 / 0.35 | 100 / 100 / 98 % | 100 % | 100 % | 100 % | 100 % | +1.5 s | both | both |

Column notes:
- **visible:** the backend can show any image after the first send.
- **colour:** a colour image (complete, preview or progressive prefix), not a grey render of the
  planes that arrived.

Detail per frame: `results/results.json`. Raw table: `results/table.md`.

### Per-method detail

- **A, progressive.** The four planes are 2×2-tiled into one modular codestream, which is lossy
  squeeze by default (`-R 1` changes nothing; `-p` adds 0.3 %).
  - It is 1–2 % smaller than four separate planes at the same distance.
  - djxl first decodes the prefix at chunk 99–104 of 180, giving the whole image at nearly full
    quality, not an early low-res preview.
  - VarDCT on the same tile (`-p`, progressive DC; S4 cool frame only) first decodes at 29–34 %
    of the bytes (SSIM 0.76), still not an early preview.
  - A hole anywhere loses everything after it: stock libjxl decodes only a prefix.
- **B, preview.** Size vs usefulness (`results/prev_sizes.txt`; SSIM / ΔE00 vs RAW at 800×450):
  - 160×90 in 5 msgs (1.4 kB): 0.79–0.84 / 1.6–2.3.
  - 320×180 in 10 msgs (2.9 kB): 0.86–0.89 / 1.0–1.7. **The pick.**
  - 320×180 in 20 msgs (5.7 kB): 0.91–0.92 / 0.9–1.1.
  - Rendered on the camera in sRGB, with the frame's WB, it beats coding the camera-RGB sqrt
    codes (ΔE 2.0–2.8 at 10 msgs).
  - A grey preview saves nothing at the same size and loses colour (ΔE 4.3–4.8).
- **C, polyphase.** Each plane is split 2×2, giving four half-resolution Bayer mosaics, each
  its own chunk-aligned codestream.
  - Any one description shows the full field of view at SSIM 0.91–0.93.
  - The size penalty (+50–66 %) costs more than it saves.
- **D, FEC.** Systematic Cauchy Reed–Solomon over GF(256) at the chunk level (`fec.py`, about 60
  lines of numpy). n ≤ 256, which suits a 180-message budget.
  - Verified round-trip on the Mac and the Pi.
  - Pi Zero, k = 116: encode 0.07–0.08 s, decode 0.13–0.15 s.
  - A fountain code (RaptorQ) is only needed above 256 chunks.
  - FEC turns "which chunks were lost" into "how many".

## Sync-collision burst loss (bm_cam_legacy #126)

The pattern comes from the issue and the G4 console.
- **Window:** when a send starts in the Spotter's hub.sync, the 2-slot cellular queue takes 2
  chunks, then reports `MS_Q_CELLULAR_ONLY is full` for about 40–50 s. That is 31–38 chunks at
  the 1.3 s pacing, and START is inside the window.
- **Real case:** the G4 bmcam004 trigger clip `0e5qbm` (2026-10-03 06:07Z), in a wake with 114
  queue-full events, lost START and 24 of its 25 chunks; only chunk 12 got through.
- **Simulated as:**
  - a 31- or 38-chunk run starting at slot 2;
  - the same 31-run at every position (the sync landing mid-burst);
  - the real trigger pattern: slots 0–24 lost except 12.

Median over the 4 frames. Each cell: complete / colour shown (grey plane renders are not
counted as colour).

| method | sync at start, 31 lost | sync at start, 38 lost | 31-run anywhere | real trg (24 of 25 + START) |
|---|---|---|---|---|
| today | 0 / 0 % | 0 / 0 % | 0 / 0 % | 0 / 0 % |
| plane-aware | 0 / 0 % (grey 100 %) | 0 / 0 % (grey 100 %) | 0 / 0 % (grey 79 %) | 0 / 0 % (chunk 0 lost) |
| A progressive | 0 / 0 % | 0 / 0 % | 0 / 33 % | 0 / 0 % |
| B preview ×2 | 0 / 100 % | 0 / 100 % | 0 / 100 % | 0 / 100 % |
| D FEC +18 | 0 / 0 % | 0 / 0 % | 0 / 0 % | 0 / 0 % |
| D FEC +27 | 0 / 0 % | 0 / 0 % | 0 / 0 % | 100 / 100 % |
| D FEC +36 | 100 / 100 % | 0 / 0 % | 100 / 100 % | 100 / 100 % |
| D FEC +45 | 100 / 100 % | 100 / 100 % | 100 / 100 % | 100 / 100 % |
| B+D preview ×2 + FEC +18 | 0 / 100 % | 0 / 100 % | 0 / 100 % | 100 / 100 % |
| B+D preview ×2 + FEC +27 | 100 / 100 % | 0 / 100 % | 9 / 100 % | 100 / 100 % |

Why the preview helps with FEC: the first preview copy sits in slots 0–9, so a window that
starts at slot 2 spends 8 of its lost slots on a copy the end copy replaces. That is why
preview ×2 + FEC +27 completes the 31-at-start case and preview ×2 + FEC +18 completes the
real trigger.

The plane-aware row assumes the plane offsets come from chunk 0. With them in every chunk
header, the real trigger would show grey too.

## Method

- **Encoder:** bm_cam_legacy #120 `rc_raw_jxl.py` at e44dde5 (read-only export):
  - `read_dng_crop` → `code_planes` (12-bit sqrt codes);
  - `cjxl -m 1 -e 5 --num_threads=0`;
  - `seal_container`.
  - Each method's distance is bisected on the Mac so its bytes fill its chunk budget. This is
    tighter than the camera's 3-encode search; the same for every method.
- **Frames** (1600×900 native IMX708 crops):
  - S4 bench cool and warm (card + chart, 0.5 m);
  - the nereus002 2026-10-03 sweep frame;
  - pool rig run 7 at 1/60 s.
- **Loss patterns** (180 slots):
  - the 50 real first sends, with run starts scaled to 180 and run lengths kept;
  - one 8-run at every position;
  - one 16-run at every position;
  - a 40-chunk tail;
  - 200 draws of i.i.d. 5 %.
- **Chunk layout:**
  - [preview] data [parity] [preview copy];
  - the third preview copy sits mid-data;
  - parity at the end;
  - polyphase descriptions in sequence.
- **What the backend shows**, in order of preference:
  1. complete (or FEC-rebuilt) image;
  2. colour preview;
  3. grey render of the complete green planes;
  4. nothing.
- **Metrics:** each shown image vs the RAW crop rendered at 800×450 with one WB/exposure per
  frame: luma SSIM, and median CIEDE2000 of 8×8 blocks.
- **Pi Zero 2 W cost:** nereus002, one encode per method on the run-7 frame at d 5.66
  (33 kB), from `pi_bench.py`.

## Not tested / caveats

- **Losses beyond the Spotter queue.** On G4 the console losses equal the backend's missing
  chunks, so none were seen. START/END loss itself is not modelled: START carries the metadata,
  and FEC needs k and m from it.
- **The tail cases** (19–42 chunks) come from the 190-message G4 phase. With FEC, a 40-chunk
  tail still fails unless m ≥ 40: the preview pair covers that case.
- **Grey + preview chroma.** The backend could colour the full-resolution grey planes with the
  preview's chroma. Not built; a likely cheap win.
- **Water.** No underwater frames: the preview's colour at depth is not measured.
- **Rate control.** Production would need a fit search per method: FEC just lowers the chunk
  cap to k; the preview adds one small encode.

## Reproduce

```bash
python -m compression_study.preview_loss.traces --out traces.json
NRJXL_BM_DIR=<bm #120 export> NRJXL_DATA=<primary>/data NRJXL_POOL=<run-7 experiment> \
  python -m compression_study.preview_loss.study --traces traces.json --out results.json
python -m compression_study.preview_loss.sheet --results results.json --traces traces.json \
  --pi pi_bench.json --out sheet/index.html
```

`pi_bench.py` runs on the Pi with the same environment.
