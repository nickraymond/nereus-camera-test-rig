"""Does a second thread overlap the N6's JPEG encode with the USB write? (TEST_CARD_N6_THREADS, Nick 10/24)

Needs the threads firmware (openmv/firmware/0002-n6-enable-python-threads.patch). Run with
``mpremote connect <port> run openmv/probes/n6_thread_overlap_probe_v5.py > out.txt`` on the Pi, then
``mpremote connect <port> reset``. Writes nothing to /flash. Result lines start with ``#G ``.

The "frame" written to USB is a run of b"U" bytes of the encoded frame's size, never the JPEG itself:
a 0x04 byte would end mpremote's raw-REPL read. Content does not matter for the timing.

Per size (1920x1080, 2592x1944), K frames each, RGB565, JPEG q70:
  t_enc    snapshot + compress only
  t_write  USB writes only (K payloads)
  t_serial snapshot + compress, then write, one after the other (today's stream loop)
  t_par    main thread: snapshot + compress; writer thread: writes the previous payload meanwhile
overlap = (t_serial - t_par) / min(t_enc, t_write): the share of the shorter phase that was hidden.
"""
import gc
import json
import sys
import time

import sensor

try:
    import _thread
except ImportError:
    _thread = None

K = 10
QUALITY = 70
SIZES = (("1920x1080", "FHD"), ("2592x1944", "WQXGA2"))
OUT = sys.stdout.buffer


def emit(**kw):
    print("\n#G", json.dumps(kw))


def write_all(data):
    mv = memoryview(data)
    total = 0
    while total < len(mv):
        w = OUT.write(mv[total:])
        if w:
            total += w


def encode():
    img = sensor.snapshot()
    img.compress(quality=QUALITY)
    n = img.size()
    del img
    return n


def run_size(label, name):
    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    sensor.set_framesize(getattr(sensor, name))
    sensor.skip_frames(time=1500)
    n = encode()
    payload = b"U" * n
    gc.collect()

    t0 = time.ticks_ms()
    for _ in range(K):
        encode()
    t_enc = time.ticks_diff(time.ticks_ms(), t0)

    t0 = time.ticks_ms()
    for _ in range(K):
        write_all(payload)
    t_write = time.ticks_diff(time.ticks_ms(), t0)

    t0 = time.ticks_ms()
    for _ in range(K):
        encode()
        write_all(payload)
    t_serial = time.ticks_diff(time.ticks_ms(), t0)

    # One-slot hand-off: the main thread sets `pending`, the writer clears it when the payload is out.
    state = {"pending": 0, "stop": False, "written": 0}
    lock = _thread.allocate_lock()

    def writer():
        while True:
            with lock:
                job, stop = state["pending"], state["stop"]
            if job:
                write_all(payload)
                with lock:
                    state["pending"] = 0
                    state["written"] += 1
            elif stop:
                return
            else:
                time.sleep_ms(0)

    _thread.start_new_thread(writer, ())
    t0 = time.ticks_ms()
    for _ in range(K):
        encode()
        while True:  # wait until the writer has taken the previous frame
            with lock:
                if not state["pending"]:
                    state["pending"] = 1
                    break
            time.sleep_ms(0)
    while True:
        with lock:
            if not state["pending"]:
                state["stop"] = True
                break
        time.sleep_ms(0)
    t_par = time.ticks_diff(time.ticks_ms(), t0)
    time.sleep_ms(50)
    shorter = min(t_enc, t_write)
    emit(size=label, frames=K, jpeg_bytes=n, t_enc=t_enc, t_write=t_write, t_serial=t_serial,
         t_par=t_par, written_par=state["written"],
         overlap=round((t_serial - t_par) / shorter, 3) if shorter else None)
    del payload
    gc.collect()


def main():
    emit(kind="identity", thread=_thread is not None, sensor_id=hex(sensor.get_id()))
    if _thread is None:
        emit(kind="done", verdict="no _thread: wrong firmware")
        return
    for label, name in SIZES:
        try:
            run_size(label, name)
        except Exception as e:
            emit(size=label, ok=False, error=repr(e))
    emit(kind="done")


main()
