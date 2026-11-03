#!/usr/bin/env python3
"""Run the Supabase CLI with SUPABASE_ACCESS_TOKEN / SUPABASE_DB_PASSWORD taken from .env.

    python scripts/supabase_cli.py functions deploy dashboard-api --use-api
    python scripts/supabase_cli.py db push --remote

The CLI asks for confirmation before changing the hosted project; add --yes only when you
mean it. Secret values are passed via the environment and never printed.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

env = dict(os.environ)
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^(SUPABASE_ACCESS_TOKEN|SUPABASE_DB_PASSWORD)=(.*)$", line)
    if m and m.group(2).strip():
        env[m.group(1)] = m.group(2).strip()
if "SUPABASE_ACCESS_TOKEN" not in env:
    sys.exit("SUPABASE_ACCESS_TOKEN missing in .env (see .env.example)")

npx = shutil.which("npx") or shutil.which("npx.cmd")
if not npx:
    sys.exit("npx not found (install Node.js)")
sys.exit(subprocess.call([npx, "-y", "supabase@latest", *sys.argv[1:]], cwd=ROOT, env=env))
