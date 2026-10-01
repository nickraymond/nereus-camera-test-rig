/*
 * hyd CLI — one grey plane → JPEG XL with the vendored hydrium (hyd_plane.h), for the Mac
 * study and the Pi. Decode with libjxl's djxl.
 *
 *   hyd enc W H NLUT BLACK WHITE HF GS LF < plane_u16le.raw > out.jxl
 *
 * Samples are codes 0..NLUT-1 (larger ones are clamped), linear light (code - BLACK) /
 * (WHITE - BLACK). HF / GS / LF = HF multiplier, globalScale, LF divisor. Prints
 * "peak_heap_bytes N" (hydrium's own allocations) on stderr.
 * Exit 0 ok; 1 bad arguments; 2 bad input / encoder error; 3 out of memory.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "hyd_plane.h"

size_t hyd_mem_peak(void);

int main(int argc, char **argv) {
    if (argc < 10 || strcmp(argv[1], "enc")) {
        fprintf(stderr, "usage: hyd enc W H NLUT BLACK WHITE HF GS LF < plane_u16le\n");
        return 1;
    }
    long W = atol(argv[2]), H = atol(argv[3]), nlut = atol(argv[4]);
    long black = atol(argv[5]), white = atol(argv[6]);
    long hf = atol(argv[7]), gs = atol(argv[8]), lf = atol(argv[9]);
    if (W < 1 || H < 1 || W > 65536 || H > 65536) return 1;
    size_t npx = (size_t)W * (size_t)H;
    uint16_t *img = malloc(npx * 2);
    uint8_t *raw = malloc(npx * 2);
    size_t cap = npx * 4 + 65536;
    uint8_t *out = malloc(cap);
    if (!img || !raw || !out) return 3;
    if (fread(raw, 1, npx * 2, stdin) != npx * 2) {
        fprintf(stderr, "hyd: short input\n");
        return 2;
    }
    for (size_t i = 0; i < npx; i++) img[i] = (uint16_t)(raw[2 * i] | (raw[2 * i + 1] << 8));
    const char *err;
    int32_t where;
    size_t n = hyd_plane_encode(img, 2, W, 1, (uint32_t)W, (uint32_t)H, (uint32_t)nlut,
                                (int32_t)black, (int32_t)white, (uint32_t)hf, (uint32_t)gs,
                                (uint32_t)lf, out, cap, &err, &where);
    if (!n) {
        fprintf(stderr, "hyd: %s (%d)\n", err, (int)where);
        return 2;
    }
    fwrite(out, 1, n, stdout);
    fprintf(stderr, "peak_heap_bytes %zu\n", hyd_mem_peak());
    return 0;
}
