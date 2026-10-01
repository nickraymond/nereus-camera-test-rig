/*
 * Lossless Bayer-plane packer: MED prediction + adaptive Golomb-Rice (LOCO-I / JPEG-LS style).
 *
 * C twin of packer.py; both write the same bytes. libc only. The core (pack_encode_row /
 * pack_decode_row) works on caller-provided row buffers and a bit writer / reader, so an MCU
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

/* ------------------------------------------------------------------ bit writer / reader */

typedef struct {
    uint8_t *buf;   /* output bytes; caller drains buf[0..pos) and may reset pos to 0 */
    size_t pos;
    uint32_t acc;   /* pending bits live in the low `nbits` bits */
    int nbits;      /* 0..7 between calls */
} BitWriter;

/* Append the n low bits of value (n <= 24), MSB first. */
static void bw_put(BitWriter *bw, uint32_t value, int n)
{
    bw->acc = (bw->acc << n) | (value & ((1u << n) - 1u));
    bw->nbits += n;
    while (bw->nbits >= 8) {
        bw->nbits -= 8;
        bw->buf[bw->pos++] = (uint8_t)(bw->acc >> bw->nbits);
    }
}

static void bw_zeros(BitWriter *bw, uint32_t n)
{
    while (n > 24) {
        bw_put(bw, 0, 24);
        n -= 24;
    }
    bw_put(bw, 0, (int)n);
}

/* Zero-pad to a byte boundary (end of a plane). */
static void bw_pad(BitWriter *bw)
{
    if (bw->nbits > 0)
        bw_put(bw, 0, 8 - bw->nbits);
}

typedef struct {
    const uint8_t *buf;
    size_t len, pos;  /* next byte to load */
    uint32_t acc;     /* current byte */
    int nbits;        /* unread bits left in acc */
    int err;          /* set when reading past the end */
} BitReader;

static uint32_t br_bit(BitReader *br)
{
    if (br->nbits == 0) {
        if (br->pos >= br->len) {
            br->err = 1;
            return 1;  /* a one stops any zero run; err is checked by the caller */
        }
        br->acc = br->buf[br->pos++];
        br->nbits = 8;
    }
    br->nbits--;
    return (br->acc >> br->nbits) & 1u;
}

static uint32_t br_get(BitReader *br, int n)
{
    uint32_t v = 0;
    while (n-- > 0)
        v = (v << 1) | br_bit(br);
    return v;
}

/* ------------------------------------------------------------------ coder */

typedef struct {
    int b;        /* bit depth 1..16 */
    uint32_t qmax;/* LIMIT - qbpp - 1: zero-run length that signals an escape */
    uint32_t a0;  /* A at the start of every row */
} PackParams;

static int pack_params(PackParams *p, int b)
{
    int limit;
    if (b < 1 || b > 16)
        return -1;
    limit = 2 * (b + (b > 8 ? b : 8));
    p->b = b;
    p->qmax = (uint32_t)(limit - b - 1);
    p->a0 = ((1u << b) + 32u) >> 6;
    if (p->a0 < 2)
        p->a0 = 2;
    return 0;
}

/* MED prediction of cur[x]; prev == NULL for row 0. */
static uint32_t predict(const PackParams *p, const uint16_t *prev, const uint16_t *cur, int x)
{
    uint32_t a, up, c, lo, hi;
    if (prev == NULL)
        return x == 0 ? 1u << (p->b - 1) : cur[x - 1];
    if (x == 0)
        return prev[0];
    a = cur[x - 1];
    up = prev[x];
    c = prev[x - 1];
    lo = a < up ? a : up;
    hi = a < up ? up : a;
    if (c >= hi)
        return lo;
    if (c <= lo)
        return hi;
    return a + up - c;
}

static int rice_k(uint32_t A, uint32_t N)
{
    int k = 0;
    while ((N << k) < A)
        k++;
    return k;
}

/* Code one row. prev: previous row of the plane, or NULL for row 0. Returns -1 if a value
 * is >= 2^b. Output goes through bw; at most w * LIMIT bits are written. */
static int pack_encode_row(const PackParams *p, const uint16_t *prev, const uint16_t *cur,
                           int w, BitWriter *bw)
{
    const uint32_t half = 1u << (p->b - 1), mask = (1u << p->b) - 1u;
    uint32_t A = p->a0, N = 1;
    int x;
    for (x = 0; x < w; x++) {
        uint32_t pred, m, q;
        int32_t e;
        int k;
        if (cur[x] > mask)
            return -1;
        pred = predict(p, prev, cur, x);
        e = (int32_t)((cur[x] - pred + half) & mask) - (int32_t)half;
        m = e >= 0 ? 2u * (uint32_t)e : 2u * (uint32_t)(-e) - 1u;
        k = rice_k(A, N);
        q = m >> k;
        if (q < p->qmax) {
            bw_zeros(bw, q);
            bw_put(bw, 1, 1);
            bw_put(bw, m, k);
        } else {  /* escape: exactly LIMIT bits */
            bw_zeros(bw, p->qmax);
            bw_put(bw, 1, 1);
            bw_put(bw, m - 1u, p->b);
        }
        A += (uint32_t)(e < 0 ? -e : e);
        if (++N == 64) {
            A >>= 1;
            N >>= 1;
        }
    }
    return 0;
}

/* Decode one row into cur. Returns -1 on a corrupt or truncated stream. */
static int pack_decode_row(const PackParams *p, const uint16_t *prev, uint16_t *cur, int w,
                           BitReader *br)
{
    const uint32_t mask = (1u << p->b) - 1u;
    uint32_t A = p->a0, N = 1;
    int x;
    for (x = 0; x < w; x++) {
        uint32_t z = 0, m;
        int32_t e;
        int k = rice_k(A, N);
        while (br_bit(br) == 0)
            if (++z > p->qmax)
                return -1;
        if (z < p->qmax)
            m = (z << k) | br_get(br, k);
        else
            m = br_get(br, p->b) + 1u;
        if (br->err)
            return -1;
        e = (m & 1u) ? -(int32_t)((m + 1u) >> 1) : (int32_t)(m >> 1);
        cur[x] = (uint16_t)((predict(p, prev, cur, x) + (uint32_t)e) & mask);
        A += (uint32_t)(e < 0 ? -e : e);
        if (++N == 64) {
            A >>= 1;
            N >>= 1;
        }
    }
    return 0;
}

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
