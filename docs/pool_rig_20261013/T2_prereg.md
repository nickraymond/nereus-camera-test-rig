# T2 unattended start — pre-registration (2026-10-13 3:26 PM PT)
Question: with pool mode armed, does a full power cycle (UPS cuts the Pi + boards, wakes them by timer) bring the loop back by itself, and are the files intact after a stop?
Conditions: bench; pool_mode.sh arm --hours 2.5 (lens 0.8); power cycle via scripts/ups_power_cycle.sh --yes --wake-min 1 (Nick's request 3:20 PM). Home Wi-Fi present. LIMITATION: "no network" is not tested physically. By design the boot path needs none: cron @reboot, plus localhost:8088 only, which is allowed to not answer.
n: 1 power cycle.
Pass bar: Pi answers ssh within 6 min; a new run with started_by=boot and its first set within 3 min of boot; lsusb device numbers change (the boards were really power-cycled); after `pool_mode.sh stop`, every set's SHA256SUMS verifies on the Pi and after the Mac pull.
Stop rule: Pi not back in 10 min -> NEEDED: press the UPS touch button (EM).
