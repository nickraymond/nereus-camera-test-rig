/*
 * libhydrium memory.c
 */

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#ifndef HYD_HOST_ALLOC
#include <stdlib.h>
#endif

#include "libhydrium/libhydrium.h"
#include "memory.h"

#ifdef HYD_HOST_ALLOC
void *hyd_host_realloc(void *ptr, size_t n);  /* like realloc; may not return NULL */
void hyd_host_free(void *ptr);
#else
#define hyd_host_realloc realloc
#define hyd_host_free free
#endif

/* bytes in use / high-water mark (reset by hyd_mem_reset_peak); not static because MicroPython's
 * native-module linker (mpy_ld) only places global bss variables */
size_t hyd_mem_cur, hyd_mem_hwm;

#define HDR 8

void *hyd_mem_realloc(void *ptr, size_t n) {
    size_t old = 0;
    uint8_t *base = NULL;
    if (ptr) {
        base = (uint8_t *)ptr - HDR;
        memcpy(&old, base, sizeof(old));
    }
    if (!n) {
        hyd_mem_free(ptr);
        return NULL;
    }
    uint8_t *p = hyd_host_realloc(base, n + HDR);
    if (!p)
        return NULL;
    memcpy(p, &n, sizeof(n));
    hyd_mem_cur += n - old;
    if (hyd_mem_cur > hyd_mem_hwm)
        hyd_mem_hwm = hyd_mem_cur;
    return p + HDR;
}

void *hyd_mem_malloc(size_t n) {
    return hyd_mem_realloc(NULL, n ? n : 1);
}

void *hyd_mem_calloc(size_t nmemb, size_t size) {
    size_t total = nmemb * size;
    if (size && total / size != nmemb)
        return NULL;
    void *p = hyd_mem_malloc(total);
    if (p)
        memset(p, 0, total);
    return p;
}

void hyd_mem_free(void *ptr) {
    if (!ptr)
        return;
    uint8_t *base = (uint8_t *)ptr - HDR;
    size_t old;
    memcpy(&old, base, sizeof(old));
    hyd_mem_cur -= old;
    hyd_host_free(base);
}

size_t hyd_mem_peak(void) {
    return hyd_mem_hwm;
}

void hyd_mem_reset_peak(void) {
    hyd_mem_hwm = hyd_mem_cur;
}

#define total_size_check(n, s, retv) \
size_t total_size = (n) * (s); \
if ((s) && total_size / (s) != (n)) \
    return (retv);

void *hyd_malloc_array(size_t nmemb, size_t size) {
    total_size_check(nmemb, size, NULL);
    return hyd_mem_malloc(total_size);
}

void *hyd_realloc_array(void *ptr, size_t nmemb, size_t size) {
    total_size_check(nmemb, size, NULL);
    return hyd_mem_realloc(ptr, total_size);
}

HYDStatusCode hyd_realloc_p(void *buffer, size_t buffer_size) {
    void **bufferp = buffer;
    void *new_buffer = hyd_mem_realloc(*bufferp, buffer_size);
    if (!new_buffer)
        return HYD_NOMEM;
    *bufferp = new_buffer;

    return HYD_OK;
}

HYDStatusCode hyd_realloc_array_p(void *buffer, size_t nmemb, size_t size) {
    total_size_check(nmemb, size, HYD_NOMEM);
    return hyd_realloc_p(buffer, total_size);
}

HYDStatusCode hyd_malloc_arraybuffer_p(size_t nmemb, size_t size, void *array,
        size_t sizeof_array, void *ptrp) {
    void **ptr = ptrp;
    total_size_check(nmemb, size, HYD_NOMEM);
    if (total_size > sizeof_array) {
        *ptr = hyd_mem_malloc(total_size);
        if (!*ptr)
            return HYD_NOMEM;
    } else {
        *ptr = array;
    }

    return HYD_OK;
}

HYDStatusCode hyd_calloc_arraybuffer_p(size_t nmemb, size_t size, void *array,
        size_t sizeof_array, void *ptrp) {
    void **ptr = ptrp;
    total_size_check(nmemb, size, HYD_NOMEM);
    if (total_size > sizeof_array) {
        *ptr = hyd_mem_calloc(nmemb, size);
        if (!*ptr)
            return HYD_NOMEM;
    } else {
        memset(array, 0, sizeof_array);
        *ptr = array;
    }

    return HYD_OK;
}
