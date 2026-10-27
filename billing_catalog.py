"""Durable display catalog. Never stamps, cancels, sends email or infers payment.

Initial history is checkpointed by page. Later cycles read newest pages, current
pending requests, explicitly resolved pending items, and a rotating history window.
Published groups are replaced atomically only after a successful reconciliation.
"""
import json
import threading
import time
from contextlib import contextmanager
from urllib.parse import quote
from memory_diagnostics import memory_span

_BUDGET=threading.local()


class CatalogPreparing(RuntimeError):
    pass


class CatalogBudgetExceeded(TimeoutError):
    pass


@contextmanager
def network_budget(seconds=25):
    previous=getattr(_BUDGET,'deadline',None)
    _BUDGET.deadline=time.monotonic()+seconds
    try:yield
    finally:_BUDGET.deadline=previous


def bounded_timeout(timeout):
    deadline=getattr(_BUDGET,'deadline',None)
    if deadline is None:return timeout
    remaining=deadline-time.monotonic()
    if remaining<=0:raise CatalogBudgetExceeded('Catalog read budget exhausted')
    return min(timeout,8,remaining)


def pick(row, name, default=''):
    return next((v for k,v in row.items() if k.casefold()==name.casefold()),default)


def identity(row):
    return str(pick(row,'Id') or pick(row,'Uuid')).strip()


