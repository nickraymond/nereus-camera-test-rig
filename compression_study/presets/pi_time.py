"""Time one production nrjxl encode (rc_raw_jxl CLI, bm #120) on the Pi and report peak RSS.

    python3 pi_time.py NAME X,Y,W,H DISTANCE DNG_STEM OUTDIR [EFFORT]

The CLI runs in its own process with an outer RLIMIT_AS of 700 MiB (a safety cap so this
bench can never push the Zero into OOM; cjxl inside it keeps the production 250 MiB guard).
Peak RSS = max over the CLI python process and its cjxl children (RUSAGE_CHILDREN).
"""
import json, resource, subprocess, sys, time

name, crop, dist, stem, out = sys.argv[1:6]
effort = sys.argv[6] if len(sys.argv) > 6 else "5"
def cap():
    resource.setrlimit(resource.RLIMIT_AS, (700 * 2**20, 700 * 2**20))
    import os; os.nice(10)
t0 = time.monotonic()
p = subprocess.run([sys.executable, "rc_raw_jxl.py", "--dng", stem + ".dng", "--metadata",
                    stem + ".json", "--crop", crop, "--distances", dist, "--effort", effort,
                    "--allow-any-crop", "--encode-max-s", "600", "--message-cap", "2000",
                    "--out", out],
                   capture_output=True, text=True, preexec_fn=cap)
wall = time.monotonic() - t0
lines = [l for l in (p.stdout + p.stderr).splitlines() if "rung" in l or "rfb" in l.lower()
         or "Fallback" in l or "Error" in l]
print(json.dumps({"name": name, "crop": crop, "d": float(dist), "effort": int(effort),
                  "rc": p.returncode, "wall_s": round(wall, 2),
                  "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1),
                  "log": lines[-3:]}))
