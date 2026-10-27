import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from flask import Flask
from operaciones_store import OperationsStore
from billing_catalog import Catalog,CatalogPreparing,network_budget,bounded_timeout,CatalogBudgetExceeded


class BillingCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=OperationsStore(local_path=Path(self.temp.name)/'catalog.sqlite3')
        self.app=Flask(__name__)
        self.pages=[[{'Id':'new','Receiver':{'Rfc':'A'},'Status':'active'}],
                    [{'Id':'middle','Receiver':{'Rfc':'B'},'Status':'active'}],
                    [{'Id':'old','Receiver':{'Rfc':'A'},'Status':'active'}]]
        self.pending=[];self.details={};self.calls=[];self.fail_page=None;self.confirmed=[]
        self.catalog=Catalog(self.app,self.store,'account-A',self.fetch,self.build,self.confirmed.append)

    def fetch(self,method,path,*,params,timeout):
        self.assertEqual(method,'GET');self.assertLessEqual(timeout,8)
        self.calls.append((path,dict(params)))
        if path!='/api/cfdi':return self.details[path.rsplit('/',1)[1]]
        status,page=params['status'],params['page']
        if (status,page)==self.fail_page:raise TimeoutError('PRIVATE_PROVIDER_DETAIL')
        if status=='pending':return self.pending if page==0 else []
        return self.pages[page] if page<len(self.pages) else []

    @staticmethod
    def build(rows):
        groups={}
        for row in rows:
            key=row['Receiver']['Rfc']
            group=groups.setdefault(key,dict(key=key,rfc=key,name=key,pending_complements=0,pending_total=0,invoices=[]))
            group['invoices'].append(row)
        return list(groups.values()),{'imported':0}

    def sync(self):
        self.assertTrue(self.catalog.lock.acquire(blocking=False))
        self.catalog.run()

    def read(self, **kwargs):
        with patch.object(self.catalog,'schedule',return_value=False):return self.catalog.read(**kwargs)

    def test_cold_catalog_is_not_reported_as_empty_history(self):
        with self.assertRaises(CatalogPreparing):self.read()
        self.assertEqual(self.calls,[])

    def test_publish_restart_and_customer_scoping_without_provider_calls(self):
        self.sync();self.calls.clear()
        groups,info=self.read(customer_tax_id='A')
        self.assertEqual([g['key'] for g in groups],['A'])
        self.assertEqual({r['Id'] for r in groups[0]['invoices']},{'new','old'})
        self.assertTrue(info['catalog']['updated_at'])
        self.assertEqual(self.calls,[])
        restarted=Catalog(self.app,OperationsStore(local_path=self.store.local_path),'account-A',self.fetch,self.build)
        with patch.object(restarted,'schedule',return_value=False):self.assertEqual(len(restarted.read()[0]),2)
        other=Catalog(self.app,self.store,'different-account',self.fetch,self.build)
        with patch.object(other,'schedule',return_value=False),self.assertRaises(CatalogPreparing):other.read()

    def test_failed_history_resumes_checkpoint_without_partial_publication(self):
        self.fail_page=('all',1);self.sync()
        self.assertEqual(self.catalog.state()['backfill_page'],1)
        with self.assertRaises(CatalogPreparing):self.read()
        self.fail_page=None;self.calls.clear();self.sync()
        self.assertEqual(self.calls[0],('/api/cfdi',{'type':'issued','status':'all','page':1}))
        self.assertEqual(len(self.read()[0]),2)

    def test_incremental_keeps_old_documents_and_checks_rotating_history(self):
        self.sync();self.calls.clear()
        self.pages[2][0]={**self.pages[2][0],'Status':'canceled'}
        self.sync()
        groups,_=self.read(customer_tax_id='A')
        self.assertEqual(next(row for row in groups[0]['invoices'] if row['Id']=='old')['Status'],'canceled')
        all_pages=[params['page'] for _,params in self.calls if params.get('status')=='all']
        self.assertNotIn(1,all_pages,'Do not rescan all historical pages on each update')

    def test_error_retains_last_complete_publication_and_reports_stale(self):
        self.sync();before=self.read()[0]
        self.fail_page=('pending',0);self.sync()
        groups,info=self.read()
        self.assertEqual(groups,before)
        self.assertTrue(info['catalog']['stale'])
        self.assertNotIn('PRIVATE_PROVIDER_DETAIL',str(info))

    def test_large_new_batch_resumes_without_publishing_a_partial_catalog(self):
        self.sync()
        self.pages=[[{'Id':f'new-{i}','Receiver':{'Rfc':'A'},'Status':'active'}] for i in range(12)]+self.pages
        self.sync()
        self.assertEqual(self.catalog.state()['newest_page'],10)
        self.assertEqual(sum(len(g['invoices']) for g in self.read()[0]),3)
        self.sync()
        self.assertEqual(sum(len(g['invoices']) for g in self.read()[0]),15)

    def test_manual_refresh_rechecks_complete_history_without_hiding_old_groups(self):
        self.sync();self.calls.clear()
        groups,_=self.read(refresh=True)
        self.assertTrue(groups)
        self.sync()
        self.assertIn(('/api/cfdi',{'type':'issued','status':'all','page':1}),self.calls)
        self.assertTrue(self.catalog.state()['history_checked_at'])

    def test_unknown_detail_status_is_not_confirmed_as_active(self):
        import facturacion_bp as billing
        with self.app.app_context(),patch.object(billing,'_persist_cancellation_state') as persist:
            with self.assertRaises(ValueError):billing._confirm_catalog_cancellation({'Id':'old','Status':'unknown'})
            persist.assert_not_called()

    def test_absent_pending_is_resolved_by_explicit_detail_not_assumed_active(self):
        self.pending=[self.pages[2][0]];self.sync()
        self.pending=[];self.details['old']={**self.pages[2][0],'Status':'canceled'}
        self.sync()
        self.assertEqual(self.confirmed[-1]['Status'],'canceled')
        self.assertIn(('/api/cfdi/old',{'type':'issued'}),self.calls)

    def test_only_one_refresh_can_be_scheduled(self):
        with patch('billing_catalog.threading.Thread') as thread:
            self.assertTrue(self.catalog.schedule());self.assertFalse(self.catalog.schedule())
            self.assertEqual(thread.call_count,1)
        self.catalog.lock.release()

    def test_get_budget_does_not_change_calls_outside_catalog(self):
        self.assertEqual(bounded_timeout(60),60)
        with network_budget(5):self.assertLessEqual(bounded_timeout(60),5)
        with network_budget(-1),self.assertRaises(CatalogBudgetExceeded):bounded_timeout(60)


if __name__=='__main__':unittest.main()
