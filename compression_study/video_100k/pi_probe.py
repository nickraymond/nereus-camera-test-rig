"""Run one shell command and measure it (runs INSIDE the Pi-matched container, stdlib only).

    python3 pi_probe.py '<shell command>'   ->  one JSON line on stdout

wall_s, cpu_s (user+sys of all children), peak_tree_rss_MiB (sum of VmRSS over the whole
process tree, sampled every 0.1 s: what the Zero's RAM must hold at once, e.g. decoder +
encoder in a pipe), peak_proc_rss_MiB (largest single process, VmHWM). Exit code = command's.
"""

import json
import os
import resource
import subprocess
import sys
import time


def tree(pid: int) -> list[int]:
    out, todo = [], [pid]
    kids: dict[int, list[int]] = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as f:
                    ppid = int(f.read().rsplit(")", 1)[1].split()[1])
                kids.setdefault(ppid, []).append(int(d))
            except OSError:
                pass
    while todo:
        p = todo.pop()
        out.append(p)
        todo += kids.get(p, [])
    return out


def status_kib(pid: int, key: str) -> int:
    try:
        with open(f"/proc/{pid}/status") as f:
            for ln in f:
                if ln.startswith(key):
                    return int(ln.split()[1])
    except OSError:
        pass
    return 0


def main() -> int:
    cmd = sys.argv[1]
    t0 = time.time()
    p = subprocess.Popen(["bash", "-o", "pipefail", "-c", cmd])
    peak_tree = peak_proc = 0
    while p.poll() is None:
        pids = tree(p.pid)
        rss = [status_kib(x, "VmRSS:") for x in pids]
        hwm = [status_kib(x, "VmHWM:") for x in pids]
        peak_tree = max(peak_tree, sum(rss))
        peak_proc = max([peak_proc, *hwm])
        time.sleep(0.1)
    wall = time.time() - t0
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    print(
        json.dumps(
            {
                "rc": p.returncode,
                "wall_s": round(wall, 2),
                "cpu_s": round(ru.ru_utime + ru.ru_stime, 2),
                "peak_tree_rss_MiB": round(peak_tree / 1024, 1),
                "peak_proc_rss_MiB": round(max(peak_proc, ru.ru_maxrss) / 1024, 1),
            }
        )
    )
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
