"""Build code.zip in the repo root (excludes caches/venvs/dataset)."""
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"__pycache__", ".venv", "venv", "node_modules"}
out = os.path.join(ROOT, "code.zip")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for base, dirs, files in os.walk(os.path.join(ROOT, "code")):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith((".pyc", ".env")):
                continue
            p = os.path.join(base, f)
            z.write(p, os.path.relpath(p, ROOT))
    z.write(os.path.join(ROOT, "README.md"), "README.md")
    z.write(os.path.join(ROOT, "problem_statement.md"), "problem_statement.md")
print("wrote", out)
