"""proof-of-match - face scan -> genuine reverse image search -> on-chain attestation.

Import side effect, deliberately placed before numpy/OpenCV are ever loaded:

OpenBLAS allocates per-thread scratch arenas on first use. Running YuNet across a batch of
candidate images exhausts them part-way through and the process dies with

    OpenBLAS error: Memory allocation still failed after 10 retries, giving up.

taking buffered stdout with it, so the failure shows no traceback and no output at all.
Pinning to one thread fixes it and costs nothing here: the work is ONNX inference, which
OpenCV threads itself, not BLAS. This must run before `import numpy`, which is why it
lives in the package __init__ rather than in a main().
"""

import os

for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ.setdefault(_var, "1")
