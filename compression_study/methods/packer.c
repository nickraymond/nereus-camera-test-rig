/*
 * Lossless Bayer-plane packer: MED prediction + adaptive Golomb-Rice (LOCO-I / JPEG-LS style).
 *
 * C twin of packer.py; both write the same bytes. libc only. The core (packer_core.h:
 * pack_encode_row / pack_decode_row) works on caller-provided row buffers and a bit writer / reader, so an MCU
 * port (Cortex-M55) uses two static uint16 rows and a static output buffer; only the CLI
 * below allocates, once, at start.
 *
 * Bitstream (fixed; packer.py documents the same):
 *   - Samples in raster order. Predictor MED / LOCO-I, a = left, b = above, c = above-left:
 *       pred = min(a,b) if c >= max(a,b); max(a,b) if c <= min(a,b); else a + b - c.
 *     Edges: pixel (0,0) predicts 1 << (b-1); the rest of row 0 predicts the left
 *     neighbour; column 0 of rows >= 1 predicts the pixel above.
 *   - Residual e = x - pred, reduced modulo 2^b into [-2^(b-1), 2^(b-1)); zigzag
 *     m = 2e (e >= 0) or -2e - 1 (e < 0), so 0 <= m < 2^b.
 *   - Adaptive Rice parameter, state reset at the start of EVERY row:
 *     A = max(2, (2^b + 32) >> 6), N = 1. Per sample: k = smallest k >= 0 with
 *     (N << k) >= A; code m; then A += |e|, N += 1; when N reaches 64: A >>= 1, N >>= 1.
 *   - Limited-length code (JPEG-LS): qbpp = b, LIMIT = 2*(b + max(8, b)),
 *     qmax = LIMIT - qbpp - 1. q = m >> k. If q < qmax: q zero bits, a one bit, the k low
 *     bits of m. Else (escape): qmax zero bits, a one bit, m - 1 in qbpp bits.
 *   - Bits MSB-first within bytes; the plane's stream is zero-padded to a byte boundary.
 *   - Every sample costs at most LIMIT (<= 64) bits, so ceil(W*H*LIMIT/8) bytes bound a
 *     plane. A row costs at most ceil(W*LIMIT/8) + 1 bytes of output when drained per row.
 *
 * CLI (planes are raw uint16 little-endian, row-major):
 *   packer enc W H B < plane_u16le.raw > out.bin
 *   packer dec W H B < in.bin > plane_u16le.raw
 * Exit code 0 on success; 1 bad arguments; 2 bad input / corrupt stream; 3 I/O error.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "packer_core.h"

/* ------------------------------------------------------------------ CLI */

static int fail(int code, const char *msg)
{
    fprintf(stderr, "packer: %s\n", msg);
    return code;
}

static int cli_encode(const PackParams *p, int w, int h)
{
    size_t row_out = ((size_t)w * 64u + 7u) / 8u + 4u;  /* LIMIT <= 64 bits per sample */
    uint16_t *rows = malloc(2u * (size_t)w * sizeof(uint16_t));
    uint8_t *in = malloc(2u * (size_t)w);
    uint8_t *out = malloc(row_out);
    uint16_t *prev = NULL, *cur;
    BitWriter bw = {0};
    int y, x, rc = 0;
    if (!rows || !in || !out)
        return fail(3, "out of memory");
    bw.buf = out;
    cur = rows;
    for (y = 0; y < h && rc == 0; y++) {
        if (fread(in, 1, 2u * (size_t)w, stdin) != 2u * (size_t)w) {
            rc = fail(2, "short input: fewer than W*H uint16 samples");
            break;
        }
        for (x = 0; x < w; x++)
            cur[x] = (uint16_t)(in[2 * x] | (in[2 * x + 1] << 8));
        if (pack_encode_row(p, prev, cur, w, &bw) != 0) {
            rc = fail(2, "sample value >= 2^B");
            break;
        }
        if (y == h - 1)
            bw_pad(&bw);
        if (fwrite(out, 1, bw.pos, stdout) != bw.pos)
            rc = fail(3, "write failed");
        bw.pos = 0;  /* row drained; leftover bits stay in the accumulator */
        prev = cur;
        cur = (cur == rows) ? rows + w : rows;
    }
    if (rc == 0 && fgetc(stdin) != EOF)
        rc = fail(2, "trailing input: more than W*H uint16 samples");
    free(rows);
    free(in);
    free(out);
    return rc;
}

static int cli_decode(const PackParams *p, int w, int h)
{
    size_t cap = 1u << 16, len = 0, n;
    uint8_t *data = malloc(cap);
    uint16_t *rows = malloc(2u * (size_t)w * sizeof(uint16_t));
    uint8_t *out = malloc(2u * (size_t)w);
    uint16_t *prev = NULL, *cur;
    BitReader br = {0};
    int y, x, rc = 0;
    if (!data || !rows || !out)
        return fail(3, "out of memory");
    while ((n = fread(data + len, 1, cap - len, stdin)) > 0) {  /* whole stream in memory */
        len += n;
        if (len == cap) {
            uint8_t *bigger = realloc(data, cap * 2u);
            if (!bigger)
                return fail(3, "out of memory");
            data = bigger;
            cap *= 2u;
        }
    }
    br.buf = data;
    br.len = len;
    cur = rows;
    for (y = 0; y < h; y++) {
        if (pack_decode_row(p, prev, cur, w, &br) != 0) {
            rc = fail(2, "corrupt or truncated stream");
            break;
        }
        for (x = 0; x < w; x++) {
            out[2 * x] = (uint8_t)(cur[x] & 0xFFu);
            out[2 * x + 1] = (uint8_t)(cur[x] >> 8);
        }
        if (fwrite(out, 1, 2u * (size_t)w, stdout) != 2u * (size_t)w) {
            rc = fail(3, "write failed");
            break;
        }
        prev = cur;
        cur = (cur == rows) ? rows + w : rows;
    }
    /* The plane must end in the last byte, padded with zero bits. */
    if (rc == 0 && (br.pos != br.len || (br.acc & ((1u << br.nbits) - 1u)) != 0))
        rc = fail(2, "trailing data or non-zero padding after the plane");
    free(data);
    free(rows);
    free(out);
    return rc;
}

int main(int argc, char **argv)
{
    PackParams p;
    long w, h, b;
    if (argc != 5 || (strcmp(argv[1], "enc") != 0 && strcmp(argv[1], "dec") != 0))
        return fail(1, "usage: packer enc|dec W H B  (stdin -> stdout)");
    w = strtol(argv[2], NULL, 10);
    h = strtol(argv[3], NULL, 10);
    b = strtol(argv[4], NULL, 10);
    if (w < 1 || h < 1 || w > 1L << 20 || h > 1L << 20 || pack_params(&p, (int)b) != 0)
        return fail(1, "need 1 <= W, H <= 2^20 and 1 <= B <= 16");
    if (argv[1][0] == 'e')
        return cli_encode(&p, (int)w, (int)h);
    return cli_decode(&p, (int)w, (int)h);
}
