"""IMX708 on the Pi Zero 2 W: run the Phase 1 encoders natively and measure them (Phase 2).

    python -m compression_study.phase2.pi_bench --dng <frame.dng> --knobs knobs.json --out DIR
                                                [--capture]

Per step: wall time, peak RSS (``VmHWM`` polled from /proc for child processes, ``ru_maxrss``
for in-process numpy steps), output bytes, and the UPS (LiFePO4wered) power above the idle
baseline → energy. Encoders run as children with ``oom_score_adj = 1000``: on the 415 MB Zero
an overrun kills the benchmark step, never the workbench or the rig service.

The blobs are written with the study's own header (``raw_planes.assemble``) so the Mac decodes
them with the Phase 1 decoder: bit-exact for C / N, Phase 1 error for D / D2.
``--capture`` also times one fresh ``rpicam-still --raw`` (12 MP DNG + JPEG) capture.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

import numpy as np  # noqa: E402

from compression_study.common import from_rawframe, tool, write_pgm  # noqa: E402
from compression_study.methods import packer  # noqa: E402
from compression_study.methods import raw_planes as rp  # noqa: E402
from compression_study.phase2.pi_run_probe import PowerSampler  # noqa: E402

MEM_LIMIT = 250 * 2 ** 20  # address-space cap per encoder (cgroup memory is not delegated)


def _limits():
    Path("/proc/self/oom_score_adj").write_text("1000")
    resource.setrlimit(resource.RLIMIT_AS, (MEM_LIMIT, MEM_LIMIT))


def child(cmd: list[str], stdin: bytes | None = None, timeout: float = 900,
          capped: bool = True) -> dict:
    """Run one encoder: first in line for the OOM killer, and hard-capped at ``MEM_LIMIT`` of
    address space (an allocation past it fails in the encoder instead of swapping the Zero);
    peak RSS polled from /proc."""
    t0 = time.perf_counter()
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE if stdin is not None else None,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         preexec_fn=_limits if capped else None)
    hwm = 0
    out_box: dict = {}

    def feed():
        out_box["out"], out_box["err"] = p.communicate(stdin, timeout=timeout)

    th = threading.Thread(target=feed, daemon=True)
    th.start()
    while th.is_alive():
        try:
            for line in Path(f"/proc/{p.pid}/status").read_text().splitlines():
                if line.startswith("VmHWM:"):
                    hwm = max(hwm, int(line.split()[1]) * 1024)
        except (FileNotFoundError, ProcessLookupError):
            pass
        time.sleep(0.02)
    th.join()
    return {"rc": p.returncode, "seconds": time.perf_counter() - t0, "peak_rss": hwm,
            "stdout": out_box.get("out", b""), "stderr": out_box.get("err", b"")[-300:]}


class Meter:
    def __init__(self, power: PowerSampler):
        self.power, self.rows = power, []

    def window(self, name, t0, t1, **kw):
        s = [(t, v, i) for t, v, i in self.power.samples if t0 <= t <= t1 and v and i]
        w = float(np.mean([v * i / 1e6 for _, v, i in s])) if s else None
        self.rows.append({"step": name, "seconds": t1 - t0, "load_w": w, "n_power": len(s),
                          **kw})
        print(f"{name:28s} {t1 - t0:7.2f} s  {kw}", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dng", type=Path, required=True)
    ap.add_argument("--knobs", type=Path, required=True,
                    help="JSON {D: {T1: q, ...}, D2: {T1: distance, ...}, mode: modular}")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--capture", action="store_true")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--mem-limit-mb", type=int, default=250)
    args = ap.parse_args(argv)
    global MEM_LIMIT
    MEM_LIMIT = args.mem_limit_mb * 2 ** 20
    args.out.mkdir(parents=True, exist_ok=True)
    knobs = json.loads(args.knobs.read_text())
    power = PowerSampler()
    power.start()
    m = Meter(power)
    time.sleep(8)
    t = time.time()
    time.sleep(10)
    m.window("idle", t, time.time())

    if args.capture:
        with tempfile.TemporaryDirectory(dir=args.out) as d:
            t = time.time()
            # bare -o name with cwd: rpicam-still silently truncates long -o paths (S3)
            r = child(["sh", "-c", f"cd {d} && rpicam-still -n --immediate --raw -o cap.jpg"],
                      timeout=120, capped=False)  # the camera stack maps large buffers
            m.window("capture_rpicam_raw", t, time.time(), rc=r["rc"],
                     dng_bytes=(Path(d) / "cap.dng").stat().st_size
                     if (Path(d) / "cap.dng").exists() else None)

    from nereus_camera_test_rig.color.raw_io import read_dng
    t = time.time()
    raw = from_rawframe(read_dng(args.dng), "imx708")
    m.window("read_dng", t, time.time(), peak_rss_self=resource.getrusage(
        resource.RUSAGE_SELF).ru_maxrss * 1024)
    t = time.time()
    for _ in range(args.repeat):
        lin_codes, _ = rp.code_planes(raw, rp.RawSpec("C"))
    m.window("split_planes", t, time.time(), repeat=args.repeat)
    t = time.time()
    for _ in range(args.repeat):
        d_codes, _ = rp.code_planes(raw, rp.RawSpec("D", "sqrt", 8))
    m.window("split+sqrt_lut8", t, time.time(), repeat=args.repeat)
    d2_codes, _ = rp.code_planes(raw, rp.RawSpec("D2", "sqrt", 12))
    n8_codes, _ = rp.code_planes(raw, rp.RawSpec("N", "sqrt", 8))

    bin_ = packer.build_c(args.out / "bin")
    blobs = {}

    def run_planes(label, spec, codes, maxval, make_cmd, suffix):
        payloads, t = {}, time.time()
        peak, fail = 0, None
        with tempfile.TemporaryDirectory(dir=args.out) as d:
            for name, plane in codes.items():
                src = Path(d) / f"{name}.pgm"
                dst = Path(d) / f"{name}.{suffix}"
                stdin = None
                if suffix == "pk":
                    stdin = plane.astype("<u2").tobytes()
                else:
                    write_pgm(src, plane, maxval)
                r = child(make_cmd(src, dst, plane), stdin=stdin)
                peak = max(peak, r["peak_rss"])
                if r["rc"] != 0:
                    fail = f"{name}: rc {r['rc']} {r['stderr'][-200:]!r}"
                    break
                payloads[name] = r["stdout"] if suffix == "pk" else dst.read_bytes()
        t1 = time.time()
        if fail:
            m.window(label, t, t1, failed=fail, peak_rss=peak)
            return
        blob = rp.assemble(raw, spec, payloads)
        (args.out / f"{label.replace('/', '_')}.bin").write_bytes(blob)
        blobs[label] = len(blob)
        m.window(label, t, t1, bytes=len(blob), bpp=len(blob) * 8 / raw.n_px, peak_rss=peak)

    h, w = lin_codes["R"].shape
    pk = lambda b: (lambda s, d, p: [str(bin_), "enc", str(w), str(h), str(b)])  # noqa: E731
    run_planes("C", rp.RawSpec("C"), lin_codes, raw.white, pk(raw.bits), "pk")
    run_planes("N/b8", rp.RawSpec("N", "sqrt", 8), n8_codes, 255, pk(8), "pk")
    for t_name, q in knobs.get("D", {}).items():
        run_planes(f"D/{t_name}", rp.RawSpec("D", "sqrt", 8), d_codes, 255,
                   lambda s, d, p, q=q: [tool("cjpeg"), "-grayscale", "-quality", str(int(q)),
                                         "-optimize", "-outfile", str(d), str(s)], "jpg")
    for mode in (knobs.get("mode", "modular"), "vardct"):
        flag = {"modular": ["-m", "1"], "vardct": ["-m", "0"]}[mode]
        for t_name, dist in knobs.get("D2", {}).items():
            for effort in (7, 3):
                run_planes(f"D2/{mode}/e{effort}/{t_name}",
                           rp.RawSpec("D2", "sqrt", 12, mode=mode), d2_codes, 4095,
                           lambda s, d, p, dist=dist, flag=flag, effort=effort:
                           [tool("cjxl"), str(s), str(d), "-d", f"{dist:.4f}", *flag,
                            "-e", str(effort), "--num_threads=0"], "jxl")
    for e in (3, 7):
        run_planes(f"C2/e{e}", rp.RawSpec("C2", effort=e), lin_codes, 1023,
                   lambda s, d, p, e=e: [tool("cjxl"), str(s), str(d), "-d", "0", "-e", str(e),
                                         "--num_threads=0"], "jxl")
    power.stop_flag = True
    meta = {"dng": str(args.dng), "frame": list(raw.shape), "knobs": knobs,
            "mem_limit_mb": args.mem_limit_mb,
            "uname": os.uname()._asdict() if hasattr(os.uname(), "_asdict") else str(os.uname()),
            "tools": {t: subprocess.run([tool(t), "--version"], capture_output=True,
                                        text=True).stdout.splitlines()[:1]
                      for t in ("cjxl",)}}
    (args.out / "bench.json").write_text(json.dumps({"meta": meta, "steps": m.rows,
                                                     "power": power.samples}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
