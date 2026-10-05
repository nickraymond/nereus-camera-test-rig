"""What does the backend show after a first send with real chunk loss? (nrjxl, 180 messages)

    NRJXL_BM_DIR=<bm #120 export> NRJXL_DATA=<primary>/data NRJXL_POOL=<run-7 folder> \
    python -m compression_study.preview_loss.study --traces traces.json --out results.json

Methods (all inside 180 messages of 288 B; the START/END messages are not counted):
  base      production nrjxl: one container (R, G1, G2, B modular codestreams); shown only
            when every chunk arrived (today's backend).
  plane     same bytes; the backend decodes every plane whose chunks all arrived (G only ->
            grey; needs chunk 0 for the container header).
  prog      A: the four planes 2x2-tiled into ONE progressive codestream (modular squeeze);
            stock libjxl decodes the received PREFIX (everything after the first hole is lost).
  prev{n}   B: a small colour preview (JPEG XL, 320x180 camera RGB) of 10 messages, sent n times
            (start / end / middle), + the production stream in the rest.
  mdc       C: 4 independent descriptions (2x2 polyphase of every plane), each its own
            codestream, chunk-aligned; any subset -> full field of view, missing phases
            interpolated.
  fec{m}    D: production stream in 180 - m chunks + m Reed-Solomon parity chunks: any
            180 - m of 180 rebuild it; otherwise as `plane` on the received data chunks.
  prev2+fec{m}  B + D.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

from compression_study import common
from compression_study.preview_loss import codec as C
from compression_study.preview_loss import frames as F

SLOTS = 180
PLANES = ("R", "G1", "G2", "B")
PREV_W, PREV_H, PREV_MSGS = 320, 180, 10
FEC_M = (9, 18, 27, 36)


# ------------------------------------------------------------------ loss patterns

def runs(received: list[int]) -> list[tuple[int, int]]:
    out, i = [], 0
    while i < len(received):
        if not received[i]:
            j = i
            while j < len(received) and not received[j]:
                j += 1
            out.append((i, j - i))
            i = j
        else:
            i += 1
    return out


def scale(trace: dict, n: int = SLOTS) -> np.ndarray:
    """A trace's loss runs moved to an n-slot burst: start scaled, run length kept."""
    lost = np.zeros(n, bool)
    for s, ln in runs(trace["received"]):
        a = min(int(round(s * n / trace["n"])), n - ln)
        lost[a:a + ln] = True
    return lost


def patterns(traces: list[dict]) -> dict[str, list[np.ndarray]]:
    rng = np.random.default_rng(5)
    burst = lambda ln: [np.r_[np.zeros(s, bool), np.ones(ln, bool),  # noqa: E731
                              np.zeros(SLOTS - s - ln, bool)] for s in range(SLOTS - ln + 1)]
    return {"traces": [scale(t) for t in traces],
            "burst8_any": burst(8), "burst16_any": burst(16),
            "tail40": [np.r_[np.zeros(SLOTS - 40, bool), np.ones(40, bool)]],
            "iid5": [rng.random(SLOTS) < 0.05 for _ in range(200)]}


# ------------------------------------------------------------------ methods

# a typical rpicam metadata set: only the container's fixed-size params block depends on it
META = {"ExposureTime": 16667, "AnalogueGain": 1.1228, "ColourGains": [2.0, 1.6],
        "ColourCorrectionMatrix": [1.8, -0.6, -0.2, -0.3, 1.6, -0.3, 0.0, -0.6, 1.6],
        "SensorTemperature": 40.0, "DigitalGain": 1.0, "ColourTemperature": 5300}
COLOUR = None


