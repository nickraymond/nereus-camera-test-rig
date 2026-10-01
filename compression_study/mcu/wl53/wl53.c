/*
 * wl53: prototype MCU-portable lossy plane codec (desk study, not shipped code).
 *
 *   integer LeGall 5/3 lifting DWT (JPEG 2000 reversible filter), L levels, Mallat layout
 *   -> per-subband uniform dead-zone quantizer, step q_b = Q / g_b (g_b = L2 norm of the
 *      subband's synthesis basis, so every subband adds the same MSE per unit of Q)
 *   -> LL: MED prediction; detail subbands: raster order, JPEG-LS-style coder =
 *      adaptive Golomb-Rice (the packer's A/N/k rule, 4 contexts from |left|+|above|)
 *      + run mode for zero runs (JPEG-LS J[] table, run-interruption context).
 *   mode 0 disables the run mode and contexts (packer-like coder: >= 1 bit / coefficient).
 *
 * libc + libm only. Working memory: the plane as int32 (int16 is enough for <= 12-bit input,
 * 5 levels) + one line buffer + the output buffer. No threads, no float in the coder.
 *
 * CLI (planes are uint16 little-endian, row-major):
 *   wl53 enc W H LEVELS Q RND MODE [TILT [check]] < plane.raw > out.bin
 *   wl53 dec W H < in.bin > recon_i32le.raw
 * enc ... check: also decodes its own output and exits 2 unless it equals the encoder's
 * own reconstruction.
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------ bits */
typedef struct { uint8_t *buf; size_t pos, cap; uint64_t acc; int nbits; } BW;
static void bw_put(BW *w, uint32_t v, int n) {
    if (n == 0) return;
    w->acc = (w->acc << n) | (v & ((n == 32) ? 0xffffffffu : ((1u << n) - 1u)));
    w->nbits += n;
    while (w->nbits >= 8) {
        w->nbits -= 8;
        if (w->pos >= w->cap) { fprintf(stderr, "output overflow\n"); exit(3); }
        w->buf[w->pos++] = (uint8_t)(w->acc >> w->nbits);
    }
}
static void bw_zeros(BW *w, int n) { while (n > 24) { bw_put(w, 0, 24); n -= 24; } bw_put(w, 0, n); }
static void bw_pad(BW *w) { if (w->nbits) bw_put(w, 0, 8 - w->nbits); }

typedef struct { const uint8_t *buf; size_t len, pos; uint32_t acc; int nbits; int err; } BR;
static int br_bit(BR *r) {
    if (!r->nbits) {
        if (r->pos >= r->len) { r->err = 1; return 0; }
        r->acc = r->buf[r->pos++]; r->nbits = 8;
    }
    r->nbits--;
    return (r->acc >> r->nbits) & 1;
}
static uint32_t br_get(BR *r, int n) { uint32_t v = 0; while (n--) v = (v << 1) | br_bit(r); return v; }

/* ------------------------------------------------------------------ 5/3 lifting */
static void fwd1(int32_t *x, int n, int s, int32_t *t) {
    if (n < 2) return;
    int nl = (n + 1) / 2, nh = n / 2;
    for (int i = 0; i < n; i++) t[i] = x[i * s];
    int32_t *d = t + n;  /* nh highs */
    for (int i = 0; i < nh; i++) {
        int32_t a = t[2 * i], b = (2 * i + 2 < n) ? t[2 * i + 2] : t[2 * i];
        d[i] = t[2 * i + 1] - ((a + b) >> 1);
    }
    for (int i = 0; i < nl; i++) {
        int32_t dl = i > 0 ? d[i - 1] : d[0], dr = i < nh ? d[i] : d[nh - 1];
        x[i * s] = t[2 * i] + ((dl + dr + 2) >> 2);
    }
    for (int i = 0; i < nh; i++) x[(nl + i) * s] = d[i];
}
static void inv1(int32_t *x, int n, int s, int32_t *t) {
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
    for (int i = 0; i < n; i++) x[i * s] = t[i];
}
/* float twin of inv1 (no rounding) for the synthesis gains */
static void inv1f(double *x, int n, int s, double *t) {
    if (n < 2) return;
    int nl = (n + 1) / 2, nh = n / 2;
    double *d = t + n;
    for (int i = 0; i < nh; i++) d[i] = x[(nl + i) * s];
    for (int i = 0; i < nl; i++) {
        double dl = i > 0 ? d[i - 1] : d[0], dr = i < nh ? d[i] : d[nh - 1];
        t[2 * i] = x[i * s] - (dl + dr) / 4.0;
    }
    for (int i = 0; i < nh; i++) {
        double a = t[2 * i], b = (2 * i + 2 < n) ? t[2 * i + 2] : t[2 * i];
        t[2 * i + 1] = d[i] + (a + b) / 2.0;
    }
    for (int i = 0; i < n; i++) x[i * s] = t[i];
}

