## Stage 1 — Mac encoders (quality ceiling; no Pi measurement)

| candidate | ok | max B | VMAF worst (clip) | mean | p5 worst | S2 worst | card SSIM-Y worst |
|---|---|---|---|---|---|---|---|
| `aom_c3` | 6/6 | 99,255 | 77.5 (c3_round3) | 86.7 | 68.5 | 21.2 | 0.9795 |
| `vvc_slower` | 6/6 | 99,942 | 76.3 (c3_round3) | 85.5 | 69.2 | 26.9 | 0.9757 |
| `aom_c6` | 6/6 | 98,488 | 76.3 (c3_round3) | 86.1 | 68.3 | 18.2 | 0.9791 |
| `vvc_medium` | 6/6 | 99,847 | 75.3 (c3_round3) | 84.9 | 67.9 | 24.6 | 0.9738 |
| `svt3_p2` | 6/6 | 99,216 | 75.2 (c3_round3) | 85.3 | 64.9 | 13.0 | 0.9818 |
| `svt3_p4` | 6/6 | 98,153 | 74.5 (c4_round3) | 85.0 | 63.8 | 12.2 | 0.9812 |
| `x265_vs` | 6/6 | 98,274 | 73.1 (c3_round3) | 84.0 | 67.2 | 19.0 | 0.9598 |
| `rav1e_s4` | 6/6 | 99,937 | 71.9 (c3_round3) | 82.1 | 60.9 | 21.6 | 0.9648 |
| `svt3_p8` | 6/6 | 99,071 | 71.3 (c4_round3) | 83.2 | 58.6 | 5.1 | 0.9762 |
| `x264_vs` | 6/6 | 97,910 | 66.3 (c3_round3) | 80.4 | 58.0 | 0.2 | 0.9528 |

## Stages 2–3 — Pi recipes (container, emulated + calibrated)

| candidate | ok | max B | VMAF worst (clip) | mean | p5 worst | S2 worst | card SSIM-Y worst | Pi time low–high s | RSS MiB | Pi |
|---|---|---|---|---|---|---|---|---|---|---|
| `pi_svt_p6_lowmem+bgcache` | 6/6 | 99,733 | 79.1 (c3_round3) | 87.3 | 72.6 | 23.9 | 0.9748 | 147–175 | 135.4 | feasible |
| `pi_aom_c4` | 6/6 | 98,126 | 77.2 (c3_round3) | 86.5 | 68.7 | 20.2 | 0.9795 | 238–582 | 384.0 | flagged: only with zram/swap |
| `pi_svt_p4_lowmem` | 6/6 | 99,509 | 76.8 (c3_round3) | 85.8 | 72.0 | 20.4 | 0.9779 | 381–451 | 160.7 | feasible |
| `pi_svt_p4_lowmem_fixed150` (post-hoc) | 6/6 | 96,100 | 76.5 (c3_round3) | 85.3 | 69.2 | 20.1 | 0.9778 | 391–462 | 151.4 | feasible |
| `pi_aom_c6` | 6/6 | 98,488 | 76.3 (c3_round3) | 86.1 | 68.3 | 18.2 | 0.9791 | 79–241 | 323.5 | flagged: only with zram/swap |
| `pi_svt_p4` | 6/6 | 99,871 | 75.4 (c3_round3) | 85.4 | 66.6 | 13.1 | 0.9817 | 113–395 | 567.5 | not feasible |
| `pi_svt_p6_lowmem+roi` | 6/6 | 98,881 | 75.0 (c3_round3) | 84.7 | 69.7 | 14.5 | 0.9760 | 127–151 | 135.6 | feasible |
| `pi_svt_p6_lowmem` | 6/6 | 99,539 | 74.8 (c3_round3) | 84.8 | 68.5 | 14.9 | 0.9762 | 129–155 | 140.9 | feasible |
| `pi_svt_p6_lowmem_fixed150` (post-hoc) | 6/6 | 97,003 | 74.8 (c3_round3) | 84.5 | 67.6 | 14.4 | 0.9765 | 126–150 | 139.1 | feasible |
| `pi_svt_p6` | 6/6 | 99,139 | 74.7 (c3_round3) | 85.0 | 66.1 | 11.2 | 0.9806 | 51–185 | 492.5 | not feasible |
| `pi_svt_p6_lowmem+vq` | 6/6 | 99,967 | 74.2 (c3_round3) | 84.3 | 68.5 | 13.2 | 0.9760 | 122–146 | 135.6 | feasible |
| `pi_svt_p8` | 6/6 | 99,166 | 74.0 (c4_round3) | 84.5 | 66.1 | 10.1 | 0.9790 | 35–121 | 471.4 | not feasible |
| `pi_svt_p6_lowmem+fg8_nograin` | 6/6 | 98,772 | 73.9 (c3_round3) | 84.3 | 68.2 | 12.6 | 0.9754 | 298–390 | 153.1 | feasible |
| `pi_svt_p6_lowmem+fg8` | 6/6 | 98,772 | 73.8 (c3_round3) | 84.2 | 68.2 | 12.5 | 0.9728 | 300–393 | 149.5 | feasible |
| `pi_svt_p6_lowmem+hqdn3d` | 6/6 | 97,901 | 73.8 (c3_round3) | 84.0 | 67.4 | 10.6 | 0.9770 | 121–146 | 135.7 | feasible |
| `pi_svt_p6_lowmem+540p` | 6/6 | 97,168 | 73.8 (c3_round3) | 82.3 | 66.5 | 18.4 | 0.9626 | 82–102 | 92.2 | feasible |
| `pi_x265_slow` | 6/6 | 98,842 | 73.3 (c3_round3) | 84.2 | 67.2 | 16.9 | 0.9610 | 177–708 | 531.2 | not feasible |
| `pi_svt_p8_lowmem` | 6/6 | 99,976 | 72.6 (c3_round3) | 83.3 | 65.1 | 6.8 | 0.9763 | 61–78 | 131.2 | feasible |
| `pi_svt_p10` | 6/6 | 98,346 | 72.0 (c3_round3) | 83.4 | 62.7 | 3.5 | 0.9750 | 16–49 | 423.9 | not feasible |
| `pi_svt_p10_lowmem` | 6/6 | 99,099 | 71.8 (c3_round3) | 82.8 | 62.7 | 4.4 | 0.9728 | 32–44 | 128.0 | feasible |
| `pi_svt_p6_lowmem+fps5_mi` | 6/6 | 98,578 | 70.7 (c3_round3) | 82.7 | 52.1 | -3.4 | 0.9760 | 76–95 | 130.3 | feasible |
| `pi_svt_p6_lowmem+fps5` | 6/6 | 98,578 | 68.8 (c3_round3) | 82.0 | 47.4 | -12.0 | 0.9753 | 75–94 | 135.3 | feasible |
| `pi_x264_vs` | 6/6 | 98,028 | 66.9 (c3_round3) | 80.5 | 57.9 | 0.3 | 0.9552 | 38–114 | 377.4 | flagged: only with zram/swap |

