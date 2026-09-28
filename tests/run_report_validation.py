"""Run report regressions without pytest, credentials, or production services.

Usage: python -B tests/run_report_validation.py
Uses the existing dependency directory when present; all databases are temporary.
"""
import importlib
import inspect
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'venv311/Lib/site-packages')]
sys.path.append(str(ROOT.parent / '.work/memory-deps'))
sys.dont_write_bytecode = True
from measure_incident_memory import load_app

MODULES = ['test_report_collaboration', 'test_report_conflicts', 'test_report_safety',
           'test_media_memory', 'test_matrix_auto_sync', 'test_operations_readiness',
           'test_operaciones_store', 'test_operaciones_sync', 'test_operaciones_matrix', 'test_pwa', 'test_runtime_guards']

def main():
    failures = passed = 0
    with tempfile.TemporaryDirectory(prefix='hsc-report-validation-') as sandbox:
        load_app(sandbox)  # Blocks network and strips credentials before importing Flask.
        os.chdir(ROOT)
        suite = unittest.TestSuite()
        for name in MODULES:
            module = importlib.import_module(name)
            suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
            for key, function in inspect.getmembers(module, inspect.isfunction):
                if not key.startswith('test_') or function.__module__ != name:
                    continue
                with tempfile.TemporaryDirectory() as directory:
                    params = inspect.signature(function).parameters
                    args = {key: Path(directory) for key in params if key == 'tmp_path'}
                    if len(params) != len(args):
                        raise AssertionError(('Unsupported fixture', name, key))
                    try:
                        function(**args)
                        passed += 1
                    except Exception:
                        failures += 1
                        traceback.print_exc()
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        print(f'Python functions: {passed} passed, {failures} failed; unittest: {result.testsRun} tests.')
        failures += len(result.failures) + len(result.errors)
        scripts = sorted(ROOT.glob('test*.cjs'))
        for script in scripts:
            process = subprocess.run(['node', str(script)], cwd=ROOT, capture_output=True,
                                     text=True, encoding='utf-8', errors='replace', timeout=45)
            if process.returncode:
                failures += 1
                print(script.name, process.stdout[-2000:], process.stderr[-3000:])
        print(f'JavaScript: {len(scripts)} test files executed.')
        smoke = subprocess.run([sys.executable, '-B', str(ROOT / 'tests/verify_report_app.py')], cwd=ROOT)
        failures += bool(smoke.returncode)
    return bool(failures)

if __name__ == '__main__':
    sys.exit(main())
