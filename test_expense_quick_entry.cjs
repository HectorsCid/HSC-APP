const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('templates/app_operativa_demo.html','utf8'),storage=new Map(),messages=[];
const notify=html.slice(html.indexOf('function announcePendingExpenses()'),html.indexOf("let profilePhotoUrl="));
function refresh(rows){
  const ctx={account:{id:'owner',isOwner:true},matrixExpenses:rows,localStorage:storage,window:{},money:String,toast:m=>messages.push(m)};
  storage.getItem=storage.get;storage.setItem=storage.set;
  vm.runInNewContext(notify+';announcePendingExpenses()',ctx);
}
const row={id:'one',technician_name:'Erick',amount:400,status:'Pendiente'};
refresh([row]);for(let i=0;i<4;i++){refresh([]);refresh([row]);}
assert.equal(messages.length,1);refresh([row,{...row,id:'two'}]);assert.equal(messages.length,2);
const start=html.indexOf("$('#saveExpense').onclick="),end=html.indexOf('async function uploadExpenseReceipt',start);
const nodes={},$=s=>nodes[s]??=( {} ),jobs=[];
const ctx={$,account:{id:'erick',name:'Erick'},crypto:{randomUUID:()=> 'expense-id'},queueFieldAction:async j=>jobs.push(j),toast(){},drainFieldActions(){}};
vm.runInNewContext(html.slice(start,end),ctx);
Object.assign($('#expenseReceipt'),{files:[]});$('#expenseDate').value='2026-11-24';$('#expenseAmount').value='400';$('#expenseConcept').value='Gasolina';$('#expensePayment').value='Dinero propio';$('#expenseModal').classList={remove(){}};
$('#expenseTechnician').selectedOptions=[];
(async()=>{await $('#saveExpense').onclick();assert.equal(jobs.length,1);assert.equal(jobs[0].receipt,false);assert.equal(jobs[0].body.reimbursable,true);assert.equal(jobs[0].body.expense_date,'2026-11-24');
$('#expensePayment').value='Tarjeta HSC';$('#expenseReceipt').files=[{type:'image/jpeg',size:120}];await $('#saveExpense').onclick();assert.equal(jobs[1].receipt,true);assert.equal(jobs[1].body.reimbursable,false);})().catch(err=>{console.error(err);process.exitCode=1;});
