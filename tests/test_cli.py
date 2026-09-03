"""Entrypoint behaviour: help text, exit codes, and console-safe output.

The encoding tests exist because of a real failure: a Windows console defaults to cp1252,
and an em dash in an error message rendered as a replacement glyph -

    PRIVATE_KEY is not set <?> needed to sign on sepolia

which reads as corruption rather than as instruction. Anything a user is meant to act on
has to survive the terminal it lands in.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINTS = ("run.py", "verify.py", "deploy.py", "preflight.py")


def cli(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True,
                          text=True, timeout=timeout)


@pytest.mark.parametrize("script", ENTRYPOINTS)
def test_help_works(script):
    proc = cli(script, "--help")
    assert proc.returncode == 0, proc.stderr
    assert "usage" in proc.stdout.lower()


@pytest.mark.parametrize("script", ENTRYPOINTS)
def test_help_survives_a_legacy_console(script):
    """cp1252 is the default on a Windows terminal; help text must encode there."""
    proc = cli(script, "--help")
    proc.stdout.encode("cp1252")


@pytest.mark.parametrize("script", ENTRYPOINTS)
def test_help_is_pure_ascii(script):
    """Stricter than the above and easier to act on: name the offending character."""
    offenders = sorted({c for c in cli(script, "--help").stdout if ord(c) > 127})
    assert not offenders, f"{script} help contains non-ASCII: {offenders}"


def test_missing_image_exits_one_with_a_readable_message():
    proc = cli("run.py", "--image", "definitely-not-here.jpg", "--chain", "local")
    assert proc.returncode == 1
    assert "no such image" in (proc.stdout + proc.stderr).lower()


def test_missing_bundle_exits_one():
    proc = cli("verify.py", "--bundle", "definitely-not-here.json", "--chain", "local")
    assert proc.returncode == 1


def test_sepolia_without_a_key_is_readable(monkeypatch):
    """The message that actually broke. It must be actionable and console-safe."""
    import os
    env = {k: v for k, v in os.environ.items() if k != "PRIVATE_KEY"}
    proc = subprocess.run([sys.executable, "deploy.py", "--chain", "sepolia"],
                          cwd=ROOT, capture_output=True, text=True, timeout=120, env=env)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 1
    assert "PRIVATE_KEY" in combined
    assert "--chain local" in combined, "should say how to proceed without a wallet"
    combined.encode("cp1252")
    assert not [c for c in combined if ord(c) > 127], "error text must be ASCII"


def test_an_unknown_flag_is_rejected():
    assert cli("run.py", "--image", "x.jpg", "--not-a-flag").returncode != 0


def test_runtime_printed_strings_are_ascii():
    """Scan the modules for non-ASCII inside string literals that reach a console.

    Comments and docstrings are exempt: they are never printed, and forcing ASCII prose
    into them would make the source worse for no benefit.
    """
    import ast

    offenders = []
    for path in sorted(ROOT.glob("*.py")) + sorted((ROOT / "pom").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc is not None:
                    docstrings.add(doc)

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in docstrings:
                    continue
                bad = {c for c in node.value if ord(c) > 127}
                if bad:
                    offenders.append(f"{path.name}:{node.lineno} {sorted(bad)}")

    assert not offenders, "non-ASCII in runtime strings:\n  " + "\n  ".join(offenders)


# ------------------------------------------------- reset chain vs real tampering

@pytest.mark.needs_chain
class TestRootNotOnChain:
    """Hardhat deploys deterministically, so restarting the node and redeploying puts a
    fresh, empty contract at the same address. A stale bundle then points at a live
    contract with no records - which must not be reported as altered evidence.
    """

    @pytest.fixture(scope="class")
    def bundle(self, tmp_path_factory):
        subprocess.run([sys.executable, "deploy.py", "--chain", "local"],
                       cwd=ROOT, capture_output=True, text=True, timeout=300)
        made = cli("run.py", "--image", "spike/control_small.jpg",
                   "--chain", "local", "--offline", timeout=900)
        if made.returncode != 0:
            pytest.skip(f"pipeline unavailable: {made.stderr[-200:]}")
        found = sorted((ROOT / "evidence").glob("run-*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not found:
            pytest.skip("no bundle produced")
        return found[0]

    def test_the_bundle_verifies_first(self, bundle):
        assert cli("verify.py", "--bundle", str(bundle),
                   "--chain", "local").returncode == 0

    def test_an_empty_contract_reads_as_a_reset_chain(self, bundle, tmp_path):
        import json

        from pom.chain import Chain

        fresh = Chain("local").deploy().address
        original = bundle.read_text(encoding="utf-8")
        try:
            data = json.loads(original)
            data["attestation"]["contract"] = fresh
            bundle.write_text(json.dumps(data, indent=2), encoding="utf-8")

            out = cli("verify.py", "--bundle", str(bundle), "--chain", "local")
            assert out.returncode == 7
            assert "no attestations at all" in out.stdout
            assert "reset chain" in out.stdout
        finally:
            bundle.write_text(original, encoding="utf-8")

    def test_a_populated_contract_still_reads_as_tampering(self, bundle):
        import json

        original = bundle.read_text(encoding="utf-8")
        try:
            data = json.loads(original)
            data["candidates"][0]["page_url"] = "https://tampered.example/"
            bundle.write_text(json.dumps(data, indent=2), encoding="utf-8")

            out = cli("verify.py", "--bundle", str(bundle), "--chain", "local")
            assert out.returncode == 6
            assert "does not correspond to any recorded run" in out.stdout
            assert "reset chain" not in out.stdout, (
                "real tampering must not be excused as a chain reset")
        finally:
            bundle.write_text(original, encoding="utf-8")
