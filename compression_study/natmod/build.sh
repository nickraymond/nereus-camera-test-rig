#!/bin/sh
# Build nrpack.mpy (MicroPython native module, arch armv7emdp = the N6 / AE3 Cortex-M55,
# MPY ABI 6.3 = MicroPython v1.28) with Apple / LLVM clang instead of arm-none-eabi-gcc —
# the steps of py/dynruntime.mk: qstr preprocess → compile → mpy_ld link (through
# mpy_ld_clang.py, which adds the one ARM relocation clang emits and gcc does not).
#
#   MPY_DIR=<micropython v1.28.0 checkout> PYTHONPATH=<dir with pyelftools> ./build.sh
#
# The module needs no libc functions; tiny freestanding stand-ins for the few libc headers the
# MicroPython headers include live in stubs/.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
: "${MPY_DIR:?set MPY_DIR to a micropython v1.28.0 checkout}"
PY=${PYTHON:-python3}
B="$HERE/build"
mkdir -p "$B"
"$PY" "$MPY_DIR/tools/mpy_ld.py" --arch armv7emdp --preprocess -o "$B/config.h" "$HERE/packer_mod.c"
clang --target=armv7em-none-eabi -mthumb -mcpu=cortex-m7 -mfpu=fpv5-d16 -mfloat-abi=hard \
  -ffreestanding -fpic -fno-common -fno-unwind-tables -fno-asynchronous-unwind-tables -fno-stack-protector -U_FORTIFY_SOURCE -std=c99 -Os \
  -Wall -Werror -Wno-typedef-redefinition -DNDEBUG -DNO_QSTR -DMICROPY_ENABLE_DYNRUNTIME \
  -DMICROPY_FLOAT_IMPL=MICROPY_FLOAT_IMPL_DOUBLE \
  -DMP_CONFIGFILE="<$B/config.h>" -I"$HERE/stubs" -I"$HERE" -I"$MPY_DIR" \
  -c "$HERE/packer_mod.c" -o "$B/packer_mod.o"
MPY_DIR="$MPY_DIR" "$PY" "$HERE/mpy_ld_clang.py" --arch armv7emdp --qstrs "$B/config.h" \
  -o "$HERE/nrpack.mpy" \
  "$B/packer_mod.o"
ls -l "$HERE/nrpack.mpy"
