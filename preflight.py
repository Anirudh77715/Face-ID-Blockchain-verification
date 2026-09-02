"""Check everything the pipeline needs, before it matters.

    py preflight.py                  # environment + repo state
    py preflight.py --recording      # stricter: everything the one take depends on

There are no resubmissions, so the failure modes worth catching are the quiet ones — a
truncated model that reports "no face", a chain that is not running, a contract that was
never deployed, a root already attested so a rerun sends no transaction. Each of those
looks like a bug in the demo rather than a setup problem, on camera, with no second take.

Exit 0 if every required check passes. Warnings never fail the run.
"""

from __future__ import annotations

import argparse
import importlib
import shutil
import subprocess
import sys
from pathlib import Path

import pom  # noqa: F401  - configures UTF-8 output and BLAS threads on import

ROOT = Path(__file__).resolve().parent

GREEN, RED, YELLOW, DIM, BOLD, OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m")

PASS, FAIL, WARN = f"{GREEN}pass{OFF}", f"{RED}FAIL{OFF}", f"{YELLOW}warn{OFF}"


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def ok(self, label: str, detail: str = "") -> None:
        print(f"  [{PASS}] {label}" + (f"  {DIM}{detail}{OFF}" if detail else ""))

    def fail(self, label: str, fix: str) -> None:
        print(f"  [{FAIL}] {label}\n         {DIM}fix: {fix}{OFF}")
        self.failures.append(label)

    def warn(self, label: str, detail: str = "") -> None:
        print(f"  [{WARN}] {label}" + (f"\n         {DIM}{detail}{OFF}" if detail else ""))
        self.warnings.append(label)


def section(title: str) -> None:
    print(f"\n{BOLD}{title}{OFF}")


# --------------------------------------------------------------------------- checks

def check_python(r: Report) -> None:
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 10):
        r.ok("python", f"{major}.{minor}")
    else:
        r.fail(f"python {major}.{minor} is too old", "this code needs 3.10+")


def check_imports(r: Report) -> None:
    for module, fix in (
        ("cv2", "pip install -r requirements.txt"),
        ("numpy", "pip install -r requirements.txt"),
        ("requests", "pip install -r requirements.txt"),
        ("web3", "pip install -r requirements.txt"),
        ("playwright", "pip install -r requirements.txt"),
    ):
        try:
            m = importlib.import_module(module)
            r.ok(module, getattr(m, "__version__", ""))
        except ImportError:
            r.fail(f"{module} is not installed", fix)


def check_models(r: Report) -> None:
    try:
        from scripts.fetch_models import FILES
    except Exception:
        sys.path.insert(0, str(ROOT / "scripts"))
        from fetch_models import FILES  # type: ignore

    import hashlib
    for name, (_, want) in FILES.items():
        path = ROOT / "models" / name
        if not path.exists():
            r.fail(f"model missing: {name}", "py scripts/fetch_models.py")
            continue
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if got != want:
            # The important one: a truncated model does not raise, it just detects
            # nothing, which reads as a broken pipeline.
            r.fail(f"model corrupt: {name}", "py scripts/fetch_models.py")
        else:
            r.ok(name, f"{path.stat().st_size / 1e6:.1f} MB, sha256 ok")


def check_face_pipeline(r: Report) -> None:
    control = ROOT / "spike" / "control_small.jpg"
    if not control.exists():
        r.warn("no control image; skipping live face check")
        return
    try:
        from pom.face import FaceEncoder
        scan = FaceEncoder().scan_path(control)
        r.ok("face detection", f"score {scan.score:.3f} at {scan.detect_scale}px")
    except Exception as e:
        r.fail(f"face detection failed: {type(e).__name__}: {e}",
               "py scripts/fetch_models.py, then re-run")


def check_playwright(r: Report) -> None:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            browser.close()
        r.ok("playwright chromium", "launches")
    except Exception as e:
        r.fail(f"chromium will not launch: {type(e).__name__}",
               "python -m playwright install chromium")


def check_reference_capture(r: Report) -> None:
    from pom.search import REFERENCE_CAPTURE, BingScripted
    if not REFERENCE_CAPTURE.exists():
        r.warn("reference capture missing", "replay will not work from a clean clone")
        return
    n = len(BingScripted._parse(
        REFERENCE_CAPTURE.read_bytes().decode("utf-8", errors="replace")))
    if n:
        r.ok("reference capture", f"{n} candidates parse")
    else:
        r.fail("reference capture parses to nothing",
               "the Bing layout may have changed; re-capture")


