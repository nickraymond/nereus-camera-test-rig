"""Systematic MDS erasure code over GF(256) at the chunk level (Cauchy Reed-Solomon).

k data chunks of MSG_B bytes + m parity chunks; ANY k of the k + m chunks rebuild the data
(k + m <= 256). Encode is k*m table multiplies per byte column (numpy); decode inverts the
k x k matrix of the received rows. Small enough to port to the Pi's Python (no new library)
or to call from a C helper; the backend needs the same few dozen lines.
"""
from __future__ import annotations

import numpy as np

_EXP = np.zeros(512, np.uint8)
_LOG = np.zeros(256, np.int32)
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
_EXP[255:510] = _EXP[:255]


def _mul(a: np.ndarray, b) -> np.ndarray:
    """Element-wise GF(256) product (broadcasting)."""
    a = np.asarray(a, np.uint8)
    b = np.asarray(b, np.uint8)
    out = _EXP[(_LOG[a] + _LOG[b]) % 255]
    return np.where((a == 0) | (b == 0), 0, out).astype(np.uint8)


def _inv(a: int) -> int:
    return int(_EXP[(255 - _LOG[a]) % 255])


def cauchy(m: int, k: int) -> np.ndarray:
    """m x k Cauchy matrix 1/(x_i + y_j), x = k..k+m-1, y = 0..k-1 (distinct, so every
    square submatrix of [I; C] is invertible)."""
    x = np.arange(k, k + m)[:, None]
    y = np.arange(k)[None, :]
    s = (x ^ y).astype(np.int32)
    return _EXP[(255 - _LOG[s]) % 255].astype(np.uint8)


def encode(data: list[bytes], m: int, size: int) -> list[bytes]:
    """m parity chunks for k data chunks (each zero-padded to `size`)."""
    k = len(data)
    d = np.zeros((k, size), np.uint8)
    for i, c in enumerate(data):
        d[i, :len(c)] = np.frombuffer(c, np.uint8)
    C = cauchy(m, k)
    par = np.zeros((m, size), np.uint8)
    for i in range(k):
        par ^= _mul(C[:, i:i + 1], d[i][None, :])
    return [bytes(r) for r in par]


def decode(received: dict[int, bytes], k: int, m: int, size: int) -> list[bytes]:
    """received: {chunk index 0..k+m-1: bytes}; needs >= k of them. -> the k data chunks."""
    if len(received) < k:
        raise ValueError(f"need {k} chunks, have {len(received)}")
    idx = sorted(received)[:k]
    G = np.vstack([np.eye(k, dtype=np.uint8), cauchy(m, k)])[idx]       # k x k
    Y = np.zeros((k, size), np.uint8)
    for r, i in enumerate(idx):
        Y[r, :len(received[i])] = np.frombuffer(received[i], np.uint8)
    A = np.concatenate([G, Y], axis=1)
    for col in range(k):                       # Gauss-Jordan over GF(256)
        piv = col + int(np.nonzero(A[col:, col])[0][0])
        if piv != col:
            A[[col, piv]] = A[[piv, col]]
        A[col] = _mul(A[col], _inv(int(A[col, col])))
        f = A[:, col].copy()
        f[col] = 0
        nz = np.nonzero(f)[0]
        if nz.size:
            A[nz] ^= _mul(f[nz][:, None], A[col][None, :])
    return [bytes(A[i, k:]) for i in range(k)]