class Frame:
    def __init__(self, name, dng, xywh, note):
        self.name, self.note = name, note
        self.fr = C.load_frame(dng, xywh)
        self.R = Renderer = C.Renderer(self.fr)
        self.ref = Renderer.ref
        self.p = self.fr["codes"]
        self.cache: dict = {}
        self.enc_s: dict = {}

    # production container at a chunk budget -> (blob, planes' (offset, len), d, decoded)
    def production(self, k: int):
        key = ("prod", k)
        if key not in self.cache:
            global COLOUR
            rc = C.rc()
            COLOUR = COLOUR or rc.colour_params(META)
            crop = self.fr["crop"]

            def make(d):
                pl = [C.encode(self.p[n], 4095, d) for n in PLANES]
                params = rc.build_params(crop_xywh=self.fr["xywh"], native_wh=(4608, 2592),
                                         crc=0, colour=COLOUR, distance=d, effort=C.EFFORT)
                blob, _ = rc.seal_container(w=1600, h=900, cfa=crop["cfa"], black=crop["black"],
                                            white=crop["white"], params=params, payloads=pl)
                make.last = pl
                return blob
            t0 = time.perf_counter()
            d, blob = C.fit(make, k * C.MSG_B)
            pl = make(d) and make.last
            offs, pos = [], 0
            for payload in pl:
                pos = blob.index(payload, pos)
                offs.append((pos, len(payload)))
                pos += len(payload)
            dec = {n: C.decode(b) for n, b in zip(PLANES, pl)}
            self.cache[key] = (blob, offs, d, dec, time.perf_counter() - t0)
        return self.cache[key]

    def show_planes(self, dec: dict, have: set) -> np.ndarray | None:
        """Render whatever planes arrived: all four -> colour; any green -> grey."""
        key = ("planes", id(dec), frozenset(have))
        if key not in self.cache:
            g = [dec[n] for n in ("G1", "G2") if n in have]
            self.cache[key] = (self.R.render(dec) if set(PLANES) <= have else
                               self.R.render_grey(np.mean(g, axis=0)) if g else None)
        return self.cache[key]

    def full(self, k: int) -> np.ndarray:
        key = ("full", k)
        if key not in self.cache:
            self.cache[key] = self.R.render(self.production(k)[3])
        return self.cache[key]

    def preview(self):
        """The camera renders a small sRGB image itself (WB = the frame's grey-world gains,
        standing in for the AWB ColourGains the camera already has) -> VarDCT JPEG XL."""
        if "prev" not in self.cache:
            src = cv2.resize(self.ref, (PREV_W, PREV_H), interpolation=cv2.INTER_AREA)
            d, blob = C.fit(lambda d: C.encode(src.astype(np.uint16), 255, d, modular=False),
                            PREV_MSGS * C.MSG_B - 24)
            self.cache["prev"] = (blob, d, C.decode(blob).astype(np.uint8))
        return self.cache["prev"]

    def prog(self):
        if "prog" not in self.cache:
            t = common.tile(self.p)
            d, blob = C.fit(lambda d: C.encode(t, 4095, d), SLOTS * C.MSG_B - 64)
            self.cache["prog"] = (blob, d, {})
        return self.cache["prog"]

    def prog_show(self, n_chunks: int):
        blob, d, memo = self.prog()
        if n_chunks not in memo:
            b = blob[: n_chunks * C.MSG_B]
            out = C.decode(b, partial=n_chunks * C.MSG_B < len(blob)) if b else None
            memo[n_chunks] = None if out is None else self.R.render(common.untile(out))
        return memo[n_chunks]

    def mdc(self):
        if "mdc" not in self.cache:
            descs = [{n: self.p[n][dy::2, dx::2] for n in PLANES}
                     for dy in (0, 1) for dx in (0, 1)]
            tiles = [common.tile(dsc) for dsc in descs]

            def make(d):
                bl = [C.encode(t, 4095, d) for t in tiles]
                make.last = bl
                return b"x" * sum(C.chunks(len(b) + 16) for b in bl) * C.MSG_B
            d, _ = C.fit(make, SLOTS * C.MSG_B)
            make(d)
            bl = make.last
            dec = [common.untile(C.decode(b)) for b in bl]
            spans, pos = [], 0
            for b in bl:
                n = C.chunks(len(b) + 16)
                spans.append((pos, n))
                pos += n
            self.cache["mdc"] = (bl, d, dec, spans, {})
        return self.cache["mdc"]

    def mdc_show(self, have: tuple):
        bl, d, dec, spans, memo = self.mdc()
        if have not in memo:
            if not any(have):
                memo[have] = None
            else:
                full = {}
                for n in PLANES:
                    acc = np.zeros(self.p[n].shape, np.float64)
                    msk = np.zeros(self.p[n].shape, np.float64)
                    for j, (dy, dx) in enumerate([(0, 0), (0, 1), (1, 0), (1, 1)]):
                        if have[j]:
                            acc[dy::2, dx::2] = dec[j][n]
                            msk[dy::2, dx::2] = 1
                    num, den = acc.copy(), msk.copy()
                    for _ in range(3):                      # fill holes: normalized 3x3 mean
                        if den.min() > 0:
                            break
                        k = np.ones((3, 3))
                        s = cv2.filter2D(num * (den > 0), -1, k, borderType=cv2.BORDER_REFLECT)
                        c = cv2.filter2D((den > 0).astype(np.float64), -1, k,
                                         borderType=cv2.BORDER_REFLECT)
                        fill = (den == 0) & (c > 0)
                        num[fill] = s[fill] / c[fill]
                        den[fill] = 1
                    full[n] = num
                memo[have] = self.R.render(full)
        return memo[have]


