"""Per-chunk first-send loss traces from the bm_cam_legacy HIL Spotter consoles (read-only).

Every cellular payload the Spotter accepts is hex-dumped on its console; a media chunk starts
"<I{key}.{index}/{n}>". A chunk of a clip that was STARTed in a log but never accepted before
that clip's END was lost at the first send (queue full, or dropped). Heal resends (later wakes,
after END) are ignored: this is what the backend holds after the first send.

    python -m compression_study.preview_loss.traces --out traces.json
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

BM = Path("/Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/"
          "vigilant-proskuriakova-a3337c")
RUNS = ["g4_outdoor12h_20261002", "r1fix_cmdres_20261003", "c1_comms_20261003"]
CHUNK = re.compile(r"<I(\w+)\.(\d+)/(\d+)>")
START = re.compile(r"<START IMG> filename: (\S+), timestamp: (\S+), length: (\d+), key[^\w]*(\w+)")
END = re.compile(r"<END IMG> filename: (\S+),")


def decode(path: Path) -> list[str]:
    out = subprocess.run([sys.executable, str(BM / "hil/tools/hil_con_decode.py")],
                         stdin=path.open("rb"), capture_output=True, check=True).stdout
    return [ln for ln in out.decode("ascii", "replace").splitlines() if ln.startswith("CELL")]


def traces() -> list[dict]:
    clips: dict[str, dict] = {}
    for run in RUNS:
        for f in sorted((BM / "runs" / run / "console").glob("*.txt")):
            fname_key: dict[str, str] = {}
            for ln in decode(f):
                if m := START.search(ln):
                    fn, ts, n, key = m.groups()
                    fname_key[fn] = key
                    clips.setdefault(key, {"run": run, "log": f.name, "key": key, "file": fn,
                                           "captured": ts, "n": int(n), "got": set(),
                                           "open": True, "ended": False})
                elif m := END.search(ln):
                    key = fname_key.get(m.group(1))
                    if key in clips:
                        clips[key]["open"] = False
                        clips[key]["ended"] = True
                elif m := CHUNK.search(ln):
                    key, i, n = m.group(1), int(m.group(2)), int(m.group(3))
                    c = clips.get(key)
                    if c and c["open"]:
                        c["got"].add(i)
                        c["n_hdr"] = n
            for c in clips.values():
                c["open"] = False     # a clip's first send ends with its log
    out = []
    for c in clips.values():
        if not c["ended"] or not c["got"]:
            continue           # START or END not on this console: not a whole first send
        n = c.get("n_hdr", c["n"])
        base = 0 if min(c["got"]) == 0 else 1
        recv = [int((i + base) in c["got"]) for i in range(n)]
        out.append({k: c[k] for k in ("run", "log", "key", "file", "captured")}
                   | {"n": n, "index_base": base, "received": recv,
                      "lost": n - sum(recv)})
    return sorted(out, key=lambda t: t["captured"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    t = traces()
    ap.parse_args().out.write_text(json.dumps(t))
    lost = [x["lost"] for x in t]
    print(len(t), "first sends;", sum(1 for v in lost if v), "with loss; lost/clip", lost)
    return 0


if __name__ == "__main__":
    sys.exit(main())


def summarise(traces: list[dict]) -> dict:
    """Loss shape per first send: lost %, runs, tail vs middle."""
    from collections import Counter
    run_len, tail, mid, per = Counter(), 0, 0, []
    for t in traces:
        r, i, runs_ = t["received"], 0, []
        while i < len(r):
            if not r[i]:
                j = i
                while j < len(r) and not r[j]:
                    j += 1
                runs_.append((i, j - i))
                i = j
            else:
                i += 1
        for s, ln in runs_:
            run_len[ln] += 1
            if s + ln == len(r):
                tail += 1
            else:
                mid += 1
        per.append(round(100 * t["lost"] / t["n"], 1))
    lossy = [p for p in per if p > 0]
    return {"first_sends": len(traces), "with_loss": len(lossy),
            "lost_pct_all": sorted(per), "lost_pct_median_lossy": sorted(lossy)[len(lossy) // 2],
            "runs_middle": mid, "runs_tail": tail, "run_lengths": dict(sorted(run_len.items())),
            "by_run": dict(Counter(t["run"] for t in traces)),
            "lossy_by_run": dict(Counter(t["run"] for t in traces if t["lost"]))}
