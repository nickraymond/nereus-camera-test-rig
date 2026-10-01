/*
 * wl53_core.h — "wl53" lossy plane codec, v1: integer-only, so every platform writes the same
 * bytes (Mac, Pi, Cortex-M55 native module). Header-only, no libc, no allocation, no 64-bit
 * division (Cortex-M has 32-bit udiv; 64-bit multiply + shift are inline).
 *
 * Ported from the desk-study prototype (compression_study/mcu/wl53/wl53.c, 2026-10-01), which
 * used floating-point steps; v1 bakes the same step table in fixed point:
 *   - integer LeGall 5/3 lifting DWT (JPEG 2000 reversible), L = 5 levels, Mallat layout,
 *     coefficients stored as int16 (inputs <= 12 bits);
 *   - per-band dead-zone quantizer, step q_b = Q * M_b where M_b = tilt^(level-1) / g_b
 *     (g_b = L2 norm of the band's synthesis basis, tilt = 0.4: coarser levels get finer
 *     steps), held as Q16 constants; q in 1/16 units; rounding offset r = 38/256 (0.15);
 *   - LL: MED prediction; detail bands: JPEG-LS-style adaptive Golomb-Rice with 4 contexts
 *     and a run mode for zero runs.
 * Stream: 9-byte header 'W', version 1, L, mode, Q16 (uint32 LE, Q * 16), rq; then the bits
 * (MSB first, zero-padded). Decoding only needs the stream and the plane size.
 *
 * Memory: the plane as int16 (2 B/px) + a 2 * max(W, H) int32 line buffer + the output.
 */
#ifndef WL53_CORE_H
#define WL53_CORE_H

#include <stddef.h>
#include <stdint.h>

#define WL_L 5
#define WL_NB (3 * WL_L + 1)
#define WL_VERSION 1
#define WL_HDR 9
#define WL_RQ 38  /* dead-zone rounding 0.15 * 256 */
/* round(65536 * tilt^(level-1) / g_b), tilt = 51/128 (the prototype's 0.4 as stored), bands
 * LL, then level 5..1 x (HL, LH, HH); g_b from mcu/wl53/gains5.h */
static const uint32_t WL_M5[WL_NB] = {
    31u, 146u, 146u, 274u, 727u, 727u, 1362u, 3563u, 3563u, 6560u, 16400u, 16400u, 28325u, 63117u, 63117u, 91181u};

/* ------------------------------------------------------------------ bits (<= 24 per put) */
typedef struct { uint8_t *buf; size_t pos, cap; uint32_t acc; int nbits; int err; } WlBW;
static void wl_put(WlBW *w, uint32_t v, int n) {
    if (n <= 0) return;
    w->acc = (w->acc << n) | (v & ((1u << n) - 1u));
    w->nbits += n;
    while (w->nbits >= 8) {
        w->nbits -= 8;
        if (w->pos >= w->cap) { w->err = 1; return; }
        w->buf[w->pos++] = (uint8_t)(w->acc >> w->nbits);
    }
}
static void wl_zeros(WlBW *w, int n) { while (n > 24) { wl_put(w, 0, 24); n -= 24; } wl_put(w, 0, n); }
static void wl_pad(WlBW *w) { if (w->nbits) wl_put(w, 0, 8 - w->nbits); }

typedef struct { const uint8_t *buf; size_t len, pos; uint32_t acc; int nbits; int err; } WlBR;
static int wl_bit(WlBR *r) {
    if (!r->nbits) {
        if (r->pos >= r->len) { r->err = 1; return 0; }
        r->acc = r->buf[r->pos++]; r->nbits = 8;
    }
    r->nbits--;
    return (r->acc >> r->nbits) & 1;
}
static uint32_t wl_get(WlBR *r, int n) { uint32_t v = 0; while (n-- > 0) v = (v << 1) | (uint32_t)wl_bit(r); return v; }

