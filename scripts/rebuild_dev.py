# scripts/rebuild_dev.py
import json, os, shutil, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
draft = ROOT / "schema_v3.json"
meta  = ROOT / "schema.meta.json"
schema = ROOT / "schema.json"
logf  = ROOT / "schema.meta.log.json"
dbfile = ROOT / "app.db"   # adjust to your SQLite path (see engine/db.py)

subprocess.check_call([
    "python", "conversion/schema_converter.py",
    str(draft), "-o", str(meta), "--log", str(logf)
])

shutil.copyfile(meta, schema)

if dbfile.exists():
    dbfile.unlink()  # drop DB for a clean recreate

env = os.environ.copy()
env["ENGINE_RECREATE"] = "1"
subprocess.check_call(["uvicorn", "engine.main:app", "--log-level", "info"], env=env)
