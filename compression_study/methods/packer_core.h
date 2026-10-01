/*
 * packer_core.h — the lossless Bayer-plane packer's codec core (bitstream: see packer.c).
 *
 * Header-only, no libc beyond <stdint.h>/<stddef.h>, no allocation: the caller owns two
 * uint16 row buffers and the output buffer. Shared by the CLI (packer.c) and the MicroPython
 * native module (compression_study/natmod/packer_mod.c), so both write the same bytes.
 */
#ifndef PACKER_CORE_H
#define PACKER_CORE_H

#include <stddef.h>
#include <stdint.h>

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

#ifndef PACKER_NO_DECODE  /* encoder-only builds (the MCU module) leave the reader out */
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
#endif /* PACKER_NO_DECODE */

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

#ifndef PACKER_NO_DECODE
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
#endif /* PACKER_NO_DECODE */

#endif /* PACKER_CORE_H */
