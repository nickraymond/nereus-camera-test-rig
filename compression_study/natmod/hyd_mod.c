/*
 * nrhyd — hydrium (vendored + study patch, methods/hydrium/) as a MicroPython native module:
 * one Bayer plane of an 8-bit mosaic → a JPEG XL stream, linear light (methods/hyd_plane.h,
 * the same code as the Mac / Pi CLI methods/hyd.c, so the bytes are identical).
 *
 *   import nrhyd
 *   n, peak = nrhyd.encode(frame, stride, offset, w, h, black, white, hf, gs, lf, out)
 *
 * Plane sample (y, x) = frame[offset + y*stride + 2*x] (plane stride = 2 * frame width).
 * Linear light = (v - black) / (white - black). hf / gs / lf = HF multiplier, globalScale, LF
 * divisor. ``out`` is a writable buffer; returns the stream length and hydrium's peak heap
 * bytes. All of hydrium's memory comes from the MicroPython heap (m_realloc / m_free) and is
 * freed before returning; an allocation failure raises MemoryError (the GC reclaims the rest).
 * Uses single-precision floats (the M55 FPU); built with -ffp-contract=off like the CLI.
 */
#include "py/dynruntime.h"

#include "../methods/hyd_plane.h"

size_t hyd_mem_peak(void);
void hyd_mem_reset_peak(void);

void *hyd_host_realloc(void *ptr, size_t n) {
    return m_realloc(ptr, n);
}

void hyd_host_free(void *ptr) {
    m_free(ptr);
}

static mp_obj_t encode(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t src, dst;
    mp_get_buffer_raise(args[0], &src, MP_BUFFER_READ);
    mp_int_t stride = mp_obj_get_int(args[1]);
    mp_int_t offset = mp_obj_get_int(args[2]);
    mp_int_t w = mp_obj_get_int(args[3]);
    mp_int_t h = mp_obj_get_int(args[4]);
    mp_int_t black = mp_obj_get_int(args[5]);
    mp_int_t white = mp_obj_get_int(args[6]);
    mp_int_t hf = mp_obj_get_int(args[7]);
    mp_int_t gs = mp_obj_get_int(args[8]);
    mp_int_t lf = mp_obj_get_int(args[9]);
    mp_get_buffer_raise(args[10], &dst, MP_BUFFER_WRITE);
    if (w < 1 || h < 1 || offset < 0 || stride < 2 * w
        || offset + (h - 1) * stride + 2 * (w - 1) >= (mp_int_t)src.len) {
        mp_raise_ValueError(MP_ERROR_TEXT("bad plane geometry"));
    }
    hyd_mem_reset_peak();
    const char *err;
    size_t n = hyd_plane_encode((const uint8_t *)src.buf + offset, 1, stride, 2, (uint32_t)w,
                                (uint32_t)h, 256, (int32_t)black, (int32_t)white, (uint32_t)hf,
                                (uint32_t)gs, (uint32_t)lf, (uint8_t *)dst.buf, dst.len, &err);
    if (!n) {
        mp_raise_ValueError(err);
    }
    mp_obj_t res[2] = {mp_obj_new_int((mp_int_t)n), mp_obj_new_int((mp_int_t)hyd_mem_peak())};
    return mp_obj_new_tuple(2, res);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(encode_obj, 11, 11, encode);

mp_obj_t mpy_init(mp_obj_fun_bc_t *self, size_t n_args, size_t n_kw, mp_obj_t *args) {
    MP_DYNRUNTIME_INIT_ENTRY
    mp_store_global(MP_QSTR_encode, MP_OBJ_FROM_PTR(&encode_obj));
    MP_DYNRUNTIME_INIT_EXIT
}
