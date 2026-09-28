"""Start our own temporary PostgreSQL cluster, test, then stop it.

No existing database URL is accepted. Requires official PostgreSQL binaries and
psycopg on PYTHONPATH. --schema21-source points at the saved pre-change module.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests'), str(ROOT/'venv311/Lib/site-packages')]
import psycopg
from psycopg import sql
from operaciones_store import OperationsStore, SCHEMA_VERSION
from test_sync_recovery import SyncRecoveryTests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bin-dir', required=True)
    parser.add_argument('--schema21-source', required=True)
    args = parser.parse_args()
    binary = Path(args.bin_dir).resolve()
    scratch = tempfile.TemporaryDirectory(prefix='hsc-real-postgres-')
    root = Path(scratch.name).resolve()
    data = root/'data'
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    env = {k:v for k,v in os.environ.items() if not k.upper().startswith(('PG','GOOGLE','OPERACIONES_DATABASE','DATABASE_URL'))}
    env['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(ROOT/'tests'), str(ROOT/'venv311/Lib/site-packages'), *sys.path])
    def command(exe, *parts):
        # PostgreSQL background children can inherit PIPE handles on Windows.
        # Files prevent communicate() waiting forever for those inherited handles.
        output=root/('command-'+uuid.uuid4().hex+'.log')
        with output.open('w',encoding='utf-8') as log:
            result=subprocess.run([str(binary/(exe+'.exe' if os.name=='nt' else exe)), *map(str,parts)],
                env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,timeout=45,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        result.stdout=output.read_text(encoding='utf-8',errors='replace');result.stderr=''
        return result
    initialized = command('initdb','-D',data,'-U','hsc_test','-A','trust','--encoding=UTF8','--locale=C')
    if initialized.returncode:
        raise RuntimeError(initialized.stdout+initialized.stderr)
    started = False
    base = f'postgresql://hsc_test@127.0.0.1:{port}/postgres'
    children = []
    def child(code, *values):
        process = subprocess.Popen([sys.executable,'-B','-c',code,*map(str,values)],env=env,cwd=root,
                                   stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        children.append(process)
        return process
    def fresh():
        name = 'test_'+uuid.uuid4().hex
        with psycopg.connect(base,autocommit=True) as conn:
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        return OperationsStore(database_url=base.rsplit('/',1)[0]+'/'+name)
    def seed(store):
        store.import_matrix_snapshot(dict(clients=[dict(id='TEST',name='Cliente ficticio')],equipment=[dict(id='TEST1',client_id='TEST',name='Equipo ficticio')],reports=[],faults=[]))
        draft=store.save_report_draft(dict(client_id='TEST',equipment_id='TEST1',round='1',payload={'p1':'120','inicio':'2026-09-28','fin':'2026-09-28'}))
        report=store.finalize_report(draft['id'])
        return report['id']
    try:
        result=command('pg_ctl','-D',data,'-l',root/'postgres.log','-o',f'-h 127.0.0.1 -p {port} -c max_connections=35','-w','start')
        if result.returncode:
            raise RuntimeError(result.stdout+result.stderr)
        started=True
        with psycopg.connect(base) as conn:
            actual,version=conn.execute("SELECT current_setting('data_directory'),version()").fetchone()
            assert Path(actual).resolve()==data.resolve()
        print('ISOLATED',version.split(' on ')[0],flush=True)

        class RealPostgresRecoveryTests(SyncRecoveryTests):
            def setUp(self):
                self.store=fresh();self.report_id=seed(self.store)
                self.operation=self.store.pending_sync()[0]
                self.revision=self.store.get_report_detail(self.report_id)['revision']
            def restarted(self):
                return OperationsStore(database_url=self.store.database_url)

        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RealPostgresRecoveryTests))
        assert result.wasSuccessful()

        # Actual saved v21 initializer, followed by additive v22 migration.
        spec=importlib.util.spec_from_file_location('schema21_store',Path(args.schema21_source).resolve())
        legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
        assert legacy.SCHEMA_VERSION=='21'
        original_source=subprocess.check_output(['git','show','5495b12:operaciones_store.py'],cwd=ROOT)
        original_file=root/'schema20_store.py';original_file.write_bytes(original_source)
        original_spec=importlib.util.spec_from_file_location('schema20_store',original_file)
        original=importlib.util.module_from_spec(original_spec);original_spec.loader.exec_module(original)
        assert original.SCHEMA_VERSION=='20'
        target=fresh();published=original.OperationsStore(database_url=target.database_url)
        report_id=seed(published);published_pending=published.pending_sync()
        old=legacy.OperationsStore(database_url=target.database_url);old.initialize()
        assert old.pending_sync()==published_pending
        with old.connection() as conn:
            assert conn.execute("SELECT value FROM operations_meta WHERE key='schema_version'").fetchone()[0]=='21'
            assert conn.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_name='operations_evidence' AND column_name IN ('lease_token','lease_until')").fetchone()[0]==2
        print('PASS real PG: actual published schema20 -> schema21 migration preserves pending report and adds leases',flush=True)
        op=old.pending_sync()[0]
        old._sheet_delivery('sheets:migration-test',op['id'],'active')
        before=old.pending_sync()
        target.initialize()
        assert before==target.pending_sync()
        assert target.get_report_detail(report_id)['payload']['p1']=='120'
        state=target.sheet_delivery_status('migration-test')
        assert state['requires_confirmation'] and state['attempt_id']
        with target.connection() as conn:
            assert conn.execute("SELECT value FROM operations_meta WHERE key='schema_version'").fetchone()[0]==SCHEMA_VERSION
        print('PASS real PG: migration 21 -> '+SCHEMA_VERSION+' preserves report, outbox and uncertain intent',flush=True)

        # Concurrent initialization, from empty database, exercises schema21 DDL
        # (registry, photo leases and delivery gate) under the migration lock.
        target=fresh()
        procs=[child("import sys;from operaciones_store import OperationsStore;s=OperationsStore(database_url=sys.argv[1]);s.initialize();print('OK')",target.database_url) for _ in range(3)]
        for proc in procs:
            out,err=proc.communicate(timeout=30);assert proc.returncode==0,(out,err)
        target.initialize()
        print('PASS real PG: three processes migrate schema including v21 concurrently',flush=True)

        # Three distinct OS processes contend for the actual PG advisory lock.
        counter=root/'counter';counter.write_text('0')
        code="""import sys,time
