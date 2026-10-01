/* study driver: raw uint16 LE grey plane on stdin -> JPEG XL on stdout (grey replicated
   into R=G=B by pixel_stride tricks, no copy). argv: W H HF_MULT TILE_SHIFT LINEAR */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include "libhydrium/libhydrium.h"
extern uint16_t hyd_study_hf_mult;
extern uint32_t hyd_study_lf_f;
extern uint32_t hyd_study_gs;
int main(int argc, char **argv) {
    if (argc < 6) return 1;
    size_t W = atoi(argv[1]), H = atoi(argv[2]);
    hyd_study_hf_mult = (uint16_t)atoi(argv[3]);
    int shift = atoi(argv[4]), linear = atoi(argv[5]);
    if (argc > 6) hyd_study_lf_f = (uint32_t)atoi(argv[6]);
    if (argc > 7) hyd_study_gs = (uint32_t)atoi(argv[7]);
    /* LINEAR = 2: float32 linear-light input (exact path, no 16-bit LUT / polynomial) */
    size_t es = linear == 2 ? 4 : 2;
    uint8_t *img = malloc(W * H * es);
    if (fread(img, es, W * H, stdin) != W * H) return 2;
    HYDEncoder *e = hyd_encoder_new();
    HYDImageMetadata md = {0};
    md.width = W; md.height = H; md.linear_light = linear != 0;
    md.tile_size_shift_x = md.tile_size_shift_y = shift;
    if (hyd_set_metadata(e, &md) < HYD_ERROR_START) { fprintf(stderr, "%s\n", hyd_error_message_get(e)); return 3; }
    size_t bs = 1 << 20; uint8_t *ob = malloc(bs);
    hyd_provide_output_buffer(e, ob, bs);
    size_t ts = 256u << (shift < 0 ? 3 : shift);
    size_t tw = (W + ts - 1) / ts, th = (H + ts - 1) / ts;
    for (size_t ty = 0; ty < th; ty++)
        for (size_t tx = 0; tx < tw; tx++) {
            const uint8_t *p = img + (ty * ts * W + tx * ts) * es;
            const void *const rgb[3] = {p, p, p};
            HYDStatusCode r = hyd_send_tile(e, rgb, tx, ty, W, 1, ty == th - 1 && tx == tw - 1,
                                            es == 4 ? HYD_FLOAT32 : HYD_UINT16);
            if (r < HYD_ERROR_START) { fprintf(stderr, "send: %s\n", hyd_error_message_get(e)); return 3; }
            do {
                r = hyd_flush(e);
                size_t wr; hyd_release_output_buffer(e, &wr);
                fwrite(ob, 1, wr, stdout);
                hyd_provide_output_buffer(e, ob, bs);
            } while (r == HYD_NEED_MORE_OUTPUT);
            if (r < HYD_ERROR_START) { fprintf(stderr, "flush: %s\n", hyd_error_message_get(e)); return 3; }
        }
    hyd_encoder_destroy(e);
    return 0;
}
