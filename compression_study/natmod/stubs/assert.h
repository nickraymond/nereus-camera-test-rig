/* Freestanding stand-in: the module is built with NDEBUG, so assert compiles out. */
#ifndef NR_ASSERT_H
#define NR_ASSERT_H
#define assert(x) ((void)0)
#endif
