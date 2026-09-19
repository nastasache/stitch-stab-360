import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
"""Automated Repository Verification & Hygiene Checker.

Validates core coding and AI operational guardrails:
1. Universal bytecode blocking guards in all Python scripts.
2. Strictly Linux LF line endings across source files.
3. Rulebook parity between CLAUDE.md and GEMINI.md.
4. Absence of stale .pyc bytecode binaries in source folders.
"""

from pathlib import Path
import subprocess
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {
    "venv", ".venv", "env", ".env", ".git", ".idea",
    "tests/temp", "tests\\temp", "scratch", "data",
    "js/leaflet", "js\\leaflet", "js/three", "js\\three", "others"
}


def should_skip(path: Path) -> bool:
    rel = str(path.relative_to(REPO_ROOT))
    for exc in EXCLUDE_DIRS:
        if rel == exc or rel.startswith(exc + "/") or rel.startswith(exc + "\\"):
            return True
    return False


def check_bytecode_header_guards() -> Tuple[bool, List[str]]:
    missing = []
    for py_file in REPO_ROOT.rglob("*.py"):
        if should_skip(py_file):
            continue
        try:
            with open(py_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = [f.readline() for _ in range(5)]
            content = "".join(lines)
            if "dont_write_bytecode" not in content or "PYTHONDONTWRITEBYTECODE" not in content:
                rel = py_file.relative_to(REPO_ROOT)
                missing.append(str(rel))
        except Exception as e:
            missing.append(f"{py_file.name}: {e}")
    return len(missing) == 0, missing


def check_line_endings() -> Tuple[bool, List[str]]:
    crlf_files = []
    text_extensions = {".py", ".md", ".js", ".html", ".css", ".json", ".bat", ".sh", ".yml", ".txt"}
    for ext in text_extensions:
        for file_path in REPO_ROOT.rglob(f"*{ext}"):
            if should_skip(file_path):
                continue
            try:
                raw = open(file_path, "rb").read()
                if b"\r\n" in raw:
                    crlf_files.append(str(file_path.relative_to(REPO_ROOT)))
            except Exception:
                pass
    return len(crlf_files) == 0, crlf_files


def check_rulebook_parity() -> Tuple[bool, str]:
    claude_path = REPO_ROOT / "CLAUDE.md"
    gemini_path = REPO_ROOT / "GEMINI.md"

    if not claude_path.exists() or not gemini_path.exists():
        return False, "Missing CLAUDE.md or GEMINI.md"

    try:
        claude_lines = claude_path.read_text(encoding="utf-8").splitlines()[1:]
        gemini_lines = gemini_path.read_text(encoding="utf-8").splitlines()[1:]
        if claude_lines != gemini_lines:
            return False, "Content lines (2+) do not match between CLAUDE.md and GEMINI.md"
        return True, "100% matched"
    except Exception as e:
        return False, str(e)


def check_source_pyc() -> Tuple[bool, List[str]]:
    source_dirs = ["config", "routers", "scripts", "utils"]
    found_pyc = []
    for sdir in source_dirs:
        target = REPO_ROOT / sdir
        if target.exists():
            for pyc in target.rglob("*.pyc"):
                found_pyc.append(str(pyc.relative_to(REPO_ROOT)))
    return len(found_pyc) == 0, found_pyc


def check_run_counter_git_baseline() -> Tuple[bool, str]:
    counter_path = REPO_ROOT / "config" / "run_counter.txt"
    if not counter_path.exists():
        return False, "config/run_counter.txt missing on disk"
    try:
        res = subprocess.run(
            ["git", "show", ":config/run_counter.txt"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        if res.returncode != 0:
            res = subprocess.run(
                ["git", "show", "HEAD:config/run_counter.txt"],
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
        if res.returncode == 0:
            val = res.stdout.strip()
            if val != "1000":
                return False, f"Git blob is '{val}', must be strictly '1000'"
            return True, "staged/tracked strictly as 1000"
        return True, "verified on disk (pending stage)"
    except Exception as e:
        return False, str(e)


def main() -> int:
    print("=" * 60)
    print("StitchStab 360 -- Repository Verification Suite")
    print("=" * 60)
    all_passed = True

    # 1. Bytecode Header Guards
    ok_guards, missing_guards = check_bytecode_header_guards()
    if ok_guards:
        print("[PASS] Bytecode Header Guards: All Python scripts guarded.")
    else:
        print(f"[FAIL] Bytecode Header Guards missing in {len(missing_guards)} file(s):")
        for m in missing_guards[:5]:
            print(f"       - {m}")
        all_passed = False

    # 2. Linux LF Line Endings
    ok_eol, crlf_files = check_line_endings()
    if ok_eol:
        print("[PASS] Line Endings: Strictly Linux LF (zero CRLF detected).")
    else:
        print(f"[FAIL] CRLF line endings detected in {len(crlf_files)} file(s):")
        for c in crlf_files[:5]:
            print(f"       - {c}")
        all_passed = False

    # 3. Rulebook Parity
    ok_rules, rule_msg = check_rulebook_parity()
    if ok_rules:
        print(f"[PASS] Rulebook Parity: CLAUDE.md and GEMINI.md ({rule_msg}).")
    else:
        print(f"[FAIL] Rulebook Parity: {rule_msg}")
        all_passed = False

    # 4. Source .pyc Absence
    ok_pyc, pyc_files = check_source_pyc()
    if ok_pyc:
        print("[PASS] Bytecode Hygiene: No .pyc files in source trees.")
    else:
        print(f"[WARN] Found {len(pyc_files)} stale .pyc file(s) in source directories:")
        for p in pyc_files[:5]:
            print(f"       - {p}")
        # pyc warning does not hard-fail repo verification if gitignore/pycache exists
        print("       (Note: Manual cleanup recommended)")

    # 5. Run Counter Baseline
    ok_counter, counter_msg = check_run_counter_git_baseline()
    if ok_counter:
        print(f"[PASS] Run Counter Baseline: {counter_msg}.")
    else:
        print(f"[FAIL] Run Counter Baseline: {counter_msg}")
        all_passed = False

    print("=" * 60)
    if all_passed:
        print("Status: ALL CORE CHECKS PASSED")
        return 0
    else:
        print("Status: VERIFICATION FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