#define MAXL 8
typedef struct { int x0, y0, w, h; double q; } Band;  /* q = quantizer step */

/* bands in coding order: LL, then level L..1 each HL, LH, HH */
static int make_bands(int W, int H, int L, Band *b) {
    int ws[MAXL + 1], hs[MAXL + 1];
    ws[0] = W; hs[0] = H;
    for (int l = 1; l <= L; l++) { ws[l] = (ws[l - 1] + 1) / 2; hs[l] = (hs[l - 1] + 1) / 2; }
    int nb = 0;
    b[nb++] = (Band){0, 0, ws[L], hs[L], 0};
    for (int l = L; l >= 1; l--) {
        int lw = ws[l], lh = hs[l], fw = ws[l - 1], fh = hs[l - 1];
        b[nb++] = (Band){lw, 0, fw - lw, lh, 0};   /* HL */
        b[nb++] = (Band){0, lh, lw, fh - lh, 0};   /* LH */
        b[nb++] = (Band){lw, lh, fw - lw, fh - lh, 0}; /* HH */
    }
    return nb;
}

static void inv2f(double *img, int W, int H, int L, double *t) {
    int ws[MAXL + 1], hs[MAXL + 1];
    ws[0] = W; hs[0] = H;
    for (int l = 1; l <= L; l++) { ws[l] = (ws[l - 1] + 1) / 2; hs[l] = (hs[l - 1] + 1) / 2; }
    for (int l = L; l >= 1; l--) {
        int w = ws[l - 1], h = hs[l - 1];
        for (int x = 0; x < w; x++) inv1f(img + x, h, W, t);
        for (int y = 0; y < h; y++) inv1f(img + (size_t)y * W, w, 1, t);
    }
}

/* L2 norm of each band's synthesis basis (impulse at the band centre of a 2^(L+5) square) */
#ifdef WL53_FIXED_GAINS
#include "gains5.h"  /* table generated by `wl53 gains 5`, L = 5 only (timing build) */
static void band_gains(int L, double *g) { (void)L; memcpy(g, GAINS5, sizeof(GAINS5)); }
#else
static void band_gains(int L, double *g) {
    int S = 1 << (L + 5);
    Band b[3 * MAXL + 1];
    int nb = make_bands(S, S, L, b);
    double *img = malloc(sizeof(double) * S * S), *t = malloc(sizeof(double) * 2 * S);
    for (int i = 0; i < nb; i++) {
        memset(img, 0, sizeof(double) * S * S);
        img[(size_t)(b[i].y0 + b[i].h / 2) * S + b[i].x0 + b[i].w / 2] = 1.0;
        inv2f(img, S, S, L, t);
        double e = 0; for (int k = 0; k < S * S; k++) e += img[k] * img[k];
        g[i] = sqrt(e);
    }
    free(img); free(t);
}
#endif

static void fwd2(int32_t *img, int W, int H, int L, int32_t *t) {
    int w = W, h = H;
    for (int l = 1; l <= L; l++) {
        for (int y = 0; y < h; y++) fwd1(img + (size_t)y * W, w, 1, t);
        for (int x = 0; x < w; x++) fwd1(img + x, h, W, t);
        w = (w + 1) / 2; h = (h + 1) / 2;
    }
}
static void inv2(int32_t *img, int W, int H, int L, int32_t *t) {
    int ws[MAXL + 1], hs[MAXL + 1];
    ws[0] = W; hs[0] = H;
    for (int l = 1; l <= L; l++) { ws[l] = (ws[l - 1] + 1) / 2; hs[l] = (hs[l - 1] + 1) / 2; }
    for (int l = L; l >= 1; l--) {
        int w = ws[l - 1], h = hs[l - 1];
        for (int x = 0; x < w; x++) inv1(img + x, h, W, t);
        for (int y = 0; y < h; y++) inv1(img + (size_t)y * W, w, 1, t);
    }
}

