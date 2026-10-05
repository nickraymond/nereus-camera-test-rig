# Pool capture-side dry run — nereus002, 2026-10-05 (bench, in air, unsealed, no housing)

`scripts/hil_soak.py run --profile configs/experiments/pool_raw.yaml --exposure-lock …`, 10 sets.
Result in docs/SPEC_pool_codec_test.md §5a. Files: `soak.jsonl` (one line per set), `soak.json`,
`storage_check.json`, `power.csv`, the run log, the lock used (`exposure_lock.json`: hand-made
for the pass-through test, because the card is not meterable at the 1.5 m bench framing),
`lock_bench/exposure_lock.json` (the real `nereus-rig meter` attempt: 0 / 3 locked, card not
found), and each set's `experiment.json`. Captures (24 MB DNG + 2 × 1 MB Bayer per set, 286 MB)
stay on nereus002: `~/nereus-rig-pool/results/pool/20261005T005220Z/`.
