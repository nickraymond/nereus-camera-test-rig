/*
 * wl53 CLI — the integer-only wl53 v1 plane codec (wl53_core.h), for the Mac study and the Pi.
 *
 *   wl53 enc W H Q16 MODE < plane_u16le.raw > out.bin      (samples 0..4095)
 *   wl53 dec W H < in.bin > recon_i16le.raw
 *
 * Q16 = the quantizer scale Q * 16 (integer). MODE 1 = contexts + run mode (default codec).
 * Exit 0 ok; 1 bad arguments; 2 bad input / corrupt stream / output overflow; 3 out of memory.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "wl53_core.h"

static uint8_t *read_all(size_t *n) {
    size_t cap = 1u << 20, len = 0, got;
    uint8_t *buf = malloc(cap);
    while (buf && (got = fread(buf + len, 1, cap - len, stdin)) > 0) {
        len += got;
        if (len == cap) {
            uint8_t *b2 = realloc(buf, cap *= 2);
            if (!b2) { free(buf); return NULL; }
            buf = b2;
        }
    }
    *n = len;
    return buf;
}

int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: wl53 enc W H Q16 MODE | wl53 dec W H\n"); return 1; }
    int W = atoi(argv[2]), H = atoi(argv[3]);
    if (W < 2 || H < 2 || W > 32768 || H > 32768) return 1;
    size_t npx = (size_t)W * (size_t)H, n;
    int16_t *img = malloc(npx * sizeof(int16_t));
    int32_t *t = malloc(sizeof(int32_t) * 2 * (size_t)(W > H ? W : H));
    uint8_t *in = read_all(&n);
    if (!img || !t || !in) return 3;
    if (!strcmp(argv[1], "enc") && argc >= 6) {
        uint32_t q16 = (uint32_t)strtoul(argv[4], NULL, 10);
        int mode = atoi(argv[5]);
        if (n != npx * 2 || q16 < 1) { fprintf(stderr, "wl53: bad input size\n"); return 2; }
        for (size_t i = 0; i < npx; i++) {
            uint16_t v = (uint16_t)(in[2 * i] | (in[2 * i + 1] << 8));
            if (v > 4095) { fprintf(stderr, "wl53: sample > 4095\n"); return 2; }
            img[i] = (int16_t)v;
        }
        size_t cap = npx * 4 + 64;
        uint8_t *out = malloc(cap);
        if (!out) return 3;
        size_t len = wl53_encode(img, W, H, q16, mode, t, out, cap);
        if (!len) { fprintf(stderr, "wl53: output overflow\n"); return 2; }
        fwrite(out, 1, len, stdout);
        return 0;
    }
    if (!strcmp(argv[1], "dec")) {
        if (wl53_decode(in, n, W, H, img, t)) { fprintf(stderr, "wl53: corrupt stream\n"); return 2; }
        for (size_t i = 0; i < npx; i++) {
            uint8_t b[2] = {(uint8_t)((uint16_t)img[i] & 0xFF), (uint8_t)((uint16_t)img[i] >> 8)};
            fwrite(b, 1, 2, stdout);
        }
        return 0;
    }
    return 1;
}
