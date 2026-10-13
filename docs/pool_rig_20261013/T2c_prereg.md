# T2c hard power cut (physical switch): pre-registration (2026-10-13 4:45 PM PT)
Question: does the loop survive Nick's physical switch? Each switch-off is a hard cut. Completed sets must be intact, the in-flight set at most is lost, and the boot-start after switch-on must work.
Conditions: nereus002 armed with the hard-cut code (feat/pool-hard-cut-hardening) and `--ups-recover-after 0`. A person at the bench switches OFF for ~10 s and then ON, while a set is in flight. No clean shutdown.
n: 2 cuts (Nick/EM ask n >= 2), >= 3 min apart.
Pass bar, per cut:
- the Pi is back and a new run (`started_by=boot`) has its first set OK 3/3 within 3 min of switch-on;
- in the cut run, every set with SHA256SUMS verifies with `sha256sum -c` (0 bad, 0 missing);
- at most the last set lacks SHA256SUMS;
- the run counter never repeats;
- the filesystem mounts read-write with no fsck failure in the journal.
Stop rule: the Pi is not back 5 min after switch-on, or the filesystem comes up read-only -> stop testing, report.
