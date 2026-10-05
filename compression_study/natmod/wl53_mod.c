/*
 * nrwl53 — the wl53 v1 lossy plane codec (methods/wl53_core.h) as a MicroPython native module.
 *
 *   import nrwl53
 *   n = nrwl53.encode(frame, stride, offset, w, h, lut, q16, out)
 *
 * Codes the plane whose sample (y, x) is lut[frame[offset + y*stride + 2*x]] — one Bayer phase
 * of an 8-bit mosaic (plane stride = 2 * frame width), mapped through ``lut`` (256 uint16 LE
 * entries, e.g. the study's sqrt curve to 12 bits). ``q16`` = quantizer scale * 16. ``out`` is
 * a writable buffer; returns the byte count. Working memory from the MicroPython heap: the
 * plane as int16 (2 * w * h bytes) and a 2 * max(w, h) int32 line buffer.
 */
#include "py/dynruntime.h"

#define WL53_NO_DECODE
#include "../methods/wl53_core.h"

static mp_obj_t encode(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t src, lut, dst;
    mp_get_buffer_raise(args[0], &src, MP_BUFFER_READ);
    mp_int_t stride = mp_obj_get_int(args[1]);
    mp_int_t offset = mp_obj_get_int(args[2]);
    mp_int_t w = mp_obj_get_int(args[3]);
    mp_int_t h = mp_obj_get_int(args[4]);
    mp_get_buffer_raise(args[5], &lut, MP_BUFFER_READ);
    mp_int_t q16 = mp_obj_get_int(args[6]);
    mp_get_buffer_raise(args[7], &dst, MP_BUFFER_WRITE);
    if (w < 2 || h < 2 || q16 < 1 || lut.len < 512
        || offset + (h - 1) * stride + 2 * (w - 1) >= (mp_int_t)src.len) {
        mp_raise_ValueError(MP_ERROR_TEXT("bad plane geometry, lut or q16"));
    }
    const uint8_t *s = (const uint8_t *)src.buf, *l = (const uint8_t *)lut.buf;
    int16_t *img = (int16_t *)m_malloc(2 * w * h);
    int32_t *t = (int32_t *)m_malloc(4 * 2 * (w > h ? w : h));
    for (mp_int_t y = 0; y < h; y++) {
        const uint8_t *r = s + offset + y * stride;
        int16_t *o = img + y * w;
        for (mp_int_t x = 0; x < w; x++) {
            uint8_t v = r[2 * x];
            o[x] = (int16_t)(l[2 * v] | (l[2 * v + 1] << 8));
        }
    }
    size_t n = wl53_encode(img, (int)w, (int)h, (uint32_t)q16, 1, t, (uint8_t *)dst.buf, dst.len);
    m_free(t);
    m_free(img);
    if (!n) {
        mp_raise_ValueError(MP_ERROR_TEXT("output buffer too small"));
    }
    return mp_obj_new_int((mp_int_t)n);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(encode_obj, 8, 8, encode);

mp_obj_t mpy_init(mp_obj_fun_bc_t *self, size_t n_args, size_t n_kw, mp_obj_t *args) {
    MP_DYNRUNTIME_INIT_ENTRY
    mp_store_global(MP_QSTR_encode, MP_OBJ_FROM_PTR(&encode_obj));
    MP_DYNRUNTIME_INIT_EXIT
}
