/*
 * nrpack — the study's lossless Bayer-plane packer as a MicroPython dynamic native module.
 *
 * Plain C over methods/packer_core.h (the same core as the packer CLI, so the bytes are
 * identical); loaded at run time from a .mpy file (arch armv7emdp), no firmware rebuild.
 *
 *   import nrpack
 *   n = nrpack.encode(frame, stride, offset, w, h, b, out)   # plane: stride = 2 * W
 *
 * Codes the plane whose sample (y, x) is frame[offset + y*stride + 2*x] — one Bayer phase
 * read straight out of the 8-bit mosaic, so no separate plane split. ``out`` is a writable
 * buffer; returns the byte count. Raises ValueError if ``out`` could overflow or a sample is
 * >= 2^b. Working memory: two uint16 rows (4*w bytes) from the MicroPython heap.
 */
#include "py/dynruntime.h"

#define PACKER_NO_DECODE
#include "../methods/packer_core.h"

static mp_obj_t encode(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t src, dst;
    mp_get_buffer_raise(args[0], &src, MP_BUFFER_READ);
    mp_int_t stride = mp_obj_get_int(args[1]);
    mp_int_t offset = mp_obj_get_int(args[2]);
    mp_int_t w = mp_obj_get_int(args[3]);
    mp_int_t h = mp_obj_get_int(args[4]);
    mp_int_t b = mp_obj_get_int(args[5]);
    mp_get_buffer_raise(args[6], &dst, MP_BUFFER_WRITE);
    PackParams p;
    if (w < 1 || h < 1 || pack_params(&p, (int)b) != 0
        || offset + (h - 1) * stride + 2 * (w - 1) >= (mp_int_t)src.len) {
        mp_raise_ValueError(MP_ERROR_TEXT("bad plane geometry or bit depth"));
    }
    const uint8_t *s = (const uint8_t *)src.buf;
    uint16_t *rows = (uint16_t *)m_malloc(4 * w);
    uint16_t *prev = NULL, *cur = rows;
    size_t row_max = ((size_t)w * 64u + 7u) / 8u + 4u;  /* LIMIT <= 64 bits per sample */
    BitWriter bw = {0};
    bw.buf = (uint8_t *)dst.buf;
    for (mp_int_t y = 0; y < h; y++) {
        const uint8_t *r = s + offset + y * stride;
        for (mp_int_t x = 0; x < w; x++) {
            cur[x] = r[2 * x];
        }
        if (bw.pos + row_max > dst.len) {
            m_free(rows);
            mp_raise_ValueError(MP_ERROR_TEXT("output buffer too small"));
        }
        if (pack_encode_row(&p, prev, cur, (int)w, &bw) != 0) {
            m_free(rows);
            mp_raise_ValueError(MP_ERROR_TEXT("sample >= 2^b"));
        }
        prev = cur;
        cur = (cur == rows) ? rows + w : rows;
    }
    bw_pad(&bw);
    m_free(rows);
    return mp_obj_new_int((mp_int_t)bw.pos);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(encode_obj, 7, 7, encode);

mp_obj_t mpy_init(mp_obj_fun_bc_t *self, size_t n_args, size_t n_kw, mp_obj_t *args) {
    MP_DYNRUNTIME_INIT_ENTRY
    mp_store_global(MP_QSTR_encode, MP_OBJ_FROM_PTR(&encode_obj));
    MP_DYNRUNTIME_INIT_EXIT
}