/* ------------------------------------------------------------------ entropy coder */
#define LIMIT 32
#define QBPP 20
#define QMAX (LIMIT - QBPP - 1)
static const int J[32] = {0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,5,5,6,6,7,7,8,9,10,11,12,13,14,15};
typedef struct { uint32_t A, N; } Ctx;
static void ctx_init(Ctx *c) { c->A = 4; c->N = 1; }
static int ctx_k(const Ctx *c) { int k = 0; while ((c->N << k) < c->A && k < 24) k++; return k; }
static void ctx_upd(Ctx *c, uint32_t m) { c->A += m; if (++c->N >= 64) { c->A >>= 1; c->N >>= 1; } }

static void rice_put(BW *w, Ctx *c, uint32_t m) {
    int k = ctx_k(c);
    uint32_t q = m >> k;
    if (q < QMAX) { bw_zeros(w, (int)q); bw_put(w, 1, 1); bw_put(w, m, k); }
    else { bw_zeros(w, QMAX); bw_put(w, 1, 1); bw_put(w, m, QBPP); }
    ctx_upd(c, m);
}
static uint32_t rice_get(BR *r, Ctx *c) {
    int k = ctx_k(c);
    uint32_t q = 0, m;
    while (!br_bit(r)) { if (r->err) return 0; if (++q == QMAX) break; }
    if (q == QMAX) { br_bit(r); m = br_get(r, QBPP); }   /* consume the terminating 1 */
    else m = (q << k) | br_get(r, k);
    ctx_upd(c, m);
    return m;
}
static uint32_t zz(int32_t v) { return v >= 0 ? (uint32_t)v << 1 : ((uint32_t)(-v) << 1) - 1; }
static int32_t unzz(uint32_t m) { return (m & 1) ? -(int32_t)((m + 1) >> 1) : (int32_t)(m >> 1); }

static inline int32_t at(const int32_t *img, int W, const Band *b, int x, int y) {
    if (x < 0 || y < 0 || x >= b->w) return 0;
    return img[(size_t)(b->y0 + y) * W + b->x0 + x];
}
static int nctx(int32_t a, int32_t bb) {
    int32_t s = abs(a) + abs(bb);
    return s == 0 ? 0 : s <= 2 ? 1 : s <= 6 ? 2 : 3;
}

/* code one detail band of quantizer indices (stored in img) */
static void enc_band(BW *w, const int32_t *img, int W, const Band *b, int mode) {
    Ctx c[4], cri; int RI = 0;
    for (int i = 0; i < 4; i++) ctx_init(&c[i]);
    ctx_init(&cri);
    for (int y = 0; y < b->h; y++) {
        for (int x = 0; x < b->w;) {
            int32_t a = at(img, W, b, x - 1, y), bb = at(img, W, b, x, y - 1),
                    cc = at(img, W, b, x - 1, y - 1), d = at(img, W, b, x + 1, y - 1);
            if (mode && !a && !bb && !cc && !d) {
                int r = 0;
                while (x + r < b->w && !at(img, W, b, x + r, y)) r++;
                int rem = r;
                while (rem >= (1 << J[RI])) { bw_put(w, 1, 1); rem -= 1 << J[RI]; if (RI < 31) RI++; }
                if (x + r == b->w) { if (rem > 0) bw_put(w, 1, 1); x += r; continue; }
                bw_put(w, 0, 1); bw_put(w, (uint32_t)rem, J[RI]);
                int32_t v = at(img, W, b, x + r, y);
                rice_put(w, &cri, (uint32_t)abs(v) - 1); bw_put(w, v < 0, 1);
                if (RI > 0) RI--;
                x += r + 1;
                continue;
            }
            int32_t v = at(img, W, b, x, y);
            rice_put(w, mode ? &c[nctx(a, bb)] : &c[0], zz(v));
            x++;
        }
    }
}
static void dec_band(BR *r, int32_t *img, int W, const Band *b, int mode) {
    Ctx c[4], cri; int RI = 0;
    for (int i = 0; i < 4; i++) ctx_init(&c[i]);
    ctx_init(&cri);
#define SET(xx, yy, v) img[(size_t)(b->y0 + (yy)) * W + b->x0 + (xx)] = (v)
    for (int y = 0; y < b->h; y++) {
        for (int x = 0; x < b->w;) {
            int32_t a = at(img, W, b, x - 1, y), bb = at(img, W, b, x, y - 1),
                    cc = at(img, W, b, x - 1, y - 1), d = at(img, W, b, x + 1, y - 1);
            if (mode && !a && !bb && !cc && !d) {
                for (;;) {
                    if (x == b->w) break;
                    if (br_bit(r)) {
                        int n = 1 << J[RI];
                        if (x + n <= b->w) { for (int k = 0; k < n; k++) SET(x + k, y, 0); x += n; if (RI < 31) RI++; }
                        else { while (x < b->w) { SET(x, y, 0); x++; } }
                    } else {
                        int rem = (int)br_get(r, J[RI]);
                        for (int k = 0; k < rem; k++) SET(x + k, y, 0);
                        x += rem;
                        int32_t m = (int32_t)rice_get(r, &cri) + 1;
                        SET(x, y, br_bit(r) ? -m : m);
                        if (RI > 0) RI--;
                        x++;
                        break;
                    }
                    if (r->err) return;
                }
                continue;
            }
            SET(x, y, unzz(rice_get(r, mode ? &c[nctx(a, bb)] : &c[0])));
            x++;
            if (r->err) return;
        }
    }
#undef SET
}
static int32_t med(int32_t a, int32_t b, int32_t c) {
    int32_t mx = a > b ? a : b, mn = a > b ? b : a;
    return c >= mx ? mn : c <= mn ? mx : a + b - c;
}
static int32_t ll_pred(const int32_t *img, int W, int x, int y) {
    if (!x && !y) return 0;
    if (!y) return img[x - 1];
    if (!x) return img[(size_t)(y - 1) * W];
    return med(img[(size_t)y * W + x - 1], img[(size_t)(y - 1) * W + x], img[(size_t)(y - 1) * W + x - 1]);
}

