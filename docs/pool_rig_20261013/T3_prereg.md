# T3 soak — pre-registration (2026-10-13 3:26 PM PT)
Question: is the loop stable for >= 2 h: per-set time, USB, memory, CMA, disk, thermals; what does it draw (battery projection)?
Conditions: the same run as T2, continuing; bench, in air, housing NOT sealed (no one at the bench to close it; thermal result is optimistic for a sealed housing); room light (light drops at dusk, so exposure and sizes drift; not a failure).
n: every set in 2 h (~150 at ~45 s).
Pass bar: >= 98 % of sets have every non-benched camera OK, and any USB drop-out auto-recovers within 5 sets; median set time of the last 10 sets within +15 % of the first 10 (no drift); p90 <= 1.3x median; CPU max < 75 C in air; throttled 0x0 throughout; MemAvailable min > 100 MB; CmaFree min stays >= 60 MB; disk/set within 10 % of plan.
Power: median W during sets and per set (Wh); report runtime = battery Wh / avg W (cell capacity unknown).
Stop rule: 2 consecutive all-camera failures, CPU >= 80 C, or 2 h elapsed.
