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


# Load .env, if there is one. Secrets belong in a gitignored file rather than in a shell
# history or a command line, and re-exporting them every session is the kind of friction
# that ends with a key pasted somewhere it should not be.
#
# Real environment variables always win (override=False): CI sets them properly, and a
# stale .env silently beating a deliberately-set variable is a bad surprise.
def _load_dotenv() -> None:
    env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(env_file):
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        # Optional dependency. Fall back to a minimal parser so a missing package never
        # silently means "your key was ignored".
        with open(env_file, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
        return
    load_dotenv(env_file, override=False)


_load_dotenv()