/* ------------------------------------------------------------------ quantizer */
static int32_t quant(int32_t c, double q, double rnd) {
    int32_t m = (int32_t)floor(fabs((double)c) / q + rnd);
    return c < 0 ? -m : m;
}
static int32_t dequant(int32_t i, double q, double rnd) {
    if (!i) return 0;
    double v = (abs(i) + 0.5 - rnd) * q;
    if (rnd >= 0.5) v = abs(i) * q;
    int32_t m = (int32_t)floor(v + 0.5);
    return i < 0 ? -m : m;
}

/* tilt < 1 gives coarser levels (and LL) finer steps: q *= tilt^(level - 1) */
static void set_steps(Band *b, int nb, int L, double Q, double tilt) {
    double g[3 * MAXL + 1];
    band_gains(L, g);
    for (int i = 0; i < nb; i++) {
        int lev = i == 0 ? L + 1 : L - (i - 1) / 3;
        b[i].q = Q / g[i] * pow(tilt, lev - 1);
        if (b[i].q < 1.0) b[i].q = 1.0;
    }
}

/* decode the stream into recon (int32 plane values) */
static int decode(const uint8_t *buf, size_t len, int W, int H, int32_t *img, int32_t *t) {
    if (len < 8) return 2;
    int L = buf[0], mode = buf[1];
    float Qf; memcpy(&Qf, buf + 2, 4);
    double rnd = buf[6] / 256.0, tilt = buf[7] / 128.0;
    Band b[3 * MAXL + 1];
    int nb = make_bands(W, H, L, b);
    set_steps(b, nb, L, Qf, tilt);
    BR r = {buf + 8, len - 8, 0, 0, 0, 0};
    Ctx cll; ctx_init(&cll);
    for (int y = 0; y < b[0].h; y++)
        for (int x = 0; x < b[0].w; x++)
            img[(size_t)y * W + x] = ll_pred(img, W, x, y) + unzz(rice_get(&r, &cll));
    for (int i = 1; i < nb; i++) dec_band(&r, img, W, &b[i], mode);
    if (r.err) return 2;
    for (int i = 0; i < nb; i++)
        for (int y = 0; y < b[i].h; y++)
            for (int x = 0; x < b[i].w; x++) {
                int32_t *p = &img[(size_t)(b[i].y0 + y) * W + b[i].x0 + x];
                *p = dequant(*p, b[i].q, rnd);
            }
    inv2(img, W, H, L, t);
    return 0;
}

