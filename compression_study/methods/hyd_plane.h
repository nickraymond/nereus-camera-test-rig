/*
 * hyd_plane — one grey Bayer plane → a JPEG XL stream with the vendored, study-patched hydrium
 * (methods/hydrium/). Shared by the Mac / Pi CLI (hyd.c) and the OpenMV native module
 * (natmod/hyd_mod.c), so every device writes the same bytes for the same plane and knobs.
 *
 * The plane is linear light: code i → (i - black) / (white - black), through hydrium's float
 * XYB path via a per-code table (hyd_study_grey_lut). 256×256 tiles, each its own frame, so the
 * working memory does not grow with the plane size. Knobs: HF multiplier, globalScale, LF
 * divisor (hyd_study_set_params; see the desk study, mcu/README.md).
 *
 * Bit-exact across devices needs IEEE single precision without fused multiply-add: build with
 * -ffp-contract=off and without -ffast-math everywhere.
 */
#ifndef HYD_PLANE_H
#define HYD_PLANE_H

#include <stddef.h>
#include <stdint.h>

#include "hydrium/libhydrium/libhydrium.h"

#define HYD_PLANE_TILE 256

/*
 * Samples: sample_bytes 1 (uint8) or 2 (uint16, native endian); strides in samples. Returns the
 * stream length, or 0 with *err set (bad arguments, out of memory, output buffer too small) and
 * *where = hydrium status code * 1000 - step * 100 - tile index (step: 1 setup, 2 send tile,
 * 3 flush, 4 output buffer).
 */
static size_t hyd_plane_encode(const void *src, int sample_bytes, ptrdiff_t row_stride,
                               ptrdiff_t pixel_stride, uint32_t w, uint32_t h, uint32_t nlut,
                               int32_t black, int32_t white, uint32_t hf_mult,
                               uint32_t global_scale, uint32_t lf_divisor, uint8_t *out,
                               size_t cap, const char **err, int32_t *where) {
    size_t total = 0;
    int step = 1, tile = 0;
    *err = NULL;
    *where = 0;
    if (cap < 64) {
        *err = "output buffer too small";
        return 0;
    }
    HYDEncoder *e = hyd_encoder_new();
    if (!e) {
        *err = "out of memory";
        return 0;
    }
    HYDImageMetadata md = {0};
    md.width = w;
    md.height = h;
    md.linear_light = 1;
    md.tile_size_shift_x = md.tile_size_shift_y = 0;  /* 256×256 tiles */
    HYDStatusCode r = hyd_set_metadata(e, &md);
    if (r >= HYD_ERROR_START)
        r = hyd_study_set_params(e, hf_mult, global_scale, lf_divisor);
    if (r >= HYD_ERROR_START)
        r = hyd_study_grey_lut(e, nlut, black, white);
    if (r >= HYD_ERROR_START)
        r = hyd_provide_output_buffer(e, out, cap);
    const uint32_t tw = (w + HYD_PLANE_TILE - 1) / HYD_PLANE_TILE;
    const uint32_t th = (h + HYD_PLANE_TILE - 1) / HYD_PLANE_TILE;
    for (uint32_t ty = 0; ty < th && r >= HYD_ERROR_START; ty++) {
        for (uint32_t tx = 0; tx < tw && r >= HYD_ERROR_START; tx++) {
            const uint8_t *p = (const uint8_t *)src
                + ((size_t)ty * HYD_PLANE_TILE * row_stride
                   + (size_t)tx * HYD_PLANE_TILE * pixel_stride) * sample_bytes;
            tile = (int)(ty * tw + tx);
            step = 2;
            r = hyd_study_send_grey_tile(e, p, sample_bytes, tx, ty, row_stride, pixel_stride,
                                         ty == th - 1 && tx == tw - 1);
            if (r < HYD_ERROR_START)
                break;
            step = 3;
            r = hyd_flush(e);
            size_t wr = 0;
            HYDStatusCode rel = hyd_release_output_buffer(e, &wr);
            total += wr;
            if (r == HYD_NEED_MORE_OUTPUT || rel == HYD_NEED_MORE_OUTPUT || cap - total < 64) {
                r = HYD_NEED_MORE_OUTPUT;
                break;
            }
            step = 4;
            if (r >= HYD_ERROR_START)
                r = hyd_provide_output_buffer(e, out + total, cap - total);
        }
    }
    if (r == HYD_NEED_MORE_OUTPUT || r < HYD_ERROR_START)
        *where = (int32_t)r * 1000 - step * 100 - (tile < 99 ? tile : 99);
    if (r == HYD_NEED_MORE_OUTPUT) {
        *err = "output buffer too small";
    } else if (r < HYD_ERROR_START) {
        const char *m = hyd_error_message_get(e);
        *err = m ? m : (r == HYD_NOMEM ? "out of memory" : "hydrium error");
    }
    hyd_encoder_destroy(e);
    return *err ? 0 : total;
}

#endif /* HYD_PLANE_H */