from pathlib import Path
from operaciones_store import OperationsStore
s=OperationsStore(database_url=sys.argv[1]);s.initialize()
with s.resource_lock('shared-test'):
 p=Path(sys.argv[2]);n=int(p.read_text());time.sleep(.15);p.write_text(str(n+1))
"""
        procs=[child(code,target.database_url,counter) for _ in range(3)]
        for proc in procs:
            out,err=proc.communicate(timeout=30);assert proc.returncode==0,(out,err)
        assert counter.read_text()=='3'
        print('PASS real PG: three-process advisory lock serialization',flush=True)

        # A killed writer must release PG locks, retain uncertain intent, and roll
        # back a local report mutation. Restart the entire database afterwards.
        report_id=seed(target)
        code="""import sys
from operaciones_store import OperationsStore
s=OperationsStore(database_url=sys.argv[1]);s.initialize();op=s.pending_sync()[0]
with s.sheet_write_guard(op,'restart-test') as delivery:
 delivery['sent']=True
 delivery['connection'].execute("UPDATE operations_reports SET revision=999 WHERE id=%s",(op['entity_id'],))
 print('READY',flush=True);sys.stdin.readline()
"""
        proc=child(code,target.database_url);assert proc.stdout.readline().strip()=='READY'
        status=target.sheet_delivery_status('restart-test');assert status['in_flight']
        with target.connection() as conn:
            conn.execute("SET LOCAL lock_timeout='150ms'")
            try:
                conn.execute('UPDATE operations_reports SET revision=998 WHERE id=%s',(report_id,))
            except psycopg.errors.LockNotAvailable:
                conn.rollback()
            else:
                raise AssertionError('Entity row was not locked during delivery')
        proc.kill();proc.communicate(timeout=10)
        assert target.get_report_detail(report_id)['revision']!=999
        assert command('pg_ctl','-D',data,'-m','fast','-w','restart','-l',root/'postgres.log').returncode==0
        restarted=OperationsStore(database_url=target.database_url)
        state=restarted.sheet_delivery_status('restart-test')
        assert state['requires_confirmation'] and len(restarted.pending_sync())==1
        restarted.resolve_sheet_delivery('restart-test',state['operation_id'],attempt_id=state['attempt_id'],external_quiescent=True,actor_id='owner-test',actor_name='Pruebas')
        assert len(restarted.pending_sync())==1
        print('PASS real PG: FOR UPDATE, killed process rollback, database restart, retained pending changes and audited resume',flush=True)

        # Kill before any HTTP write: durable prepared phase must recover alone.
        code="""import sys
from operaciones_store import OperationsStore
s=OperationsStore(database_url=sys.argv[1]);s.initialize();op=s.pending_sync()[0]
def prepare():
 print('PREPARED',flush=True);sys.stdin.readline()
with s.sheet_write_guard(op,'prepared-crash',prepare=prepare):pass
"""
        proc=child(code,target.database_url);assert proc.stdout.readline().strip()=='PREPARED'
        proc.kill();proc.communicate(timeout=10)
        state=restarted.sheet_delivery_status('prepared-crash')
        assert state['state']=='idle' and state['last_recovery']['mode']=='automatic'
        assert len(restarted.pending_sync())==1
        print('PASS real PG: process killed before HTTP resumes automatically, with pending report preserved',flush=True)
    finally:
        for proc in children:
            if proc.poll() is None:proc.kill();proc.communicate(timeout=10)
        if started:
            stopped=command('pg_ctl','-D',data,'-m','fast','-w','stop')
            if stopped.returncode:raise RuntimeError('Temporary PostgreSQL did not stop: '+stopped.stderr)
        scratch.cleanup()


if __name__=='__main__':
    main()