static uint8_t *read_all(FILE *f, size_t *n) {
    size_t cap = 1 << 20, len = 0; uint8_t *buf = malloc(cap);
    for (;;) {
        if (len == cap) buf = realloc(buf, cap *= 2);
        size_t got = fread(buf + len, 1, cap - len, f);
        if (!got) break;
        len += got;
    }
    *n = len; return buf;
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: see source\n"); return 1; }
    int W = atoi(argv[2]), H = argc > 3 ? atoi(argv[3]) : 1;
    int32_t *img = malloc(sizeof(int32_t) * (size_t)W * H);
    int32_t *t = malloc(sizeof(int32_t) * 2 * (size_t)(W > H ? W : H));
    if (!strcmp(argv[1], "enc")) {
        if (argc < 8) return 1;
        int L = atoi(argv[4]), mode = atoi(argv[7]);
        double Q = atof(argv[5]), rnd = atof(argv[6]);
        double tilt = argc > 8 ? atof(argv[8]) : 1.0;
        int check = argc > 9 && !strcmp(argv[9], "check");
        uint8_t tq = (uint8_t)floor(tilt * 128 + 0.5);
        tilt = tq / 128.0;
        if (L < 0 || L > MAXL) return 1;
        size_t n; uint8_t *raw = read_all(stdin, &n);
        if (n != (size_t)W * H * 2) { fprintf(stderr, "bad input size %zu\n", n); return 2; }
        for (size_t i = 0; i < (size_t)W * H; i++) img[i] = raw[2 * i] | (raw[2 * i + 1] << 8);
        fwd2(img, W, H, L, t);
        Band b[3 * MAXL + 1];
        int nb = make_bands(W, H, L, b);
        float Qf = (float)Q;
        uint8_t rq = (uint8_t)floor(rnd * 256 + 0.5);
        rnd = rq / 256.0;
        set_steps(b, nb, L, Qf, tilt);
        for (int i = 0; i < nb; i++)
            for (int y = 0; y < b[i].h; y++)
                for (int x = 0; x < b[i].w; x++) {
                    int32_t *p = &img[(size_t)(b[i].y0 + y) * W + b[i].x0 + x];
                    *p = quant(*p, b[i].q, rnd);
                }
        BW w = {malloc((size_t)W * H * 4 + 64), 0, (size_t)W * H * 4 + 64, 0, 0};
        uint8_t hdr[8] = {(uint8_t)L, (uint8_t)mode, 0, 0, 0, 0, rq, tq};
        memcpy(hdr + 2, &Qf, 4);
        for (int i = 0; i < 8; i++) bw_put(&w, hdr[i], 8);
        Ctx cll; ctx_init(&cll);
        /* LL: MED on indices; iterate bottom-right to top-left is not needed: residuals use
           already-final neighbours, so code in raster order reading the index plane */
        for (int y = 0; y < b[0].h; y++)
            for (int x = 0; x < b[0].w; x++)
                rice_put(&w, &cll, zz(img[(size_t)y * W + x] - ll_pred(img, W, x, y)));
        for (int i = 1; i < nb; i++) enc_band(&w, img, W, &b[i], mode);
        bw_pad(&w);
        fwrite(w.buf, 1, w.pos, stdout);
        if (check) {
            int32_t *ref = malloc(sizeof(int32_t) * (size_t)W * H);
            memcpy(ref, img, sizeof(int32_t) * (size_t)W * H);
            for (int i = 0; i < nb; i++)
                for (int y = 0; y < b[i].h; y++)
                    for (int x = 0; x < b[i].w; x++) {
                        int32_t *p = &ref[(size_t)(b[i].y0 + y) * W + b[i].x0 + x];
                        *p = dequant(*p, b[i].q, rnd);
                    }
            inv2(ref, W, H, L, t);
            int32_t *dec = malloc(sizeof(int32_t) * (size_t)W * H);
            if (decode(w.buf, w.pos, W, H, dec, t) || memcmp(dec, ref, sizeof(int32_t) * (size_t)W * H)) {
                fprintf(stderr, "self-check FAILED\n"); return 2;
            }
        }
        return 0;
    }
    if (!strcmp(argv[1], "gains")) {
        double g[3 * MAXL + 1]; int L = atoi(argv[2]);
        band_gains(L, g);
        printf("static const double GAINS5[%d] = {", 3 * L + 1);
        for (int i = 0; i < 3 * L + 1; i++) printf("%.17g,", g[i]);
        printf("};\n");
        return 0;
    }
    if (!strcmp(argv[1], "dec")) {
        size_t n; uint8_t *buf = read_all(stdin, &n);
        if (decode(buf, n, W, H, img, t)) { fprintf(stderr, "corrupt stream\n"); return 2; }
        fwrite(img, sizeof(int32_t), (size_t)W * H, stdout);
        return 0;
    }
    return 1;
}
