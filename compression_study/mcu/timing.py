# ruff: noqa: E501, E702  (archived desk-study script, 2026-10-01)
"""Encode time + peak RSS for one 640x400 plane (n6 cool G1, T2 knobs), NEON vs no-SIMD builds."""
import json
import math
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from compression_study import mcu_codec_bench as b
from compression_study.common import split, srgb_eotf

S = Path(sys.argv[1]); data = Path('/Users/nickbuemond/Documents/GitHub/nereus-camera-test-rig/data/s4_20260930')
res = json.loads((b.OUT).read_text())
raw, ctx = b.frameset(data, 'n6', 'cool', 'air')
c = b.rs.rp.forward(split(raw.mosaic, raw.cfa)['G1'], 'sqrt', raw.black, raw.white, 12)
h, w = c.shape
def knob(m): return res['cost'][m]['knob']
def timeit(cmd, stdin=None, n=7):
    best = 1e9
    for _ in range(n):
        t = time.perf_counter(); r = subprocess.run(cmd, input=stdin, capture_output=True); dt = time.perf_counter() - t
        assert r.returncode == 0, (cmd, r.stderr[-300:]); best = min(best, dt)
    r = subprocess.run(['/usr/bin/time', '-l'] + cmd, input=stdin, capture_output=True)
    rss = int(re.search(rb'(\d+)\s+maximum resident set size', r.stderr).group(1))
    return best, rss / 2**20, len(r.stdout)
out = {}
# process-start baseline: a C binary that exits at once
base_t, base_rss, _ = timeit([str(S/'wl53'/'wl53_fast'), 'x', '1', '1'], n=15) if False else (None, None, None)
r = subprocess.run([str(S/'wl53'/'wl53_fast')], capture_output=True)
best = min((lambda: (lambda t: (subprocess.run([str(S/'wl53'/'wl53_fast')], capture_output=True), time.perf_counter() - t)[1])(time.perf_counter()))() for _ in range(15))
out['process_start_s'] = best
r = subprocess.run(['/usr/bin/time', '-l', str(S/'wl53'/'wl53_fast')], capture_output=True)
out['process_rss_mb'] = int(re.search(rb'(\d+)\s+maximum resident set size', r.stderr).group(1)) / 2**20
# wl53
k = knob('wl53'); plane = c.astype('<u2').tobytes()
for name in ('wl53_fast', 'wl53_novec'):
    t, rss, n = timeit([str(S/'wl53'/name), 'enc', str(w), str(h), '5', f'{k:.4f}', '0.15', '1', '0.4'], plane)
    out[name] = dict(s=t, rss_mb=rss, bytes=n)
ref = subprocess.run([str(S/'wl53'/'wl53'), 'enc', str(w), str(h), '5', f'{k:.4f}', '0.15', '1', '0.4'], input=plane, capture_output=True).stdout
fast = subprocess.run([str(S/'wl53'/'wl53_fast'), 'enc', str(w), str(h), '5', f'{k:.4f}', '0.15', '1', '0.4'], input=plane, capture_output=True).stdout
out['wl53_fast_identical'] = ref == fast
# hydrium
for m in ('hyd-256', 'hyd-1frame', 'hyd-256-lf4'):
    kk = knob(m); hf = max(1, int(math.floor(kk))); gs = int(np.clip(round(32768 * kk / hf), 1, 73728))
    shift = '-1' if m == 'hyd-1frame' else '0'; lf = '4' if m.endswith('lf4') else '1'
    v = srgb_eotf(c.astype(np.float64) / 4095).astype('<f4').tobytes()
    for binn in ('hyd', 'hyd_novec'):
        t, rss, n = timeit([str(S/'hydrium'/binn), str(w), str(h), str(hf), shift, '2', lf, str(gs)], v)
        out[f'{m}:{binn}'] = dict(s=t, rss_mb=rss, bytes=n)
# libjxl-tiny
with tempfile.TemporaryDirectory() as d:
    for m, vals in (('tiny-srgb', srgb_eotf(c / 4095.0)), ('tiny-lin', split(raw.mosaic, raw.cfa)['G1'].astype(float) / 255)):
        p = Path(d) / 'in.pfm'; b.write_pfm3(p, vals.astype(np.float32))
        for build in ('build', 'build-scalar'):
            t, rss, n = timeit([str(S/'libjxl-tiny'/build/'encoder'/'cjxl_tiny'), str(p), str(Path(d)/'o.jxl'), '-d', f"{knob(m):.4f}"])
            out[f'{m}:{build}'] = dict(s=t, rss_mb=rss)
print(json.dumps(out, indent=1))
(S/'timing.json').write_text(json.dumps(out, indent=1))