class Catalog:
    def __init__(self, app, store, scope, fetch, build, confirm_detail=None):
        self.app,self.store,self.scope,self.fetch,self.build=app,store,scope,fetch,build
        self.lock=threading.Lock()
        self.next_attempt=0
        self.confirm_detail=confirm_detail
        self.full_refresh_requested=False

    def state(self):
        self.store.initialize()
        with self.store.connection() as conn:
            row=conn.execute(f'SELECT payload_json FROM operations_billing_state WHERE scope={self.store.placeholder}',(self.scope,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_state(self, conn, state):
        p=self.store.placeholder
        conn.execute(f'INSERT INTO operations_billing_state(scope,payload_json) VALUES ({p},{p}) '
                     'ON CONFLICT(scope) DO UPDATE SET payload_json=excluded.payload_json',
                     (self.scope,json.dumps(state)))

    def rows(self):
        with self.store.connection() as conn:
            return [json.loads(row[0]) for row in conn.execute(
                f'SELECT payload_json FROM operations_billing_rows WHERE scope={self.store.placeholder}',(self.scope,)).fetchall()]

    def identities(self):
        status = "payload_json::jsonb->>'Status'" if self.store.dialect=='postgres' else "json_extract(payload_json,'$.Status')"
        with self.store.connection() as conn:
            return conn.execute(f'SELECT id,{status} FROM operations_billing_rows WHERE scope={self.store.placeholder}',(self.scope,)).fetchall()

    def save_page(self, rows, state):
        p=self.store.placeholder
        with self.store.connection() as conn:
            for row in rows:
                ident=identity(row)
                if ident:
                    conn.execute(f'INSERT INTO operations_billing_rows(scope,id,payload_json) VALUES ({p},{p},{p}) '
                                 'ON CONFLICT(scope,id) DO UPDATE SET payload_json=excluded.payload_json',
                                 (self.scope,ident,json.dumps({**row,'Status':pick(row,'Status')},ensure_ascii=False)))
            self.save_state(conn,state)

    def page(self, status, page):
        raw=self.fetch('GET','/api/cfdi',params={'type':'issued','status':status,'page':page},timeout=bounded_timeout(8))
        if isinstance(raw,list):return raw
        if isinstance(raw,dict):
            rows=pick(raw,'Data',None)
            if rows is None:rows=pick(raw,'Items',None)
            if isinstance(rows,list):return rows
        raise ValueError('Unexpected provider catalog response')

    def read(self, *, refresh=False, customer_tax_id=None):
        state=self.state()
        if refresh:self.full_refresh_requested=True
        if self.full_refresh_requested or not state.get('backfill_complete') or time.time()-state.get('updated_at',0)>180:
            self.schedule()
        if not state.get('published'):
            raise CatalogPreparing('Preparando el historial de documentos de Facturama. Intenta nuevamente en unos segundos; no se está emitiendo ningún comprobante.')
        p=self.store.placeholder
        condition=f' AND id={p}' if customer_tax_id else ''
        params=(self.scope,customer_tax_id) if customer_tax_id else (self.scope,)
        with self.store.connection() as conn:
            groups=[json.loads(row[0]) for row in conn.execute(
                f'SELECT payload_json FROM operations_billing_groups WHERE scope={p}'+condition,params).fetchall()]
        groups.sort(key=lambda row:(row['pending_complements']==0,-row['pending_total'],row['name'].casefold()))
        info=dict(state.get('payment_sync') or {})
        info['catalog']=dict(updated_at=state.get('updated_at'),refreshing=self.lock.locked(),
                             stale=bool(state.get('error')) or not state.get('backfill_complete') or time.time()-state.get('updated_at',0)>180,
                             history_checked_at=state.get('history_checked_at'),
                             error='No se pudo actualizar Facturama; se conserva la última información.' if state.get('error') else '')
        return groups,info

    def schedule(self):
        if time.monotonic()<self.next_attempt or not self.lock.acquire(blocking=False):return False
        try:
            threading.Thread(target=self.run,name='hsc-billing-catalog',daemon=True).start()
        except Exception:
            self.lock.release()
            raise
        return True

    def run(self):
        try:
            with self.app.app_context(),memory_span('billing_catalog'),self.store.resource_lock('billing-catalog:'+self.scope,timeout=.1),network_budget():
                state=self.state()
                if self.full_refresh_requested:
                    self.full_refresh_requested=False
                    state.update(backfill_complete=False,backfill_page=0)
                    self.save_page([],state)
                if not state.get('backfill_complete'):
                    self.backfill(state)
                self.incremental(state)
                groups,payment_sync=self.build(self.rows())
                p=self.store.placeholder
                # Readers see the previous complete publication until this commits.
                with self.store.connection() as conn:
                    conn.execute(f'DELETE FROM operations_billing_groups WHERE scope={p}',(self.scope,))
                    for group in groups:
                        conn.execute(f'INSERT INTO operations_billing_groups(scope,id,payload_json) VALUES ({p},{p},{p})',
                                     (self.scope,group['key'],json.dumps(group,ensure_ascii=False)))
                    state.update(published=True,updated_at=time.time(),payment_sync=payment_sync,error='')
                    self.save_state(conn,state)
        except Exception as exc:
            # Do not expose provider payloads or credentials through the UI.
            try:
                state=self.state();state['error']=type(exc).__name__
                self.save_page([],state)
            except Exception:
                pass
            self.next_attempt=time.monotonic()+(2 if isinstance(exc,CatalogBudgetExceeded) else 30)
            self.app.logger.warning('El catálogo de documentos no pudo actualizarse (%s)',type(exc).__name__)
        finally:
            self.lock.release()

    def backfill(self, state):
        previous=None
        # Checkpoint every page, keeping network data bounded to one page.
        # The next attempt resumes rather than discarding downloaded history.
        for _ in range(100):
            page=int(state.get('backfill_page',0));rows=self.page('all',page)
            if not rows:
                state.update(backfill_complete=True,history_page=0,history_checked_at=time.time())
                self.save_page([],state)
                return
            signature=tuple(identity(row) for row in rows)
            if signature==previous:raise RuntimeError('Provider repeated a history page')
            previous=signature
            state['backfill_page']=page+1
            self.save_page(rows,state)
        raise RuntimeError('History checkpoint reached; resume on next attempt')

    def incremental(self, state):
        identities=self.identities()
        known={row[0] for row in identities}
        previous_pending={row[0] for row in identities if str(row[1]).lower() in {'pending','requested'}}
        del identities
        # Fetch current requests without a date restriction: old invoices count too.
        pending=[];seen_pages=set()
        for page in range(20):
            rows=self.page('pending',page)
            if not rows:break
            signature=tuple(identity(row) for row in rows)
            if signature in seen_pages:raise RuntimeError('Provider repeated a pending page')
            seen_pages.add(signature)
            pending.extend({**row,'Status':'pending'} for row in rows)
        else:raise RuntimeError('Pending history incomplete')
        pending_ids={identity(row) for row in pending}
        # Stop once a whole page is already known, without discarding older rows.
        start=int(state.get('newest_page',0))
        if start:
            self.save_page(self.page('all',0),state)
        for page in range(start,start+10):
            rows=self.page('all',page)
            if not rows:
                state['newest_page']=0;self.save_page([],state);break
            state['newest_page']=page+1
            self.save_page(rows,state)
            if all(identity(row) in known for row in rows):
                state['newest_page']=0;self.save_page([],state);break
        else:raise CatalogBudgetExceeded('New documents checkpoint saved; continue next cycle')
        # A bounded rotating historical window also detects external cancellations
        # that skipped pending or affected old documents. Never assume active.
        for _ in range(2):
            page=int(state.get('history_page',0));rows=self.page('all',page)
            state['history_page']=page+1 if rows else 0
            if not rows:state['history_checked_at']=time.time()
            self.save_page(rows,state)
            if not rows:break
        for ident in previous_pending-pending_ids:
            raw=self.fetch('GET','/api/cfdi/'+quote(ident,safe=''),params={'type':'issued'},timeout=bounded_timeout(8))
            if not isinstance(raw,dict) or identity(raw)!=ident or not pick(raw,'Status'):
                raise ValueError('Pending resolution not confirmed')
            if self.confirm_detail:self.confirm_detail(raw)
            self.save_page([raw],state)
        # The explicit pending filter wins over stale active rows in general pages.
        self.save_page(pending,state)


def controller(app,store,scope,fetch,build,confirm_detail=None):
    with _CONTROLLER_LOCK:
        value=app.extensions.get('hsc_billing_catalog')
        if value is None or value.scope!=scope or value.store is not store:
            value=Catalog(app,store,scope,fetch,build,confirm_detail)
            app.extensions['hsc_billing_catalog']=value
        return value


_CONTROLLER_LOCK=threading.Lock()
