| method | extra msgs (of 180) | complete: SSIM / ΔE00 | traces: visible / colour / complete | 8-run anywhere: complete | 16-run anywhere: complete | tail 40: colour shown | i.i.d. 5 %: complete | Pi Zero cost | backend | wire contract |
|---|---|---|---|---|---|---|---|---|---|---|
| base | 0 | 0.975 / 0.27 | 42 % / 42 % / 42 % | 0 % | 0 % | 0 % | 0 % | 0 (today) | today | — |
| plane | 0 | 0.975 / 0.27 | 96 % / 42 % / 42 % | 0 % | 0 % | 0 % | 0 % | 0 | decode each complete plane; grey from G | none (container offsets already in chunk 0) |
| prog | 0 | 0.976 / 0.30 | 64 % / 64 % / 42 % | 0 % | 0 % | 100 % | 0 % | same encode; cjxl peak RSS 68 → 123 MB | stock djxl --allow_partial_files on the prefix | new payload layout (one tiled codestream) |
| prev2 | 20 | 0.973 / 0.30 | 100 % / 100 % / 42 % | 4 % | 0 % | 100 % | 0 % | +1.4 s render + encode (2.9 kB) | decode a 2.9 kB JPEG XL; show until the full image | new chunk kind (preview copy) + its index range in START |
| prev3 | 30 | 0.970 / 0.33 | 100 % / 100 % / 42 % | 5 % | 0 % | 100 % | 0 % | +1.4 s | as prev2 | as prev2 |
| mdc | 0 | 0.926 / 0.73 | 100 % / 100 % / 42 % | 0 % | 0 % | 100 % | 0 % | same encode time; +54–66 % bytes at equal d | 4 decodes + interpolate missing phases | new container (4 descriptions, chunk-aligned) |
| fec9 | 9 | 0.974 / 0.29 | 99 % / 74 % / 74 % | 100 % | 0 % | 0 % | 50 % | +0.07–0.08 s (numpy RS) | RS decode (~0.1 s) when ≥ k of n chunks | START carries k and m; chunk index ≥ k = parity; heal = 'any N more' |
| fec18 | 18 | 0.973 / 0.30 | 100 % / 88 % / 88 % | 100 % | 100 % | 0 % | 100 % | +0.07–0.08 s (numpy RS) | RS decode (~0.1 s) when ≥ k of n chunks | START carries k and m; chunk index ≥ k = parity; heal = 'any N more' |
| fec27 | 27 | 0.971 / 0.32 | 100 % / 94 % / 94 % | 100 % | 100 % | 0 % | 100 % | +0.07–0.08 s (numpy RS) | RS decode (~0.1 s) when ≥ k of n chunks | START carries k and m; chunk index ≥ k = parity; heal = 'any N more' |
| fec36 | 36 | 0.969 / 0.34 | 100 % / 98 % / 98 % | 100 % | 100 % | 0 % | 100 % | +0.07–0.08 s (numpy RS) | RS decode (~0.1 s) when ≥ k of n chunks | START carries k and m; chunk index ≥ k = parity; heal = 'any N more' |
| prev2+fec18 | 38 | 0.969 / 0.34 | 100 % / 100 % / 90 % | 100 % | 100 % | 100 % | 100 % | +1.5 s | both | both |
| prev2+fec27 | 47 | 0.967 / 0.35 | 100 % / 100 % / 98 % | 100 % | 100 % | 100 % | 100 % | +1.5 s | both | both |
