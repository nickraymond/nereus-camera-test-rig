# Test card: N6 firmware with Python threads + GIL overlap probe (Nick, chat 10/24: "Yes: build the N6 firmware with MICROPY_PY_THREAD on, back up, flash it, and run the quick GIL probe first. Stop if threads don't overlap.")

Pre-registered before it runs. Unit: nereus001 + spare N6 020023000450433547373200 (PS5520, stock lens), today v5.0.1 + tag25h9
(`fw_tag25h9/firmware.bin` sha256 273be3fa9a0a…). Why: `2026-10-24_ps5520_usb_fps/DOUBLE_BUFFER_FINDINGS.md` (no `_thread`; encode and USB write run in series).

## Firmware
- Patches on v5.0.1: `openmv/firmware/0001-n6-enable-apriltags-tag25h9.patch` (unchanged) + **`0002-n6-enable-python-threads.patch`** (`#define MICROPY_PY_THREAD (1)`
  under `#if defined(OPENMV_N6)` in OpenMV's `ports/stm32/mp_config.h`). Nothing else.
- Build: ADIN_SPI_OpenMV `firmware/openmv_build/build_n6.sh --rev v5.0.1 --openmv-dir ~/openmv-dev/openmv-n6-threads --patch 0001 --patch 0002`
  (a new tree, so the tag25h9 tree stays as it is).
- **Build check before any flash:** MANIFEST lists both patches; `firmware.elf` has the `_thread` module and `TAG25H9` (`nm` / `strings`);
  firmware.bin fits alt 1 (3,670,020 B). If any fails: stop, nothing is flashed.
- Source facts behind the expectation (not proof): with threads on, the stm32 wait macro releases the GIL and yields (`mphalport.h:73-81`),
  and the N6 JPEG encode waits on DMA with `mp_event_wait_ms` (`ports/stm32/stm_jpeg.c:516,538`).

## Backup + flash (procedure from the 10/23 tag25h9 flash, `2026-10-23_n6_eval_phase0/RESULTS.md`)
1. Board files to nereus001 `~/board_backups/<ts>/` (7 files + sha256).
2. `machine.bootloader()` → DFU 37c5:9206. **Read alt 1** to `~/fw/fw_before_threads.bin` and check its prefix equals the tag25h9 firmware.bin.
3. `dfu-util -a 1 -D threads/firmware.bin`; read alt 1 back and check the prefix equals the new image. Only alt 1 is written.
4. After boot: `omv` 5.0.1, `import _thread` works, `TAG25H9` present, sensor 0x5520, `board_hashes` ALL MATCH, an HD still `completed`.
- **Rollback:** bootloader → `dfu-util -a 1 -D ~/fw/tag25h9/firmware.bin`, read back, same boot checks.

## GIL probe (`openmv/probes/n6_thread_overlap_probe_v5.py`, mpremote, nothing written to /flash)
At 1920×1080 and 2592×1944, K = 10 frames, RGB565 → JPEG q70; payloads of the frame's size over USB:
t_enc, t_write, t_serial (encode then write), t_par (writer thread sends frame k while the main thread encodes k+1).
**overlap = (t_serial − t_par) / min(t_enc, t_write).** n = 1 run per size.

- **Go bar (pre-registered):** overlap ≥ 0.30 at 2592×1944 and all K payloads written. Then, as a separate step, a threaded writer in the stream
  service and the `bigsizes` sweep rerun (deploy with backup, as for fda23ad).
- **Stop (Nick's rule):** overlap < 0.30 → stop, report, and offer the rollback to the tag25h9 image. No more firmware changes in this card.

## Stop rules
- Build or image check fails → no flash. DFU read-back mismatch → rollback at once.
- The N6 does not come back on USB within 60 s after a flash → stop, ask Nick (replug / P12). Never lightsleep. Nothing on nereus002.
- Cost: build ~15–40 min on the Mac (docker), flash + checks ~5 min, probe ~2 min.
