"""secret_scan: refuse to let a credential or a local artefact reach a public repo.

Run it before every commit:

    .venv/Scripts/python scripts/secret_scan.py

It reads only files git is tracking, so an ignored .env or a local database is
out of scope by construction. Two kinds of finding fail the run:

  patterns   a string shaped like a real credential
  paths      a tracked file that should never be committed

It never prints a matched value. A scan that leaked the secret it found would
be worse than no scan, so findings are reported as "file:line, rule name" and
nothing else.

One deliberate exception, and it covers only the bare Shopify prefix with no
payload after it. tests/ asserts that a token never appears in a response, a
log or /api/state, and does that with sentinel strings carrying the prefix.
scripts/check_access.py explains the prefix in an error message. This file has
to name what it looks for. Those three are the only places allowed.

The exception is deliberately narrow. A real token is matched by
'shopify_admin_token', which requires the 32-character payload, and that rule
has no exemption anywhere. So a genuine credential pasted into tests/ or into
this file still fails the scan.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# A real token is the prefix plus its payload. These match the payload too, so
# a documentation mention of the bare prefix is not a finding anywhere.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("shopify_admin_token", re.compile(r"shpat_[0-9a-fA-F]{32}")),
    ("shopify_custom_app_token", re.compile(r"shpca_[0-9a-fA-F]{32}")),
    ("shopify_private_app_token", re.compile(r"shppa_[0-9a-fA-F]{32}")),
    ("shopify_app_secret", re.compile(r"shpss_[0-9a-fA-F]{32}")),
    ("anthropic_api_key", re.compile(r"sk-ant-[0-9A-Za-z_\-]{20,}")),
    ("openai_api_key", re.compile(r"sk-[A-Za-z0-9]{32,}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("slack_token", re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}")),
]

# The bare prefix, with no payload. A real token is caught by
# 'shopify_admin_token' above, which is exempted nowhere.
BARE_PREFIX = re.compile(r"shpat_")
BARE_PREFIX_ALLOWED_IN = (
    "tests/",                   # sentinels that prove a token never leaks
    "scripts/check_access.py",  # explains the prefix in an error message
    "scripts/secret_scan.py",   # this file has to name what it looks for
)

# Tracked paths that should never be in a public repo.
FORBIDDEN_PATHS: list[tuple[str, re.Pattern[str]]] = [
    ("env_file", re.compile(r"(^|/)\.env(\.|$)(?!example)")),
    ("env_file", re.compile(r"(^|/)\.env$")),
    ("database", re.compile(r"\.sqlite(-shm|-wal)?$|\.sqlite3$|\.db$")),
    ("cache_dir", re.compile(r"(^|/)\.(cache|pytest_cache|mypy_cache)/")),
    ("room_photo", re.compile(r"^fixtures/photos/.*\.(jpe?g|png|heic)$")),
    ("eval_room_media", re.compile(r"^eval/data/.*\.(jpe?g|png|heic|mp4|mov)$")),
    ("eval_cases", re.compile(r"^eval/data/cases\.json$")),
    ("key_file", re.compile(r"\.(key|pem|p12|pfx)$")),
    ("os_cruft", re.compile(r"(^|/)(\.DS_Store|Thumbs\.db)$")),
    ("credential_dump", re.compile(r"(?i)(token|secret|credential)s?\.(txt|json|eml)$")),
]

SKIP_SUFFIXES = {
    ".glb", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".pdf",
    ".woff", ".woff2", ".ttf", ".zip", ".whl", ".mp4", ".mov",
}


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def main() -> int:
    findings: list[str] = []

    for rel in tracked_files():
        for name, rx in FORBIDDEN_PATHS:
            if rx.search(rel):
                findings.append(f"  {rel}: tracked file matches rule '{name}'")

        if Path(rel).suffix.lower() in SKIP_SUFFIXES:
            continue

        try:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError, FileNotFoundError):
            continue

        allowed_bare = rel.startswith(BARE_PREFIX_ALLOWED_IN)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for name, rx in PATTERNS:
                if rx.search(line):
                    findings.append(f"  {rel}:{lineno}: matches rule '{name}'")
            if not allowed_bare and BARE_PREFIX.search(line):
                findings.append(
                    f"  {rel}:{lineno}: matches rule 'shopify_token_prefix' "
                    f"(allowed only in {' and '.join(BARE_PREFIX_ALLOWED_IN)})"
                )

    if findings:
        print("secret_scan: FAIL")
        for finding in sorted(set(findings)):
            print(finding)
        print(f"\n{len(set(findings))} finding(s). Nothing above prints the matched value.")
        return 1

    print(f"secret_scan: clean. {len(tracked_files())} tracked files checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
