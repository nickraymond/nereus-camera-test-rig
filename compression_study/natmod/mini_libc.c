/*
 * The few libc functions hydrium calls (and clang emits for struct copies), for the native
 * module, which links no libc. Byte loops: speed is irrelevant next to the DCT. clang's
 * loop-idiom pass leaves functions named memcpy / memset alone, so these do not recurse.
 */
#include <stddef.h>

void *memcpy(void *d, const void *s, size_t n) {
    unsigned char *a = d;
    const unsigned char *b = s;
    while (n--) *a++ = *b++;
    return d;
}

void *memmove(void *d, const void *s, size_t n) {
    unsigned char *a = d;
    const unsigned char *b = s;
    if (a < b) {
        while (n--) *a++ = *b++;
    } else {
        while (n--) a[n] = b[n];
    }
    return d;
}

void *memset(void *d, int c, size_t n) {
    unsigned char *a = d;
    while (n--) *a++ = (unsigned char)c;
    return d;
}

int memcmp(const void *x, const void *y, size_t n) {
    const unsigned char *a = x, *b = y;
    for (; n; n--, a++, b++) {
        if (*a != *b) return *a - *b;
    }
    return 0;
}

/* ARM EABI run-time helpers clang emits for zeroing / copying (note memset's argument order) */
void __aeabi_memclr(void *d, size_t n) { memset(d, 0, n); }
void __aeabi_memclr4(void *d, size_t n) { memset(d, 0, n); }
void __aeabi_memclr8(void *d, size_t n) { memset(d, 0, n); }
void __aeabi_memset(void *d, size_t n, int c) { memset(d, c, n); }
void __aeabi_memset4(void *d, size_t n, int c) { memset(d, c, n); }
void __aeabi_memset8(void *d, size_t n, int c) { memset(d, c, n); }
void __aeabi_memcpy(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memcpy4(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memcpy8(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memmove(void *d, const void *s, size_t n) { memmove(d, s, n); }
void __aeabi_memmove4(void *d, const void *s, size_t n) { memmove(d, s, n); }
void __aeabi_memmove8(void *d, const void *s, size_t n) { memmove(d, s, n); }