def check_secrets(r: Report) -> None:
    """Report which optional credentials are configured.

    Presence only - never the value, and never a prefix of it. A preflight that echoes
    part of a key trains people to paste its output into chats and issues.
    """
    import os

    env_file = ROOT / ".env"
    if env_file.exists():
        r.ok(".env", "present (gitignored)")
    else:
        r.warn("no .env file",
               "cp .env.example .env - only needed for serpapi or Base Sepolia")

    for name, needed_for in (
        ("SERPAPI_KEY", "--backend serpapi"),
        ("PRIVATE_KEY", "--chain sepolia"),
    ):
        value = os.environ.get(name, "").strip()
        if not value:
            r.warn(f"{name} not set", f"optional; only needed for {needed_for}")
            continue

        # Validate before reporting it as usable, so a malformed key is not announced
        # as a pass on one line and a failure on the next.
        if name == "PRIVATE_KEY" and not _is_hex_key(value):
            r.fail("PRIVATE_KEY is set but is not a 32-byte hex key",
                   "expected 64 hex characters, with or without a 0x prefix")
            continue

        r.ok(name, f"set ({len(value)} chars) - enables {needed_for}")


def _is_hex_key(value: str) -> bool:
    cleaned = value.removeprefix("0x")
    return len(cleaned) == 64 and all(c in "0123456789abcdefABCDEF" for c in cleaned)


def check_contract(r: Report) -> None:
    artifact = (ROOT / "artifacts" / "contracts" / "AttestationRegistry.sol"
                / "AttestationRegistry.json")
    if artifact.exists():
        r.ok("contract compiled", artifact.name)
    else:
        r.fail("contract not compiled", "npx hardhat compile")


def check_chain(r: Report, recording: bool) -> None:
    try:
        from pom.chain import Chain
        chain = Chain("local")
    except Exception:
        (r.fail if recording else r.warn)(
            "local chain unreachable", "npx hardhat node")
        return

    r.ok("local chain", f"chainId 31337, block {chain.w3.eth.block_number}")

    try:
        address = chain.address()
    except Exception:
        (r.fail if recording else r.warn)(
            "no contract deployed on the local chain", "py deploy.py --chain local")
        return

    try:
        count = chain.contract().functions.count().call()
        r.ok("contract deployed", f"{address}  ({count} attestations)")
    except Exception as e:
        r.fail(f"deployed address is not a working contract: {type(e).__name__}",
               "npx hardhat node was probably restarted; py deploy.py --chain local")


def check_recording_readiness(r: Report) -> None:
    """A rerun over identical evidence sends no transaction. Better to know now."""
    from pom.chain import Chain
    evidence_dir = ROOT / "evidence"
    bundles = list(evidence_dir.glob("run-*.json")) if evidence_dir.exists() else []
    if bundles:
        r.warn(f"{len(bundles)} existing bundle(s) in evidence/",
               "rm -rf evidence/ for a clean take")

    try:
        chain = Chain("local")
        if chain.contract().functions.count().call() > 0:
            r.warn("this contract already holds attestations",
                   "re-running identical evidence will send no transaction; "
                   "py deploy.py --chain local for a fresh one")
    except Exception:
        pass


def check_git(r: Report) -> None:
    if not shutil.which("git"):
        return
    try:
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                               capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:
        return
    if dirty:
        r.warn(f"{len(dirty.splitlines())} uncommitted change(s)",
               "commit before recording so git log tells the true story")
    else:
        r.ok("git", "working tree clean")


def check_tests(r: Report) -> None:
    try:
        # No -q here: pytest.ini already supplies one, and -qq suppresses the summary
        # line entirely, leaving only progress dots to report.
        proc = subprocess.run([sys.executable, "-m", "pytest", "--no-header"],
                              cwd=ROOT, capture_output=True, text=True, timeout=600)
    except Exception as e:
        r.warn(f"could not run tests: {type(e).__name__}")
        return
    # The last line under -q is the progress dots; the summary is the line that
    # actually names counts.
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    summary = next((ln for ln in reversed(lines)
                    if "passed" in ln or "failed" in ln or "error" in ln),
                   lines[-1] if lines else "no output")
    if proc.returncode == 0:
        r.ok("tests", summary)
    else:
        r.fail(f"tests failing: {summary}", "py -m pytest")


# ------------------------------------------------------------------ end to end

