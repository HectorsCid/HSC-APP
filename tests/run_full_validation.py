"""Run every Python/CJS regression in a disposable source-only copy.

No database, credentials, uploaded media or business JSON files are copied.
Sockets are disabled before application import; subprocesses inherit that guard.
"""
import importlib
import inspect
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback
import unittest

ROOT = Path(__file__).resolve().parents[1]


def worker():
    sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), os.environ['HSC_TEST_DEPENDENCIES']]
    from measure_incident_memory import load_app
    load_app(str(ROOT))
    modules = sorted({path.stem for path in ROOT.glob('test*.py')} |
                     {path.stem for path in (ROOT / 'tests').glob('test_*.py')})
    if os.environ.get('HSC_TEST_BASELINE'):
        modules = [name for name in modules if name not in {'test_audit_regressions','test_sync_recovery','test_sync_recovery_api'}]
    suite = unittest.TestSuite()
    failures = functions = 0
    for name in modules:
        try:
            module = importlib.import_module(name)
            suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
            for key, function in inspect.getmembers(module, inspect.isfunction):
                if not key.startswith('test_') or function.__module__ != name:
                    continue
                with tempfile.TemporaryDirectory(prefix='hsc-function-') as directory:
                    params = inspect.signature(function).parameters
                    args = {key: Path(directory) for key in params if key == 'tmp_path'}
                    assert len(params) == len(args), (name, key, 'unsupported fixtures')
                    try:
                        function(**args)
                        functions += 1
                    except Exception:
                        failures += 1
                        traceback.print_exc()
        except Exception:
            failures += 1
            traceback.print_exc()
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    failures += len(result.failures) + len(result.errors)
    scripts = sorted(path for path in ROOT.glob('test*.cjs') if not (os.environ.get('HSC_TEST_BASELINE') and path.name in {'test_audit_regressions.cjs','test_sync_recovery.cjs'}))
    for script in scripts:
        run = subprocess.run(['node', str(script)], cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        if run.returncode:
            failures += 1
            print(script.name, run.stdout[-3000:], run.stderr[-3000:])
    print(f'FULL SUITE: {len(modules)} Python modules; {result.testsRun} unittest cases; {functions} functions passed; {len(scripts)} CJS files; {failures} failures.', flush=True)
    return bool(failures)


def main():
    if '--worker' in sys.argv:
        return worker()
    with tempfile.TemporaryDirectory(prefix='hsc-source-only-') as folder:
        target = Path(folder)
        for path in ROOT.iterdir():
            if path.is_file() and (path.suffix in {'.py', '.cjs', '.js', '.txt', '.yaml', '.toml'} or path.name == 'Procfile'):
                shutil.copy2(path, target / path.name)
        for name in ['templates', 'static', 'tests', 'img']:
            shutil.copytree(ROOT / name, target / name, ignore=shutil.ignore_patterns('__pycache__', 'diag_uploads', 'diag_pdfs', '*.sqlite*', '*.db', '*.pyc'))
        if '--baseline' in sys.argv:
            # Same sandbox and tests, only original application sources.
            changed = subprocess.check_output(['git', 'diff', '--name-only', '5495b12', '--', '*.py', '*.cjs', 'templates/*.html', 'static/*.js'], cwd=ROOT, text=True).splitlines()
            for name in changed:
                original = subprocess.run(['git', 'show', '5495b12:' + name], cwd=ROOT, capture_output=True)
                if original.returncode == 0:
                    (target / name).write_bytes(original.stdout)
        guard = target / 'sitecustomize.py'
        guard.write_text("import socket\ndef blocked(*a,**k): raise RuntimeError('Network disabled in regression sandbox')\nsocket.socket.connect=blocked\nsocket.socket.connect_ex=blocked\nsocket.create_connection=blocked\n", encoding='utf-8')
        allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'COMSPEC', 'PATHEXT'}
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        dependencies = str(ROOT / 'venv311/Lib/site-packages')
        bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/Lib/site-packages'
        env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join([folder, dependencies, str(bundled)]), HSC_TEST_DEPENDENCIES=dependencies)
        if '--baseline' in sys.argv:
            env['HSC_TEST_BASELINE'] = '1'
        return subprocess.run([sys.executable, '-B', str(target / 'tests/run_full_validation.py'), '--worker'], cwd=target, env=env).returncode


if __name__ == '__main__':
    sys.exit(main())
