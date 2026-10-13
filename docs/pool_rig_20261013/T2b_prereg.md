# T2b automatic UPS recovery: pre-registration (2026-10-13 3:40 PM PT)
Question: when a board keeps failing, does the loop request exactly one UPS power cycle, come back by itself, and resume capture?
Conditions: after T3, rig idle. A test config gives the N6 a wrong USB serial (the board is "absent"), so the N6 fails every attempt. POOL_UPS_TAG=test, so the cycle does not spend the pool_auto daily budget. Uptime > 600 s. Started with pool_mode.sh start --config <test config>. The armed boot-start uses the normal config.
Expected path: the N6 fails sets 1-2 and is benched; it is retried at set 5 (3rd failed attempt) -> UPS cycle -> boot -> new run with the real config.
n: 1.
Pass bar: exactly one `CYCLE tag=test` line in ~/ups_power_cycle.log; the test run's manifest has stop_reason=ups_power_cycle; the IMX708 and AE3 are OK in every test-run set; a new run with started_by=boot starts after the cycle and its first set is OK 3/3; the Pi is back within 6 min.
Stop rule: Pi not back in 10 min -> NEEDED: UPS touch button (EM).
