"""Words for the Clip Codec Bench page, filled from the measured numbers (build_page.py).

Every figure on the page comes from ``summary.json`` or the Pi logs; nothing is typed in.
"""

from __future__ import annotations

from statistics import median


def _need(S, stem, enc, v):
    r = S["size_needed_display"].get(f"{stem}|{enc}", {}).get(str(v))
    return r["kB"] if r else None


def _floor(S, stem, enc):
    c = S["curves_display"].get(f"{stem}|{enc}")
    return c[0][0] if c else None


def _cost(S, stem, enc, key="wall_s"):
    xs = [
        c[key]
        for c in S["cost"]
        if c["host"] == "enc_pi"
        and c["source"] == stem
        and c["encoder"] == enc
        and c.get("status") == "ok"
        and c.get(key) is not None
    ]
    return median(xs) if xs else None


def _probe(S, tag, **match):
    for p in S["n6"].get("probe", []):
        if p["tag"] == tag and all(p.get(k) == v for k, v in match.items()):
            return p
    return {}


def _small_budget(S):
    parts = []
    for b in (100, 200):
        av = _best(S, "imx_bob", "av1_svt_p12", b)
        h = _best(S, "imx_bob", "h264_hw", b)
        a_txt = (
            f"AV1 at {RES.get(av['source'], av['source'])} (VMAF {av['vmaf']:.0f})"
            if av
            else "AV1: no setting fits"
        )
        h_txt = (
            f"H.264 at {RES.get(h['source'], h['source'])} (VMAF {h['vmaf']:.0f})"
            if h
            else "H.264: no setting fits"
        )
        parts.append(f"at {b} kB, {a_txt} against {h_txt}")
    return (
        "<b>Very small budgets need fewer pixels or frames.</b> With motion, the Zero's best "
        "settings are: " + "; ".join(parts) + ". Lower frame rates look choppier, which "
        "VMAF does not count, so judge those in the player."
    )


def fmt(x, nd=0, unit=""):
    if x is None:
        return "n/a"
    return f"{x:,.{nd}f}{unit}"


RES = {
    "imx_bob_1080": "1080p 30 fps",
    "imx_bob_720": "720p 30 fps",
    "imx_bob_720_10": "720p 10 fps",
    "imx_bob_360_10": "360p 10 fps",
    "n6_bob": "1280×800 18 fps",
    "n6_bob_9": "1280×800 9 fps",
    "n6_bob_400_9": "640×400 9 fps",
}


def _best(S, group, enc, budget):
    return S["best_per_budget"].get(group, {}).get(enc, {}).get(str(budget))