## Stage 4 — sensitivity window (frames 150–199; not used for ranking)

| candidate | ok | max B | VMAF worst (clip) | mean | p5 worst | S2 worst | card SSIM-Y worst | Pi time low–high s | RSS MiB | Pi |
|---|---|---|---|---|---|---|---|---|---|---|
| `pi_svt_p6_lowmem+bgcache` | 6/6 | 99,835 | 64.4 (c3_round3@150) | 82.1 | 52.4 | -12.4 | 0.8875 | 190–225 | 135.5 | feasible |
| `pi_svt_p4_lowmem` | 6/6 | 99,550 | 58.9 (c3_round3@150) | 79.7 | 43.4 | -11.5 | 0.9126 | 437–517 | 162.1 | feasible |
| `pi_svt_p4_lowmem_fixed150` (post-hoc) | 6/6 | 94,528 | 58.2 (c3_round3@150) | 79.0 | 42.9 | -13.3 | 0.9099 | 492–580 | 152.7 | feasible |
| `pi_svt_p6_lowmem_fixed150` (post-hoc) | 6/6 | 99,115 | 55.5 (c4_round3@150) | 77.8 | 40.0 | -17.3 | 0.9033 | 186–220 | 140.2 | feasible |
| `pi_svt_p6_lowmem` | 6/6 | 99,270 | 54.1 (c4_round3@150) | 77.7 | 38.7 | -19.2 | 0.8963 | 162–191 | 135.6 | feasible |
| `pi_svt_p6_lowmem+roi` | 6/6 | 98,998 | 53.2 (c3_round3@150) | 77.4 | 38.7 | -21.0 | 0.8897 | 160–187 | 135.6 | feasible |

Calibration: n = 60 PR #88 jobs; wall ratio median 13.0, CPU ratio median 12.3 / max 14.12; stalls dropped: 0.

## Decision (PREREG v2 rule)

- top eligible worst-clip VMAF 79.1; tie set (within 3.0): pi_svt_p6_lowmem+bgcache (79.1), pi_svt_p4_lowmem (76.79)
- 1. standard bitstream first -> pi_svt_p4_lowmem
- 2. lowest Pi high time (451 s, within 20 % = equal) -> pi_svt_p4_lowmem (451 s)
- **winner: pi_svt_p4_lowmem**
