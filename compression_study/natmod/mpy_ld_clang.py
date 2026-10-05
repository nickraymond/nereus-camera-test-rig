"""MicroPython's ``tools/mpy_ld.py`` plus one ARM relocation clang emits and gcc does not.

clang ``-fpic`` reaches the GOT through ``R_ARM_GOT_PREL`` (type 96: GOT entry − P + A, a 32-bit
literal); arm-none-eabi-gcc uses ``R_ARM_GOT_BREL`` (26), the only form ``mpy_ld`` knows. 96 is
the ARM twin of x86-64 ``GOTPCREL``, which ``mpy_ld`` already links the same way. This wrapper
imports ``mpy_ld`` from ``$MPY_DIR/tools`` unmodified, registers 96 as a GOT relocation for the
Thumb-2 arches and handles it; everything else is ``mpy_ld``'s own code path.

    MPY_DIR=<micropython v1.28.0> python mpy_ld_clang.py <mpy_ld arguments>
"""

from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.environ["MPY_DIR"], "tools"))
import mpy_ld  # noqa: E402

R_ARM_GOT_PREL = 96

for arch in ("armv7m", "armv7emsp", "armv7emdp"):
    data = mpy_ld.ARCH_DATA[arch]
    if R_ARM_GOT_PREL not in data.arch_got:
        data.arch_got = tuple(data.arch_got) + (R_ARM_GOT_PREL,)

_upstream = mpy_ld.do_relocation_text


def do_relocation_text(env, text_addr, r):
    if env.arch.name == "EM_ARM" and r["r_info_type"] == R_ARM_GOT_PREL:
        place = r["r_offset"] + text_addr
        entry = env.got_entries[r.sym.name]
        reloc = env.got_section.addr + entry.offset - place
        (existing,) = struct.unpack_from("<I", env.full_text, place)  # REL: implicit addend
        struct.pack_into("<I", env.full_text, place, (existing + reloc) & 0xFFFFFFFF)
        mpy_ld.log(mpy_ld.LOG_LEVEL_3, "  {:08x} {} -> GOT+{:x} (GOT_PREL)".format(
            place, r.sym.name, entry.offset))
        return
    return _upstream(env, text_addr, r)


mpy_ld.do_relocation_text = do_relocation_text

if __name__ == "__main__":
    mpy_ld.main()
