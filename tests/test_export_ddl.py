import os
from subprocess import run, PIPE, CalledProcessError
import sys

def test_export_ddl_generates_file(tmp_path):
    out = tmp_path / "schema.sql"
    # Run CLI via the same interpreter
    cmd = [sys.executable, "generate.py", "export-ddl", "--dialect=sqlite", f"--out={out}"]
    proc = run(cmd, stdout=PIPE, stderr=PIPE, text=True)
    assert proc.returncode == 0, f"CLI failed: {proc.stderr}"
    assert out.exists(), "schema.sql not created"
    content = out.read_text(encoding="utf-8")
    assert "CREATE TABLE" in content
