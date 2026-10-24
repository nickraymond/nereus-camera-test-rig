# N6 firmware patches

Stock OpenMV firmware plus small, named patches that this rig needs. Each patch is one `git apply`-able diff against
an exact upstream release tag. The binaries are never committed: they are rebuilt from the tag + patch, and their
sha256 is recorded below.

## 0001 — enable tag25h9 AprilTags on the N6 (card V3)

**Why:** stock OpenMV **v5.0.1** builds only the tag36h11 family for the N6. `boards/OPENMV_N6/imlib_config.h`
(line 99 at tag v5.0.1, 631681e5ac5a) defines `IMLIB_ENABLE_APRILTAGS_TAG36H11` and nothing else. The reference
card V3 uses **tag25h9**, so `find_apriltags` on a stock N6 finds 0 of its tags. Measured 10/23: `dir(image)` has only
TAG36H11, and the other family bits return in 14 ms with nothing. The tag25h9 decoder is already in OpenMV's
`lib/imlib/apriltag.c`, behind `IMLIB_ENABLE_APRILTAGS_TAG25H9`. This patch is that one `#define`.

**Build** (Mac, Docker, SDK 1.6.0), with Nick's ADIN_SPI_OpenMV harness:
```bash
~/Documents/GitHub/ADIN_SPI_OpenMV/firmware/openmv_build/build_n6.sh --rev v5.0.1 \
    --openmv-dir ~/openmv-dev/openmv-n6-tag25h9 \
    --patch "$PWD/openmv/firmware/0001-n6-enable-apriltags-tag25h9.patch"
```
Built 2026-10-23: label `v5.0.1-2.gbe78cb31ce`, `firmware.bin` 1,988,448 B, sha256
`273be3fa9a0ad147fa8108a63b32feb3835f28938425f92cef724dc1446b7edc`. Check that the `TAG25H9` string and the
`tag25h9_create` symbol are in `firmware.elf`.

**Flash** (from the Pi the N6 is plugged into; FIRMWARE partition only):
```bash
mpremote connect <n6-by-id> exec 'import machine; machine.bootloader()'   # -> 37c5:9206 (DFU)
sudo dfu-util -d 37c5:9206 -a 1 -U fw_before.bin                          # read first: keep it
sudo dfu-util -d 37c5:9206 -a 1 -D firmware.bin
sudo dfu-util -d 37c5:9206 -a 1 -U fw_after.bin -R                        # read back + boot
```
The DFU alts are BOOTLOADER 0, FIRMWARE 1, FILESYSTEM 2, ROMFS0 3. **Only alt 1 is written.** Alt 1 takes the raw
`firmware.bin`. Verified on 10/23: the stock board's alt 1 began with OpenMV's official v5.0.1 N6 `firmware.bin`
byte for byte, and the read-back after flashing matched the new image. The bootloader, filesystem (the rig service)
and ROMFS are untouched.

**Verify after boot:**
```bash
mpremote connect <n6-by-id> exec "import omv, image; print(omv.version_string(), [n for n in dir(image) if 'TAG' in n])"
# expect: 5.0.1 ['TAG25H9', 'TAG36H11']
```
Then run `openmv/probes/n6_apriltag_snapshot_v5.py` with the V3 card in view: it should decode ids 0–3. On 10/23:
4 tags in 372 ms on one HD RGB565 frame.

**Rollback:** download OpenMV's official `firmware_v5.0.1.zip` from github.com/openmv/openmv/releases (sha256
`1338b9607b57…`), take `OPENMV_N6/firmware.bin` (sha256 `479c7a1656d9…`), and flash alt 1 the same way.

**Known limits:** tag25h9 has a small code distance, so false ids appear (10/24: id 30 and id 11 in 100 frames).
Filter to the card's ids (V3: 0–15). Units flashed so far: the spare N6 on nereus001 (serial 020023000450433547373200),
10/23 8:28 PM PT, approved by Nick in the N6 eval builder's chat.
