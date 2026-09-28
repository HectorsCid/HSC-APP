"""Isolated regression entry point; no credentials, network or production DB."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'venv311/Lib/site-packages')]
sys.dont_write_bytecode = True
from measure_incident_memory import load_app

with tempfile.TemporaryDirectory(prefix='hsc-audit-run-') as sandbox:
    load_app(sandbox)
    os.chdir(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('test_audit_regressions'))
    js = subprocess.run(['node', '--test', 'test_audit_regressions.cjs'], cwd=ROOT)
    sys.exit(not result.wasSuccessful() or js.returncode != 0)
