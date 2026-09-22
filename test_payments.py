import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from flask import Flask, jsonify, session
from operaciones_store import OperationsStore
from payment_bp import create_payment_blueprint
import operation_payments as pay


class PaymentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = OperationsStore(local_path=Path(tmp.name)/'pay.db')
        self.store.initialize()
        with self.store.connection() as conn:
            for uid, role in [('T1','technician'), ('T2','technician'), ('C1','client')]:
                conn.execute('INSERT INTO operations_users(id,name,role,email,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                             (uid, uid+' nombre', role, uid+'@test.invalid','2026-09-01','2026-09-01'))
        self.salary = dict(action='salary',mutation_id='salary-0001',expected_revision=0,weekly_amount='2000',effective_week='2026-09-13')
        self.payment = dict(action='payment',mutation_id='payment-0001',expected_revision=1,amount='500',paid_on='2026-09-19',week_start='2026-09-13',note='Prueba')

    def salary_on(self):
        return pay.mutate(self.store,'T1',self.salary,'owner',date(2026,9,14))

    def read(self, day=date(2026,9,20)):
        return pay.detail(self.store,'T1',day)

    def expense(self, ident, day, amount, status='Pendiente', reimbursable=True, user='T1'):
        return self.store.save_expense(dict(id=ident,user_id=user,technician_name=user,expense_date=day,amount=amount,
                                            status=status,reimbursable=reimbursable,concept='Gasto de prueba'))

    def test_all_technicians_even_without_expenses_and_no_default_salary(self):
        rows=pay.listing(self.store,date(2026,9,20))
        self.assertEqual([r['id'] for r in rows],['T1','T2'])
        self.assertEqual(sum(r['due_cents'] for r in rows),0)
        with self.store.connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM operations_pay_accounts').fetchone()[0],0)

    def test_deleted_technician_with_settled_expense_is_not_listed(self):
        self.expense('old', '2026-09-12', 100, status='Liquidado', user='T2')
        self.assertTrue(self.store.delete_user_account('T2'))
        self.assertEqual([r['id'] for r in pay.listing(self.store, date(2026,9,20))], ['T1'])
        self.assertEqual(pay.detail(self.store, 'T2', date(2026,9,20))['due_cents'], 0)

    def test_deleted_technician_with_unpaid_expense_remains_visible(self):
        self.expense('old', '2026-09-12', 100, user='T2')
        self.assertTrue(self.store.delete_user_account('T2'))
        rows = pay.listing(self.store, date(2026,9,20))
        self.assertEqual(rows[0]['id'], 'T2')
        self.assertEqual(rows[0]['due_cents'], 10000)

    def test_sunday_never_resets_expense_debt(self):
        self.expense('old','2026-09-12',123.45)
        self.expense('recent','2026-09-19',100,'Aprobado')
        self.expense('paid','2026-09-18',200,'Liquidado')
        self.expense('rejected','2026-09-18',300,'Rechazado')
        self.expense('company','2026-09-18',900,reimbursable=False)
        self.expense('other','2026-09-18',700,user='T2')
        for day in [date(2026,9,19),date(2026,9,20),date(2026,10,1)]:
            self.assertEqual(self.read(day)['due_cents'],22345)
        self.assertEqual(self.read()['weeks'][0]['start'],'2026-09-20')

    def test_salary_due_saturday_not_earlier_or_double_counted_sunday(self):
        self.salary_on()
        self.assertEqual(self.read(date(2026,9,18))['salary_due_cents'],0)
        self.assertEqual(self.read(date(2026,9,18))['projected_salary_cents'],200000)
        self.assertEqual(self.read(date(2026,9,19))['salary_due_cents'],200000)
        self.assertEqual(self.read()['salary_due_cents'],200000)
        self.assertEqual(self.read(date(2026,9,26))['salary_due_cents'],400000)

    def test_partial_payment_retry_then_final_payment(self):
        self.salary_on()
        self.expense('old','2026-09-12',250)
        result=pay.mutate(self.store,'T1',self.payment,'owner',date(2026,9,20))
        self.assertFalse(result['duplicate'])
        self.assertTrue(pay.mutate(self.store,'T1',self.payment,'owner',date(2026,9,20))['duplicate'])
        self.assertEqual(self.read()['due_cents'],175000)
        pay.mutate(self.store,'T1',{**self.payment,'mutation_id':'payment-0002','expected_revision':2,'amount':'1500'},'owner',date(2026,9,20))
        self.assertEqual(self.read()['due_cents'],25000)
        self.assertEqual(self.store.snapshot()['expenses'][0]['status'],'Pendiente')

    def test_same_mutation_different_amount_or_actor_rejected(self):
        self.salary_on()
        pay.mutate(self.store,'T1',self.payment,'owner',date(2026,9,20))
        for change in [dict(amount='600'),dict(note='Otra')]:
            with self.assertRaises(pay.Conflict):
                pay.mutate(self.store,'T1',{**self.payment,**change},'owner',date(2026,9,20))
        with self.assertRaises(pay.Conflict):
            pay.mutate(self.store,'T1',self.payment,'admin2',date(2026,9,20))

    def test_rate_change_keeps_previous_debt_and_zero_stops_future(self):
        self.salary_on()
        pay.mutate(self.store,'T1',{**self.salary,'mutation_id':'salary-0002','expected_revision':1,'effective_week':'2026-09-20','weekly_amount':'2500'},'owner',date(2026,9,20))
        self.assertEqual(self.read()['salary_due_cents'],200000)
        self.assertEqual(self.read(date(2026,9,26))['salary_due_cents'],450000)
        pay.mutate(self.store,'T1',{**self.salary,'mutation_id':'salary-0003','expected_revision':2,'effective_week':'2026-09-27','weekly_amount':'0'},'owner',date(2026,9,27))
        self.assertEqual(self.read(date(2026,10,10))['salary_due_cents'],450000)

    def test_cannot_rewrite_due_salary_or_backdate_initial_salary(self):
        self.salary_on()
        with self.assertRaises(ValueError):
            pay.mutate(self.store,'T1',{**self.salary,'mutation_id':'salary-0002','expected_revision':1},'owner',date(2026,9,19))
        with self.assertRaises(ValueError):
            pay.mutate(self.store,'T2',self.salary,'owner',date(2026,9,20))
        self.assertEqual(self.read()['revision'],1)

    def test_disable_salary_cancels_all_scheduled_raises_but_keeps_debt(self):
        self.salary_on()
        for revision, start, amount in [(1,'2026-09-27','2500'),(2,'2026-10-04','3000')]:
            pay.mutate(self.store,'T1',{**self.salary,'mutation_id':'schedule-'+str(revision),
                'expected_revision':revision,'effective_week':start,'weekly_amount':amount},'owner',date(2026,9,20))
        stop={**self.salary,'mutation_id':'stop-salary','expected_revision':3,'effective_week':'2026-09-20','weekly_amount':'0'}
        pay.mutate(self.store,'T1',stop,'owner',date(2026,9,20))
        self.assertTrue(pay.mutate(self.store,'T1',stop,'owner',date(2026,9,20))['duplicate'])
        account=self.read(date(2026,11,1))
        self.assertEqual(account['salary_due_cents'],200000)
        self.assertEqual([p['amount_cents'] for p in account['plans']],[200000,0])
        import json
        with self.store.connection() as conn:
            audit=json.loads(conn.execute("SELECT payload_json FROM operations_pay_changes WHERE id='stop-salary'").fetchone()[0])
            self.assertEqual([p['amount_cents'] for p in audit['cancelled_plans']],[250000,300000])

    def test_explicit_reactivation_after_stopping_is_allowed(self):
        self.salary_on()
        pay.mutate(self.store,'T1',{**self.salary,'mutation_id':'stop-salary','expected_revision':1,
            'effective_week':'2026-09-20','weekly_amount':'0'},'owner',date(2026,9,20))
        pay.mutate(self.store,'T1',{**self.salary,'mutation_id':'restart-salary','expected_revision':2,
            'effective_week':'2026-09-27','weekly_amount':'2500'},'owner',date(2026,9,27))
        self.assertEqual(self.read(date(2026,10,3))['salary_due_cents'],450000)

    def test_invalid_amounts_dates_future_and_overpayment(self):
        for value in ['NaN','Infinity','-1','1.001',True,None,'10000001']:
            with self.subTest(value=value),self.assertRaises(ValueError):
                pay.cents(value)
        self.salary_on()
        for updates in [dict(amount='2000.01'),dict(amount='0'),dict(paid_on='2026-09-21'),dict(paid_on='2026-09-18'),dict(week_start='2026-09-20')]:
            with self.subTest(updates=updates),self.assertRaises(ValueError):
                pay.mutate(self.store,'T1',{**self.payment,**updates},'owner',date(2026,9,20))
        self.assertEqual(self.read()['revision'],1)

    def test_concurrent_payments_and_lost_ack(self):
        self.salary_on()
        def send(index):
            try:
                return pay.mutate(self.store,'T1',{**self.payment,'mutation_id':'parallel-'+str(index),'amount':'1500'},'owner',date(2026,9,20))
            except pay.Conflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(send,[1,2]))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(self.read()['salary_due_cents'],50000)

    def test_void_restores_salary_and_preserves_audit(self):
        self.salary_on()
        pay.mutate(self.store,'T1',self.payment,'owner',date(2026,9,20))
        body=dict(action='void',mutation_id='void-00001',expected_revision=2,payment_id='payment-0001',reason='Registro equivocado')
        pay.mutate(self.store,'T1',body,'owner',date(2026,9,20))
        self.assertTrue(pay.mutate(self.store,'T1',body,'owner',date(2026,9,20))['duplicate'])
        account=self.read()
        self.assertEqual(account['salary_due_cents'],200000)
        payment=next(w for w in account['weeks'] if w['start']=='2026-09-13')['payments'][0]
        self.assertEqual(payment['amount_cents'],50000)
        self.assertEqual(payment['void_reason'],'Registro equivocado')

    def test_pagination_total_includes_older_weeks_and_year_boundary(self):
        self.salary_on()
        account=self.read(date(2027,2,1))
        self.assertEqual(len(account['weeks']),12)
        self.assertEqual(account['next_offset'],12)
        older=pay.detail(self.store,'T1',date(2027,2,1),12)
        self.assertEqual(account['due_cents'],older['due_cents'])
        self.assertTrue(set(w['start'] for w in account['weeks']).isdisjoint(w['start'] for w in older['weeks']))
        self.assertEqual(pay.week_start(date(2027,1,1)),date(2026,12,27))

    def test_salary_history_prevents_destructive_user_deletion(self):
        self.salary_on()
        with self.assertRaises(ValueError):
            self.store.delete_user_account('T1')
        self.assertTrue(self.store.delete_user_account('T2'))

    def test_client_cannot_have_salary(self):
        with self.assertRaises(ValueError):
            pay.mutate(self.store,'C1',self.salary,'owner',date(2026,9,14))

    def test_endpoints_are_admin_only_and_not_cached(self):
        app=Flask(__name__);app.secret_key='test'
        def forbidden(*roles):
            if session.get('role') not in roles:return jsonify(ok=False,error='Sin permiso'),403
        app.register_blueprint(create_payment_blueprint(self.store,forbidden))
        client=app.test_client()
        for role in ['', 'technician','client']:
            with client.session_transaction() as s:s['role']=role
            for method,path in [('get','/api/operaciones/payments'),('get','/api/operaciones/payments/T1'),('post','/api/operaciones/payments/T1')]:
                self.assertEqual(getattr(client,method)(path).status_code,403)
        with client.session_transaction() as s:s['role']='admin'
        result=client.get('/api/operaciones/payments')
        self.assertEqual(result.status_code,200)
        self.assertIn('no-store',result.headers['Cache-Control'])
        self.assertEqual(client.post('/api/operaciones/payments/T1',json=[]).status_code,400)
        self.assertNotIn('pay_accounts',self.store.snapshot())


if __name__=='__main__':unittest.main()