/* ------------------------------------------------------------------ 5/3 lifting */
static void wl_fwd1(int16_t *x, int n, int s, int32_t *t) {
    if (n < 2) return;
    int nl = (n + 1) / 2, nh = n / 2;
    int32_t *d = t + n;
    for (int i = 0; i < n; i++) t[i] = x[i * s];
    for (int i = 0; i < nh; i++) {
        int32_t a = t[2 * i], b = (2 * i + 2 < n) ? t[2 * i + 2] : t[2 * i];
        d[i] = t[2 * i + 1] - ((a + b) >> 1);
    }
    for (int i = 0; i < nl; i++) {
        int32_t dl = i > 0 ? d[i - 1] : d[0], dr = i < nh ? d[i] : d[nh - 1];
        x[i * s] = (int16_t)(t[2 * i] + ((dl + dr + 2) >> 2));
    }
    for (int i = 0; i < nh; i++) x[(nl + i) * s] = (int16_t)d[i];
}
static void wl_inv1(int16_t *x, int n, int s, int32_t *t) {
    if (n < 2) return;
    int nl = (n + 1) / 2, nh = n / 2;
    int32_t *d = t + n;
    for (int i = 0; i < nh; i++) d[i] = x[(nl + i) * s];
    for (int i = 0; i < nl; i++) {
        int32_t dl = i > 0 ? d[i - 1] : d[0], dr = i < nh ? d[i] : d[nh - 1];
        t[2 * i] = x[i * s] - ((dl + dr + 2) >> 2);
    }
    for (int i = 0; i < nh; i++) {
        int32_t a = t[2 * i], b = (2 * i + 2 < n) ? t[2 * i + 2] : t[2 * i];
        t[2 * i + 1] = d[i] + ((a + b) >> 1);
    }
    for (int i = 0; i < n; i++) x[i * s] = (int16_t)t[i];
}

typedef struct { int x0, y0, w, h; uint32_t qf; } WlBand;  /* qf = step * 16 */

static void wl_bands(int W, int H, uint32_t q16, WlBand *b) {
    int ws[WL_L + 1], hs[WL_L + 1], nb = 0;
    ws[0] = W; hs[0] = H;
    for (int l = 1; l <= WL_L; l++) { ws[l] = (ws[l - 1] + 1) / 2; hs[l] = (hs[l - 1] + 1) / 2; }
    b[nb++] = (WlBand){0, 0, ws[WL_L], hs[WL_L], 0};
    for (int l = WL_L; l >= 1; l--) {
        int lw = ws[l], lh = hs[l], fw = ws[l - 1], fh = hs[l - 1];
        b[nb++] = (WlBand){lw, 0, fw - lw, lh, 0};
        b[nb++] = (WlBand){0, lh, lw, fh - lh, 0};
        b[nb++] = (WlBand){lw, lh, fw - lw, fh - lh, 0};
    }
    for (int i = 0; i < WL_NB; i++) {
        uint32_t qf = (uint32_t)(((uint64_t)q16 * WL_M5[i] + 32768u) >> 16);
        b[i].qf = qf < 16u ? 16u : qf;  /* step >= 1 */
    }
}

static void wl_fwd2(int16_t *img, int W, int H, int32_t *t) {
    int w = W, h = H;
    for (int l = 1; l <= WL_L; l++) {
        for (int y = 0; y < h; y++) wl_fwd1(img + (size_t)y * W, w, 1, t);
        for (int x = 0; x < w; x++) wl_fwd1(img + x, h, W, t);
        w = (w + 1) / 2; h = (h + 1) / 2;
    }
}
static void wl_inv2(int16_t *img, int W, int H, int32_t *t) {
    int ws[WL_L + 1], hs[WL_L + 1];
    ws[0] = W; hs[0] = H;
    for (int l = 1; l <= WL_L; l++) { ws[l] = (ws[l - 1] + 1) / 2; hs[l] = (hs[l - 1] + 1) / 2; }
    for (int l = WL_L; l >= 1; l--) {
        int w = ws[l - 1], h = hs[l - 1];
        for (int x = 0; x < w; x++) wl_inv1(img + x, h, W, t);
        for (int y = 0; y < h; y++) wl_inv1(img + (size_t)y * W, w, 1, t);
    }
}

/* ------------------------------------------------------------------ quantizer (integer) */
/* m = floor(|c| / q + rq / 256) with q = qf / 16 */
static int32_t wl_quant(int32_t c, uint32_t qf) {
    uint32_t a = (uint32_t)(c < 0 ? -c : c);
    uint32_t m = (a * 4096u + WL_RQ * qf) / (qf * 256u);
    return c < 0 ? -(int32_t)m : (int32_t)m;
}
/* round((|i| + 0.5 - rq / 256) * q) */
static int32_t wl_dequant(int32_t i, uint32_t qf) {
    if (!i) return 0;
    uint32_t a = (uint32_t)(i < 0 ? -i : i);
    uint64_t v = (uint64_t)(512u * a + 256u - 2u * WL_RQ) * qf;
    int32_t m = (int32_t)((v + 4096u) >> 13);
    return i < 0 ? -m : m;
}

