"""Scan tracked/staged text for credential patterns without printing matching values."""
from pathlib import Path
import re
import subprocess
import sys

paths = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
patterns = [re.compile(r"sk-or-v1-[a-zA-Z0-9]{24,}"),
            re.compile(r"gh[pousr]_[a-zA-Z0-9]{30,}"),
            re.compile(r"github_pat_[a-zA-Z0-9_]{30,}")]
bad = []
for name in filter(None, paths):
    if Path(name).name.startswith(".env") and Path(name).name != ".env.example":
        bad.append(name)
        continue
    blob = subprocess.check_output(["git", "show", ":" + name])
    text = blob.decode("utf-8", errors="replace")
    if any(p.search(text) for p in patterns):
        bad.append(name)
if bad:
    print("Credential-like content in: " + ", ".join(bad), file=sys.stderr)
    sys.exit(1)
print("Tracked-content credential check passed.")