def check_end_to_end(r: Report) -> None:
    """Actually run the pipeline and assert every outcome.

    Uses the replay backend on purpose. The question this answers is "is my project
    working", and a live search failing means a provider is rate-limiting today - which
    is real, but it is not the project being broken. Exit codes are the contract, so they
    are what gets asserted.
    """
    import shutil
    import tempfile

    workdir = Path(tempfile.mkdtemp(prefix="pom-e2e-"))
    try:
        deployed = subprocess.run(
            [sys.executable, "deploy.py", "--chain", "local"],
            cwd=ROOT, capture_output=True, text=True, timeout=300)
        if deployed.returncode != 0:
            r.fail("deploy failed", "is `npx hardhat node` running?")
            return
        address = next((ln.split()[1] for ln in deployed.stdout.splitlines()
                        if ln.startswith("contract")), None)
        r.ok("deploy", address or "ok")

        run = subprocess.run(
            [sys.executable, "run.py", "--image", "spike/control_small.jpg",
             "--chain", "local", "--backend", "replay"],
            cwd=ROOT, capture_output=True, text=True, timeout=900)
        if run.returncode != 0:
            r.fail(f"pipeline exited {run.returncode}, expected 0",
                   (run.stderr or run.stdout)[-300:])
            return
        r.ok("pipeline runs", "exit 0")

        bundles = sorted((ROOT / "evidence").glob("run-*.json"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        if not bundles:
            r.fail("pipeline wrote no evidence bundle", "check run.py output")
            return
        bundle = bundles[0]

        def verify(extra: list[str] | None = None) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, "verify.py", "--bundle", str(bundle),
                 "--chain", "local", *(extra or [])],
                cwd=ROOT, capture_output=True, text=True, timeout=300)

        if verify().returncode == 0:
            r.ok("verify accepts untouched evidence", "exit 0")
        else:
            r.fail("verify rejected untouched evidence", "the commitment is not matching")
            return

        # The claim is tamper-evidence, so the negative case is the one that matters.
        pristine = workdir / "pristine.json"
        shutil.copy(bundle, pristine)
        try:
            import json
            data = json.loads(bundle.read_text(encoding="utf-8"))
            data["candidates"][0]["page_url"] = "https://tampered.example/"
            bundle.write_text(json.dumps(data, indent=2), encoding="utf-8")

            tampered = verify()
            if tampered.returncode == 6 and "candidate[0]" in tampered.stdout:
                r.ok("verify rejects tampered evidence", "exit 6, names candidate[0]")
            elif tampered.returncode == 0:
                r.fail("verify ACCEPTED tampered evidence",
                       "tamper-evidence is broken - this is the core claim")
            else:
                r.fail(f"tamper detected but reported oddly (exit {tampered.returncode})",
                       "expected exit 6 naming the field")
        finally:
            shutil.copy(pristine, bundle)

        disclosed = verify(["--disclose", "0"])
        if disclosed.returncode == 0 and "accepts this leaf" in disclosed.stdout:
            r.ok("on-chain inclusion proof", "contract accepts a single leaf")
        else:
            r.warn("selective disclosure did not confirm",
                   "non-fatal; the core path still verified")

        noface = subprocess.run(
            [sys.executable, "run.py", "--image", "spike/noface.jpg", "--chain", "local"],
            cwd=ROOT, capture_output=True, text=True, timeout=300)
        if noface.returncode == 4:
            r.ok("guardrail: no face", "exit 4, nothing written")
        else:
            r.fail(f"no-face image exited {noface.returncode}, expected 4",
                   "the guardrail is not firing")
    except subprocess.TimeoutExpired:
        r.fail("end-to-end check timed out", "is the local chain responsive?")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ----------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recording", action="store_true",
                    help="stricter checks for a one-take screen recording")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--e2e", action="store_true",
                    help="also run the whole pipeline and assert every outcome")
    args = ap.parse_args()

    r = Report()

    section("environment")
    check_python(r)
    check_imports(r)

    section("models")
    check_models(r)
    check_face_pipeline(r)

    section("search")
    check_playwright(r)
    check_reference_capture(r)

    section("secrets")
    check_secrets(r)

    section("chain")
    check_contract(r)
    check_chain(r, args.recording)

    section("repo")
    check_git(r)
    if not args.skip_tests:
        check_tests(r)

    if args.e2e:
        section("end to end")
        check_end_to_end(r)

    if args.recording:
        section("recording readiness")
        check_recording_readiness(r)

    print()
    if r.failures:
        print(f"{RED}{BOLD}{len(r.failures)} check(s) failed{OFF} - "
              f"{len(r.warnings)} warning(s)")
        for f in r.failures:
            print(f"  - {f}")
        return 1

    if r.warnings:
        print(f"{GREEN}{BOLD}all required checks passed{OFF} - "
              f"{len(r.warnings)} warning(s), read them before recording")
        return 0

    print(f"{GREEN}{BOLD}all checks passed{OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
