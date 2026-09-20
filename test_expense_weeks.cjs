const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('templates/app_operativa_demo.html','utf8');
const start=source.indexOf('  let expensePage=0;'),end=source.indexOf('  async function openExpenseForm()',start);
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{value:'',innerHTML:'',textContent:'',hidden:false,parentElement:{},nextElementSibling:{}});return elements.get(id)};
const rows=[{id:'a',user_id:'T1',technician_name:'Uno',expense_date:'2026-09-14',amount:100,status:'Pendiente',reimbursable:true},
{id:'b',user_id:'T2',technician_name:'Dos',expense_date:'2026-09-19',amount:200,status:'Liquidado',reimbursable:true},
{id:'old',user_id:'T1',technician_name:'Uno',expense_date:'2026-09-12',amount:50,status:'Pendiente',reimbursable:true}];
const context={$,$$:()=>[],visibleExpenses:()=>rows,account:{isOwner:true},localDate:s=>new Date(s+'T12:00:00'),dateKey:d=>d.toISOString().slice(0,10),money:n=>String(n),escapeHtml:s=>String(s||''),clientName:()=>'',equipment:[],expenseStatusClass:()=>'',openExpenseDetail:()=>{}};
vm.createContext(context);vm.runInContext(source.slice(start,end),context);
assert.deepEqual(Array.from(vm.runInContext("expenseWeekBounds('2026-09-19')",context)),['2026-09-13','2026-09-19']);
assert.deepEqual(Array.from(vm.runInContext("expenseWeekBounds('2026-09-20')",context)),['2026-09-20','2026-09-26']);
assert.deepEqual(Array.from(vm.runInContext("expenseWeekBounds('2027-01-01')",context)),['2026-12-27','2027-01-02']);
$('#expensePeriod').value='history';$('#expenseWeekDate').value='2026-09-19';
vm.runInContext('renderExpenses()',context);
assert.equal($('#expenseMonthTotal').textContent,'300');assert.equal($('#expensePendingTotal').textContent,'100');assert.equal($('#expensePaidTotal').textContent,'200');
assert($('#expensePeriodLabel').textContent.includes('pago el sábado'));
assert($('#expenseOtherDebt').textContent.includes('50'));assert($('#expenseList').innerHTML.includes('Selecciona un técnico'));
$('#expenseTechnicianFilter').value='T1';vm.runInContext('renderExpenses()',context);
assert.equal($('#expenseMonthTotal').textContent,'100');assert(!$('#expenseList').innerHTML.includes('data-expense-id="old"'));
for(let i=0;i<20;i++)rows.push({...rows[0],id:'more'+i});
vm.runInContext('renderExpenses()',context);
assert.equal(($('#expenseList').innerHTML.match(/data-expense-id=/g)||[]).length,10);
console.log('OK: semanas, año nuevo, técnico, deuda anterior y páginas de 10.');
