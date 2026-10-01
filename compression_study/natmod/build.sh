#!/bin/sh
# Build the native modules nrpack.mpy (lossless packer), nrwl53.mpy (wl53 lossy codec) and
# nrhyd.mpy (hydrium JPEG XL, vendored in ../methods/hydrium)
# (MicroPython native modules, arch armv7emdp = the N6 / AE3 Cortex-M55,
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
for pair in packer_mod:nrpack wl53_mod:nrwl53; do
SRC=${pair%%:*}; MOD=${pair##*:}
"$PY" "$MPY_DIR/tools/mpy_ld.py" --arch armv7emdp --preprocess -o "$B/$SRC.config.h" "$HERE/$SRC.c"
clang --target=armv7em-none-eabi -mthumb -mcpu=cortex-m7 -mfpu=fpv5-d16 -mfloat-abi=hard \
  -ffreestanding -fpic -fno-common -fno-unwind-tables -fno-asynchronous-unwind-tables -fno-stack-protector -U_FORTIFY_SOURCE -std=c99 -Os \
  -Wall -Werror -Wno-typedef-redefinition -Wno-unused-function -DNDEBUG -DNO_QSTR -DMICROPY_ENABLE_DYNRUNTIME \
  -DMICROPY_FLOAT_IMPL=MICROPY_FLOAT_IMPL_DOUBLE \
  -DMP_CONFIGFILE="<$B/$SRC.config.h>" -I"$HERE/stubs" -I"$HERE" -I"$MPY_DIR" \
  -c "$HERE/$SRC.c" -o "$B/$SRC.o"
MPY_DIR="$MPY_DIR" "$PY" "$HERE/mpy_ld_clang.py" --arch armv7emdp --qstrs "$B/$SRC.config.h" \
  -o "$HERE/$MOD.mpy" "$B/$SRC.o"
ls -l "$HERE/$MOD.mpy"
done

# nrhyd: hyd_mod.c + the vendored hydrium sources + mini_libc.c, linked into one .mpy.
# -ffp-contract=off (no fused multiply-add) like the Mac / Pi CLI, so the floats — and the
# bytes — match; hydrium is C11 and not warning-clean under -Wall, so no -Werror for it.
CC_ARM="clang --target=armv7em-none-eabi -mthumb -mcpu=cortex-m7 -mfpu=fpv5-d16 -mfloat-abi=hard
  -ffreestanding -fpic -fno-common -fno-unwind-tables -fno-asynchronous-unwind-tables
  -fno-stack-protector -U_FORTIFY_SOURCE -O2 -ffp-contract=off -DNDEBUG -I$HERE/stubs"
HYD="$HERE/../methods/hydrium"
"$PY" "$MPY_DIR/tools/mpy_ld.py" --arch armv7emdp --preprocess -o "$B/hyd_mod.config.h" "$HERE/hyd_mod.c"
$CC_ARM -std=c99 -Wall -Werror -Wno-typedef-redefinition -Wno-unused-function -DNO_QSTR \
  -DMICROPY_ENABLE_DYNRUNTIME -DMICROPY_FLOAT_IMPL=MICROPY_FLOAT_IMPL_DOUBLE \
  -DMP_CONFIGFILE="<$B/hyd_mod.config.h>" -I"$HERE" -I"$MPY_DIR" -I"$HYD" \
  -c "$HERE/hyd_mod.c" -o "$B/hyd_mod.o"
$CC_ARM -std=c99 -fno-builtin -Wall -Werror -c "$HERE/mini_libc.c" -o "$B/mini_libc.o"
OBJS="$B/hyd_mod.o $B/mini_libc.o"
for f in bitwriter encoder entropy format libhydrium memory; do
  $CC_ARM -std=c11 -DHYD_HOST_ALLOC -I"$HYD" -w -c "$HYD/$f.c" -o "$B/hyd_$f.o"
  OBJS="$OBJS $B/hyd_$f.o"
done
# shellcheck disable=SC2086
MPY_DIR="$MPY_DIR" "$PY" "$HERE/mpy_ld_clang.py" --arch armv7emdp --qstrs "$B/hyd_mod.config.h" \
  -o "$HERE/nrhyd.mpy" $OBJS
ls -l "$HERE/nrhyd.mpy"
