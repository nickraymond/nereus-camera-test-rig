# T1 bench smoke — pre-registration (2026-10-13 3:11 PM PT)
Question: does the pool loop capture all 3 cameras (still + RAW) every set, in plausible sizes, fast enough?
Conditions: nereus002 on the bench, in air, room light, housing open; lens 0.8 manual; AE/AWB auto; back-to-back sets; started with pool_mode.sh start (background, supervisor).
n: 10 sets.
Pass bar: 10/10 sets with 3/3 cameras still+raw completed; SHA256SUMS verify (sha256sum -c) on every set; DNG 24.0 MB ±0.1, Bayer = 1,024,000 B each, JPEGs > 50 kB; set time median <= 120 s and max <= 150 s; 0 tracebacks; throttled 0x0.
Stop rule: stop at the first traceback/timeout, or 2 sets in a row with the same camera failing -> diagnose, fix, rerun.

Attempt 1 (pool001, 3:12 PM): STOPPED after set 1. The IMX708 was OK; N6 and AE3 FAILED because the boards were left in Nick's recorder/REPL state (N6 at >>>; AE3 wedged, not reading USB). Set time 269 s from USB timeouts. Fixes: N6 hard-reset into its service; per-camera backoff added; AE3 -> UPS power cycle (T2).
Attempt 2 = the first 10 sets of the T2/T3 chained run (same pass bar; AE3 included only if it recovered).