def outcome(f: Frame, method: str, lost: np.ndarray):
    """-> (image or None, complete?) for one 180-slot loss pattern."""
    got = ~lost
    if method in ("base", "plane"):
        blob, offs, d, dec, _ = f.production(SLOTS)
        n = C.chunks(len(blob))
        if got[:n].all():
            return f.full(SLOTS), True, "full"
        if method == "base" or not got[0]:
            return None, False, None
        have = {name for name, (o, ln) in zip(PLANES, offs)
                if got[o // C.MSG_B:(o + ln - 1) // C.MSG_B + 1].all()}
        img = f.show_planes(dec, have)
        return img, False, "grey" if img is not None else None
    if method == "prog":
        blob = f.prog()[0]
        n = C.chunks(len(blob))
        first = int(np.argmax(~got[:n])) if not got[:n].all() else n
        img = f.prog_show(first)
        return img, first == n, ("full" if first == n else "prefix") if img is not None else None
    if method == "mdc":
        spans = f.mdc()[3]
        have = tuple(bool(got[a:a + ln].all()) for a, ln in spans)
        img = f.mdc_show(have)
        return img, all(have), ("full" if all(have) else f"mdc{sum(have)}") if img is not None \
            else None
    if method.startswith("prev") or method.startswith("fec"):
        npv = int(method[4]) if method.startswith("prev") else 0
        m = int(method.split("fec")[1]) if "fec" in method else 0
        k = SLOTS - npv * PREV_MSGS - m
        blob, offs, d, dec, _ = f.production(k)
        n = C.chunks(len(blob))
        # slot layout: [prev1] data [prev3 in the middle] data parity [prev2]
        order = []
        pv = [[("p", i)] * PREV_MSGS for i in range(npv)]
        data = [("d", i) for i in range(n)]
        if npv >= 1:
            order += pv[0]
        if npv >= 3:
            order += data[: n // 2] + pv[2] + data[n // 2:]
        else:
            order += data
        order += [("q", i) for i in range(m)]
        if npv >= 2:
            order += pv[1]
        order += [("pad", 0)] * (SLOTS - len(order))
        rx_data = np.zeros(n, bool)
        rx_par = 0
        prev_ok = set()
        prev_slots: dict = {}
        for s, (kind, i) in enumerate(order[:SLOTS]):
            if kind == "d":
                rx_data[i] = got[s]
            elif kind == "q":
                rx_par += int(got[s])
            elif kind == "p":
                prev_slots.setdefault(i, []).append(got[s])
        prev_ok = {i for i, v in prev_slots.items() if all(v)}
        if rx_data.all() or (m and rx_data.sum() + rx_par >= n):
            return f.full(k), True, "full"
        if prev_ok:                      # colour preview beats a grey plane render
            return f.preview()[2], False, "preview"
        if rx_data[0]:
            have = {name for name, (o, ln) in zip(PLANES, offs)
                    if rx_data[o // C.MSG_B:(o + ln - 1) // C.MSG_B + 1].all()}
            img = f.show_planes(dec, have)
            if img is not None:
                return img, False, "grey"
        return None, False, None
    raise ValueError(method)


METHODS = ["base", "plane", "prog", "prev2", "prev3", "mdc"] + [f"fec{m}" for m in FEC_M] + \
          ["prev2+fec18", "prev2+fec27"]


def evaluate(f: Frame, pats: dict) -> dict:
    res = {}
    for meth in METHODS:
        row = {}
        for pname, plist in pats.items():
            vis = comp = col = 0
            ss, de = [], []
            kinds: dict = {}
            memo = {}
            for lost in plist:
                img, ok, kind = outcome(f, meth, lost)
                vis += img is not None
                comp += ok
                col += img is not None and kind != "grey"
                kinds[kind or "none"] = kinds.get(kind or "none", 0) + 1
                if img is not None:
                    key = id(img)          # images are cached on the Frame, so ids are stable
                    if key not in memo:
                        memo[key] = (img, C.score(img, f.ref))
                    ss.append(memo[key][1]["ssim"])
                    de.append(memo[key][1]["de_med"])
            n = len(plist)
            row[pname] = {"n": n, "p_visible": round(vis / n, 3), "p_complete": round(comp / n, 3),
                          "p_colour": round(col / n, 3), "kinds": kinds,
                          "ssim_shown_med": round(float(np.median(ss)), 4) if ss else None,
                          "ssim_shown_min": round(float(np.min(ss)), 4) if ss else None,
                          "de_shown_med": round(float(np.median(de)), 2) if de else None,
                          "ssim_expected": round(float(np.sum(ss)) / n, 4)}
        res[meth] = row
    return res


def describe(f: Frame) -> dict:
    """Bytes / distance / complete quality per method (no loss)."""
    out = {}
    blob, offs, d, dec, t = f.production(SLOTS)
    full = C.score(f.R.render(dec), f.ref)
    out["base"] = {"d": d, "bytes": len(blob), "chunks": C.chunks(len(blob)), "full": full,
                   "plane_bytes": [ln for _, ln in offs]}
    pb, pd, _ = f.prog()
    out["prog"] = {"d": pd, "bytes": len(pb), "chunks": C.chunks(len(pb)),
                   "full": C.score(f.prog_show(C.chunks(len(pb))), f.ref),
                   "first_decodable_chunk": next((c for c in range(1, C.chunks(len(pb)) + 1)
                                                  if f.prog_show(c) is not None), None)}
    # size of the tiled progressive stream at the production distance (penalty vs base)
    out["prog"]["bytes_at_base_d"] = len(C.encode(common.tile(f.p), 4095, d))
    bl, md, mdec, spans, _ = f.mdc()
    out["mdc"] = {"d": md, "bytes": sum(len(b) for b in bl), "chunks": sum(s[1] for s in spans),
                  "full": C.score(f.mdc_show((True,) * 4), f.ref),
                  "one_desc": C.score(f.mdc_show((True, False, False, False)), f.ref),
                  "two_desc": C.score(f.mdc_show((True, False, False, True)), f.ref),
                  "three_desc": C.score(f.mdc_show((True, True, True, False)), f.ref)}
    tiles = [common.tile({n: f.p[n][dy::2, dx::2] for n in PLANES})
             for dy in (0, 1) for dx in (0, 1)]
    base_at_d = sum(len(C.encode(f.p[n], 4095, d)) for n in PLANES)
    out["mdc"]["size_penalty_at_base_d"] = round(
        sum(len(C.encode(t, 4095, d)) for t in tiles) / base_at_d - 1, 4)
    pv, pvd, pimg = f.preview()
    out["preview"] = {"d": pvd, "bytes": len(pv), "msgs": PREV_MSGS, "size": [PREV_W, PREV_H],
                      "score": C.score(pimg, f.ref)}
    for npv, m in [(0, mm) for mm in FEC_M] + [(2, 0), (3, 0), (2, 18), (2, 27)]:
        k = SLOTS - npv * PREV_MSGS - m
        b, _, dk, deck, _ = f.production(k)
        out[f"k{k}"] = {"data_chunks": k, "d": dk, "bytes": len(b),
                        "full": C.score(f.R.render(deck), f.ref)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--frames", nargs="*")
    a = ap.parse_args()
    traces = json.loads(a.traces.read_text())
    pats = patterns(traces)
    result = {"slots": SLOTS, "msg_bytes": C.MSG_B, "effort": C.EFFORT, "frames": {}}
    for name, dng, xywh, note in F.frames():
        if a.frames and name not in a.frames:
            continue
        t0 = time.perf_counter()
        f = Frame(name, dng, xywh, note)
        result["frames"][name] = {"note": note, "xywh": list(xywh), "describe": describe(f),
                                  "methods": evaluate(f, pats)}
        print(name, f"{time.perf_counter() - t0:.0f} s", flush=True)
        a.out.write_text(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