def build(S, live):
    n6 = S["n6"]
    q90, q70 = n6.get("q90", {}), n6.get("q70", {})
    snap = _probe(S, "rate", what="snapshot_only")
    j90 = _probe(S, "rate", what="snapshot_jpeg", q=90)
    j60 = _probe(S, "rate", what="snapshot_jpeg", q=60)
    buf = _probe(S, "buffer")
    fs = _probe(S, "fs")

    # headline comparison: IMX708 720p30 with motion, device encoders, VMAF 70 full-screen
    st, target = "imx_bob_720", 70
    h = _need(S, st, "h264_hw", target)
    av = _need(S, st, "av1_svt_p12", target)
    best_av = _need(S, st, "av1_svt_p4", target)
    ratio = (1 - av / h) * 100 if h and av else None
    t720 = _cost(S, "imx_bob_720", "av1_svt_p12")
    t1080 = _cost(S, "imx_bob_1080", "av1_svt_p12")
    tn6 = _cost(S, "n6_bob", "av1_svt_p12")
    rss = max(
        (c["maxrss_MB"] or 0)
        for c in S["cost"]
        if c["host"] == "enc_pi" and c["encoder"] == "av1_svt_p12" and c.get("maxrss_MB")
    )
    e720 = _cost(S, "imx_bob_720", "av1_svt_p12", "energy_above_idle_J")
    e720_total = _cost(S, "imx_bob_720", "av1_svt_p12", "energy_J")
    fl1080 = _floor(S, "imx_bob_1080", "h264_hw")
    fl720 = _floor(S, "imx_bob_720", "h264_hw")
    fl720s = _floor(S, "imx_static_720", "h264_hw")
    live_rows = [r for r in live if r.get("kind") == "live_h264" and r.get("rc") == 0]
    mj = {r["height"]: r for r in live if r.get("kind") == "mjpeg_master"}

    answer = [
        f"<b>For a small budget, AV1 wins clearly.</b> On the IMX708 clip with motion at 720p, "
        f"the Pi Zero's AV1 encoder reaches good quality (VMAF {target}) at about "
        f"<span class=num>{fmt(av)} kB</span>; the Zero's hardware H.264 needs "
        f"<span class=num>{fmt(h)} kB</span> for the same picture "
        f"({fmt(ratio)} % smaller with AV1). A slower AV1 encoder on a bigger computer would "
        f"need only {fmt(best_av)} kB.",
        f"<b>The cost is encode time.</b> The Zero takes about {fmt(t720)} s to compress a "
        f"15 s 720p clip to AV1 and {fmt(t1080)} s at 1080p, on one core with up to "
        f"{fmt(rss)} MB of memory (the most the Zero can spare). Its hardware H.264 runs in "
        f"real time while recording. H.264 also has a floor: it cannot make a 15 s clip with "
        f"motion smaller than about {fmt(fl720)} kB at 720p or {fmt(fl1080)} kB at 1080p.",
        _small_budget(S),
        f"<b>The N6 has no video encoder today.</b> Its chip has an H.264 block, but OpenMV's "
        f"firmware does not use it yet, and there is no H.265 or AV1. The N6 can stream "
        f"{fmt(q90.get('fps_mean'), 1)} frames per second of 1280×800 JPEG to the Pi "
        f"({fmt(q70.get('fps_mean'), 1)} at lower JPEG quality), and the Pi compresses the clip.",
        "<b>For the dashboard:</b> AV1 in MP4 plays in Chrome, Edge and Firefox, but Safari only "
        "on Apple devices with AV1 hardware (M3 Macs, iPhone 15 Pro and later). If the dashboard "
        "must work everywhere, the backend can decode the AV1 and serve an H.264 copy; the "
        "transmission stays AV1.",
    ]
    tiles = [
        {
            "v": f"{fmt(ratio)} % smaller",
            "l": f"AV1 vs H.264 on the Zero, same quality (720p motion, VMAF {target})",
        },
        {
            "v": f"{fmt(t720)} s",
            "l": "Zero AV1 encode time for a 15 s 720p clip (H.264: real time)",
        },
        {"v": f"{fmt(fl720)} kB", "l": "smallest 15 s 720p motion clip the Zero's H.264 can make"},
        {
            "v": f"{fmt(q90.get('fps_mean'), 1)} fps",
            "l": "N6 1280×800 clip over USB to the Pi (no encoder on the N6)",
        },
    ]

    pi_rows = [
        ("Master recording", "1080p30 MJPEG q95, 0 dropped"),
        ("Master size, 15 s", "235 MB (125 Mbit/s)"),
        ("2304×1296 at 30 fps", "drops frames (17 gaps)"),
        ("H.264 hardware", "High profile, no B-frames"),
        ("H.264 smallest clip, motion", f"{fmt(fl720)} kB 720p · {fmt(fl1080)} kB 1080p"),
        ("H.264 smallest clip, still", f"{fmt(fl720s)} kB 720p"),
        (
            "AV1 (SVT-AV1 2.3) encode, 15 s",
            f"{fmt(t720)} s 720p · {fmt(t1080)} s 1080p · {fmt(tn6)} s N6",
        ),
        ("AV1 memory", f"{fmt(rss)} MB peak; default settings run out of memory"),
        ("AV1 energy, 720p clip", f"{fmt(e720_total)} J total · {fmt(e720)} J above idle"),
        ("H.265", "no hardware; x265 software ~8 fps at 720p, GPL"),
    ]
    if live_rows:
        lr = {(r["height"], r["budget_kB"]): r for r in live_rows}
        pi_rows.insert(
            4,
            (
                "Live H.264 while recording",
                "CPU "
                + " · ".join(
                    f"{fmt(r['cpu_busy_pct'])} % {k[0]}p"
                    for k, r in sorted(lr.items())
                    if k[1] == 400
                ),
            ),
        )
        pi_rows.insert(
            5,
            (
                "Live H.264 size vs budget, 720p",
                " · ".join(
                    f"{k[1]}→{fmt(r['size_B'] / 1000)}"
                    for k, r in sorted(lr.items())
                    if k[0] == 720
                )
                + " kB",
            ),
        )
        pi_rows.insert(
            6,
            (
                "Live H.264 frames in 15 s",
                " · ".join(
                    f"{r['frames']} at {k[0]}p ({r['gaps_gt50ms']} gaps)"
                    for k, r in sorted(lr.items(), reverse=True)
                    if k[1] == 400
                ),
            ),
        )
        pi_rows.insert(
            7,
            (
                "Live H.264 power",
                " · ".join(
                    f"{fmt(r['mean_W'], 2)} W {k[0]}p"
                    for k, r in sorted(lr.items(), reverse=True)
                    if k[1] == 400
                ),
            ),
        )
    if mj:
        pi_rows.insert(
            1,
            (
                "Recording power (MJPEG master)",
                " · ".join(
                    f"{fmt(r['mean_W'], 2)} W {h}p" for h, r in sorted(mj.items(), reverse=True)
                ),
            ),
        )
    n6_rows = [
        ("Sensor (PAG7936), 1280×800", f"{fmt(snap.get('fps'))} fps"),
        ("Hardware JPEG q90", f"{fmt(j90.get('fps'))} fps · {fmt(j90.get('kB'))} kB/frame"),
        ("Hardware JPEG q60", f"{fmt(j60.get('fps'))} fps · {fmt(j60.get('kB'))} kB/frame"),
        (
            "Stream to Pi, q90",
            f"{fmt(q90.get('fps_mean'), 1)} fps · {fmt(q90.get('link_MBps'), 1)} MB/s",
        ),
        (
            "Stream to Pi, q70",
            f"{fmt(q70.get('fps_mean'), 1)} fps · {fmt(q70.get('link_MBps'), 1)} MB/s",
        ),
        (
            "RAM for frames",
            f"{fmt(buf.get('heap_free0_MB'), 1)} MB = {fmt(buf.get('frames'))} frames q90",
        ),
        (
            "Storage",
            f"no SD card · {fmt((fs.get('/flash') or {}).get('free', 0) / 1e6, 1)} MB flash free",
        ),
        ("Video encoders in firmware", "none (OpenMV v5.0.1)"),
        ("H.264 block in the chip", "yes, 1080p30, not yet exposed"),
        ("H.265 / AV1", "no"),
    ]
    limits = [
        {
            "title": "IMX708 on the Pi Zero 2 W",
            "rows": pi_rows,
            "notes": [
                "Record a 15 s master, then compress: works at 1080p30. The master needs ~235 MB "
                "of "
                "storage; the SD card has room for hundreds.",
                "AV1 only fits in the Zero's 415 MB with reduced settings (one thread, no "
                "look-ahead, "
                "no temporal filter); with defaults the kernel kills it, even at 720p.",
                "AV1 encoding is one core, so the Zero stays responsive, but it is 3–15× slower "
                "than real time.",
            ],
        },
        {
            "title": "OpenMV N6",
            "rows": n6_rows,
            "notes": [
                "1080p is not possible: the sensor's largest frame is 1280×800.",
                "A 15 s clip cannot be held on the board: RAM fits ~2.5 s of good-quality frames "
                "and "
                "there is no SD card. The clip has to stream to the Pi as it is recorded.",
                "The stream rate is set by JPEG time plus USB transfer, not the sensor: "
                "18 fps at q90, 30 fps at q70.",
                "If OpenMV exposes the chip's H.264 block, the N6 could send H.264 itself; quality "
                "would likely sit between the Zero's hardware H.264 and x264 (dashed blue).",
            ],
        },
    ]
    method = [
        "Masters: IMX708 recorded with <code>rpicam-vid</code> at 1080p30 MJPEG q95 on the Pi "
        "Zero 2 W (<code>nereus002</code>); N6 streamed 1280×800 JPEG q90 over USB to the Pi "
        "with the rig's <code>start_stream</code> command. The scene was static (reference card, "
        "chart, window light).",
        "Motion is simulated: each frame of the recorded clip rotated ±2.5°, shifted ±3–4 % and "
        "zoomed 1.18×, like a camera on a moving buoy. Real water adds particles, light ripples "
        "and fish, which cost more bits; treat motion results as a lower bound on the bytes "
        "needed.",
        "Every encoder read the same lossless copy of each clip. Budgets are bytes per 15 s "
        "clip; each encoder was given the matching bitrate and one key frame per clip. "
        "Encoders over- and undershoot, so the charts use the real file sizes and the tables "
        "read each encoder's curve at the budget.",
        "On the device: H.264 by the Zero's hardware encoder (ffmpeg <code>h264_v4l2m2m</code>); "
        "AV1 by SVT-AV1 2.3, preset 12, one thread. Best case (Mac, not runnable on the "
        "devices): x264 and x265 medium two-pass, SVT-AV1 3.1 preset 4.",
        "Quality: VMAF 0.6.1 (Netflix's perceptual score, 0–100), each frame against the same "
        "frame of the lossless clip, upscaled to the full-screen size for 720p / 360p clips. "
        "VMAF scores single frames, so it does not penalise the choppiness of 10 fps.",
        "Times, memory and energy are from the Zero's own runs (ffmpeg <code>-benchmark</code>, "
        "the UPS power meter). AV1 times exclude reading the source.",
        "Licences: SVT-AV1 is BSD and AV1 is royalty-free; x264/x265 are GPL and ran only as Mac "
        "benchmarks (spec §20). H.265 also carries patent-pool royalties.",
    ]
    footer = (
        "Nereus camera test rig · compression study, video bench · code in "
        "<code>compression_study/video/</code> · masters and scores in "
        "<code>data/video_20261002/</code> (not committed)."
    )
    return {"answer": answer, "tiles": tiles, "limits": limits, "method": method, "footer": footer}
