const assert=require('node:assert/strict');
const fs=require('node:fs');
const pay=require('./static/operations_payments.js');
assert.equal(pay.week('2026-09-19'),'2026-09-13');
assert.equal(pay.week('2026-09-20'),'2026-09-20');
assert.equal(pay.week('2027-01-01'),'2026-12-27');
const rows=[{amount:123.45,status:'Pendiente',reimbursable:true,expense_date:'2026-09-12'},
{amount:100,status:'Aprobado',reimbursable:true,expense_date:'2026-09-19'},
{amount:200,status:'Liquidado',reimbursable:true},{amount:200,status:'Reembolsado',reimbursable:true},
{amount:200,status:'Rechazado',reimbursable:true},{amount:200,status:'Pendiente',reimbursable:false}];
assert.equal(pay.expenseBalance(rows),22345);
rows[0].status='Liquidado';assert.equal(pay.expenseBalance(rows),10000);
assert.equal(pay.mount({account:{isOwner:false}}),null);
const page=fs.readFileSync('templates/app_operativa_demo.html','utf8');
assert(page.includes("if(view==='payAccount')return 'payments'"));
assert(page.includes("if(view==='payments')return 'profile'"));
assert(page.includes("$('#openExpenses').onclick=()=>showView('payments')"));
console.log('Pagos: deuda acumulada, centavos, domingos y acceso administrativo OK');