/* ------------------------------------------------------------------ entropy coder */
#define WL_QBPP 20
#define WL_QMAX (32 - WL_QBPP - 1)
static const uint8_t WL_J[32] = {0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,5,5,6,6,7,7,8,9,10,11,12,13,14,15};
typedef struct { uint32_t A, N; } WlCtx;
static void wl_ctx_init(WlCtx *c) { c->A = 4; c->N = 1; }
static int wl_k(const WlCtx *c) { int k = 0; while ((c->N << k) < c->A && k < 24) k++; return k; }
static void wl_upd(WlCtx *c, uint32_t m) { c->A += m; if (++c->N >= 64) { c->A >>= 1; c->N >>= 1; } }
static void wl_rice_put(WlBW *w, WlCtx *c, uint32_t m) {
    int k = wl_k(c);
    uint32_t q = m >> k;
    if (q < WL_QMAX) { wl_zeros(w, (int)q); wl_put(w, 1, 1); wl_put(w, m, k); }
    else { wl_zeros(w, WL_QMAX); wl_put(w, 1, 1); wl_put(w, m, WL_QBPP); }
    wl_upd(c, m);
}
static uint32_t wl_rice_get(WlBR *r, WlCtx *c) {
    int k = wl_k(c);
    uint32_t q = 0, m;
    while (!wl_bit(r)) { if (r->err) return 0; if (++q == WL_QMAX) break; }
    if (q == WL_QMAX) { wl_bit(r); m = wl_get(r, WL_QBPP); }
    else m = (q << k) | wl_get(r, k);
    wl_upd(c, m);
    return m;
}
static uint32_t wl_zz(int32_t v) { return v >= 0 ? (uint32_t)v << 1 : ((uint32_t)(-v) << 1) - 1; }
static int32_t wl_unzz(uint32_t m) { return (m & 1) ? -(int32_t)((m + 1) >> 1) : (int32_t)(m >> 1); }
static int32_t wl_abs(int32_t v) { return v < 0 ? -v : v; }

static int32_t wl_at(const int16_t *img, int W, const WlBand *b, int x, int y) {
    if (x < 0 || y < 0 || x >= b->w) return 0;
    return img[(size_t)(b->y0 + y) * W + b->x0 + x];
}
static int wl_nctx(int32_t a, int32_t bb) {
    int32_t s = wl_abs(a) + wl_abs(bb);
    return s == 0 ? 0 : s <= 2 ? 1 : s <= 6 ? 2 : 3;
}
static void wl_enc_band(WlBW *w, const int16_t *img, int W, const WlBand *b, int mode) {
    WlCtx c[4], cri; int RI = 0;
    for (int i = 0; i < 4; i++) wl_ctx_init(&c[i]);
    wl_ctx_init(&cri);
    for (int y = 0; y < b->h; y++) {
        for (int x = 0; x < b->w;) {
            int32_t a = wl_at(img, W, b, x - 1, y), bb = wl_at(img, W, b, x, y - 1),
                    cc = wl_at(img, W, b, x - 1, y - 1), d = wl_at(img, W, b, x + 1, y - 1);
            if (mode && !a && !bb && !cc && !d) {
                int r = 0;
                while (x + r < b->w && !wl_at(img, W, b, x + r, y)) r++;
                int rem = r;
                while (rem >= (1 << WL_J[RI])) { wl_put(w, 1, 1); rem -= 1 << WL_J[RI]; if (RI < 31) RI++; }
                if (x + r == b->w) { if (rem > 0) wl_put(w, 1, 1); x += r; continue; }
                wl_put(w, 0, 1); wl_put(w, (uint32_t)rem, WL_J[RI]);
                int32_t v = wl_at(img, W, b, x + r, y);
                wl_rice_put(w, &cri, (uint32_t)wl_abs(v) - 1); wl_put(w, v < 0, 1);
                if (RI > 0) RI--;
                x += r + 1;
                continue;
            }
            wl_rice_put(w, mode ? &c[wl_nctx(a, bb)] : &c[0], wl_zz(wl_at(img, W, b, x, y)));
            x++;
        }
    }
}
static void wl_dec_band(WlBR *r, int16_t *img, int W, const WlBand *b, int mode) {
    WlCtx c[4], cri; int RI = 0;
    for (int i = 0; i < 4; i++) wl_ctx_init(&c[i]);
    wl_ctx_init(&cri);
#define WL_SET(xx, yy, v) img[(size_t)(b->y0 + (yy)) * W + b->x0 + (xx)] = (int16_t)(v)
    for (int y = 0; y < b->h; y++) {
        for (int x = 0; x < b->w;) {
            int32_t a = wl_at(img, W, b, x - 1, y), bb = wl_at(img, W, b, x, y - 1),
                    cc = wl_at(img, W, b, x - 1, y - 1), d = wl_at(img, W, b, x + 1, y - 1);
            if (mode && !a && !bb && !cc && !d) {
                for (;;) {
                    if (x == b->w) break;
                    if (wl_bit(r)) {
                        int n = 1 << WL_J[RI];
                        if (x + n <= b->w) { for (int k = 0; k < n; k++) WL_SET(x + k, y, 0); x += n; if (RI < 31) RI++; }
                        else { while (x < b->w) { WL_SET(x, y, 0); x++; } }
                    } else {
                        int rem = (int)wl_get(r, WL_J[RI]);
                        for (int k = 0; k < rem; k++) WL_SET(x + k, y, 0);
                        x += rem;
                        int32_t m = (int32_t)wl_rice_get(r, &cri) + 1;
                        WL_SET(x, y, wl_bit(r) ? -m : m);
                        if (RI > 0) RI--;
                        x++;
                        break;
                    }
                    if (r->err) return;
                }
                continue;
            }
            WL_SET(x, y, wl_unzz(wl_rice_get(r, mode ? &c[wl_nctx(a, bb)] : &c[0])));
            x++;
            if (r->err) return;
        }
    }
#undef WL_SET
}
static int32_t wl_med(int32_t a, int32_t b, int32_t c) {
    int32_t mx = a > b ? a : b, mn = a > b ? b : a;
    return c >= mx ? mn : c <= mn ? mx : a + b - c;
}
static int32_t wl_llpred(const int16_t *img, int W, int x, int y) {
    if (!x && !y) return 0;
    if (!y) return img[x - 1];
    if (!x) return img[(size_t)(y - 1) * W];
    return wl_med(img[(size_t)y * W + x - 1], img[(size_t)(y - 1) * W + x],
                  img[(size_t)(y - 1) * W + x - 1]);
}

