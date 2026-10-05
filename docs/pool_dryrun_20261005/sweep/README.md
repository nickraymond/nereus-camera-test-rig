# Exposure-sweep dry runs — nereus002, 2026-10-05 (bench, in air)

`nereus-rig experiment --raw --exposure-sweep` (5 RAWs per camera at 1/250 … 1/15 s, gain at the
floor). Records only; the 15 RAWs per run (~130 MB) stay on nereus002 under
`~/nereus-rig-pool/results/pool/passthrough/2026-10-05/exp_20261005T021027Z_pool_sweep` (run 1) and
`exp_20261005T021630Z_pool_sweep` (run 2). 178 s / 184 s per set, peak RSS 206 / 202 MB.

- **Run 1** (scored with the first scorer, kept as evidence): the room lamp went off between
  frames 2 and 3 and an arm crossed frame 3. The first scorer let dark, noisy frames look sharp
  and located the card only from frame 3 on. Fixed: noise-corrected sharpness, too-dark guard,
  card located on the brightest frame first. Re-scored offline with the fix: N6 / AE3 pick 1/15 s
  with flat card sharpness (still card); the IMX708 is confounded by the lamp change.
- **Run 2** (fixed scorer): someone at the rig moved a light panel with their hands through the
  IMX708's view. The IMX708 picked 1/125 s and flagged 1/60 to 1/15 s as blurred. The panel lit
  the N6 / AE3 head-on, so all their frames clipped: no pick. Sheet:
  https://claude.ai/artifact/TkZqowjH2gEey42QmdmxiR
