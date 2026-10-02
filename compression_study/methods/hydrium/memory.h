#ifndef HYDRIUM_MEMORY_H_
#define HYDRIUM_MEMORY_H_

#include <stddef.h>
#include <stdint.h>

#include "libhydrium/libhydrium.h"

/*
 * Study patch: every allocation goes through these four (memory.c). With HYD_HOST_ALLOC defined
 * (the OpenMV native module) the bytes come from hyd_host_realloc / hyd_host_free, supplied by
 * the caller (MicroPython's heap); otherwise from libc. Peak bytes in use (hyd_mem_peak) are
 * counted with libc, and with HYD_HOST_ALLOC only if HYD_MEM_STATS is also defined.
 */
void *hyd_mem_malloc(size_t n);
void *hyd_mem_calloc(size_t nmemb, size_t size);
void *hyd_mem_realloc(void *ptr, size_t n);
void hyd_mem_free(void *ptr);

static inline void hyd_freep(void *ptrp) {
    void **ptrv = ptrp;
    if (ptrv && *ptrv) {
        hyd_mem_free(*ptrv);
        *ptrv = NULL;
    }
}

static inline void hyd_free_arraybuffer_p(void *array, void *ptrp) {
    void **ptrv = ptrp;
    if (array != *ptrv)
        hyd_freep(ptrv);
}

void *hyd_malloc_array(size_t nmemb, size_t size);
void *hyd_realloc_array(void *ptr, size_t nmemb, size_t size);
HYDStatusCode hyd_realloc_p(void *buffer, size_t buffer_size);
HYDStatusCode hyd_realloc_array_p(void *buffer, size_t nmemb, size_t size);
HYDStatusCode hyd_malloc_arraybuffer_p(size_t nmemb, size_t size, void *array,
    size_t sizeof_array, void *ptrp);
HYDStatusCode hyd_calloc_arraybuffer_p(size_t nmemb, size_t size, void *array,
    size_t sizeof_array, void *ptrp);

#endif /* HYDRIUM_MEMORY_H_ */