/* ------------------------------------------------------------------ API */
/* Encode a W x H plane held in `img` (samples 0..4095; overwritten with quantizer indices).
 * t: int32[2 * max(W, H)]. Returns the stream length, or 0 if `cap` was too small. */
static size_t wl53_encode(int16_t *img, int W, int H, uint32_t q16, int mode, int32_t *t,
                          uint8_t *out, size_t cap) {
    WlBand b[WL_NB];
    WlBW w = {out, 0, cap, 0, 0, 0};
    wl_fwd2(img, W, H, t);
    wl_bands(W, H, q16, b);
    for (int i = 0; i < WL_NB; i++)
        for (int y = 0; y < b[i].h; y++)
            for (int x = 0; x < b[i].w; x++) {
                int16_t *p = &img[(size_t)(b[i].y0 + y) * W + b[i].x0 + x];
                *p = (int16_t)wl_quant(*p, b[i].qf);
            }
    uint8_t hdr[WL_HDR] = {'W', WL_VERSION, WL_L, (uint8_t)mode, (uint8_t)(q16 & 0xFF),
                           (uint8_t)((q16 >> 8) & 0xFF), (uint8_t)((q16 >> 16) & 0xFF),
                           (uint8_t)(q16 >> 24), WL_RQ};
    for (int i = 0; i < WL_HDR; i++) wl_put(&w, hdr[i], 8);
    WlCtx cll; wl_ctx_init(&cll);
    for (int y = 0; y < b[0].h; y++)
        for (int x = 0; x < b[0].w; x++)
            wl_rice_put(&w, &cll, wl_zz(img[(size_t)y * W + x] - wl_llpred(img, W, x, y)));
    for (int i = 1; i < WL_NB; i++) wl_enc_band(&w, img, W, &b[i], mode);
    wl_pad(&w);
    return w.err ? 0 : w.pos;
}

#ifndef WL53_NO_DECODE
/* Decode into img (reconstructed samples). Returns 0, or 1 on a bad / truncated stream. */
static int wl53_decode(const uint8_t *in, size_t len, int W, int H, int16_t *img, int32_t *t) {
    if (len < WL_HDR || in[0] != 'W' || in[1] != WL_VERSION || in[2] != WL_L || in[8] != WL_RQ)
        return 1;
    int mode = in[3];
    uint32_t q16 = in[4] | (uint32_t)in[5] << 8 | (uint32_t)in[6] << 16 | (uint32_t)in[7] << 24;
    WlBand b[WL_NB];
    wl_bands(W, H, q16, b);
    WlBR r = {in + WL_HDR, len - WL_HDR, 0, 0, 0, 0};
    WlCtx cll; wl_ctx_init(&cll);
    for (int y = 0; y < b[0].h; y++)
        for (int x = 0; x < b[0].w; x++)
            img[(size_t)y * W + x] = (int16_t)(wl_llpred(img, W, x, y) + wl_unzz(wl_rice_get(&r, &cll)));
    for (int i = 1; i < WL_NB; i++) wl_dec_band(&r, img, W, &b[i], mode);
    if (r.err) return 1;
    for (int i = 0; i < WL_NB; i++)
        for (int y = 0; y < b[i].h; y++)
            for (int x = 0; x < b[i].w; x++) {
                int16_t *p = &img[(size_t)(b[i].y0 + y) * W + b[i].x0 + x];
                *p = (int16_t)wl_dequant(*p, b[i].qf);
            }
    wl_inv2(img, W, H, t);
    return 0;
}
#endif /* WL53_NO_DECODE */

#endif /* WL53_CORE_H */
