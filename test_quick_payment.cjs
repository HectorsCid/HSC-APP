const assert=require('node:assert/strict');
const {execute,today,contactOptions}=require('./static/quick_payment.js');
(async()=>{
  assert.match(today(),/^\d{4}-\d{2}-\d{2}$/);
  assert.deepEqual(contactOptions({complementos:'rep@example.com',cuentas_pagar:'PAGOS@example.com; aux@example.com',contactos:[{nombre:'Pagos',correo:'pagos@example.com'},{nombre:'REP',correo:'REP@example.com'}]}),[
    {email:'rep@example.com',label:'Preferido para complementos'},{email:'pagos@example.com',label:'Pagos'},{email:'aux@example.com',label:'Cuentas por pagar'}
  ]);
  const state={invoice_id:'invoice',rfc:'RFC',amount:'120',date:'2026-11-09',folio:'42'};
  const fields={email:'pagos@example.com',cc:'conta@example.com',payment_form:'03',send_key:'test'};
  const calls=[];
  const request=async(url,body)=>{
    calls.push({url,body});
    if(url==='/api/pagos/crear-rapido')return {id:'new-rep',uuid:'rep-uuid'};
    if(calls.length===2)throw Error('Correo no confirmado');
    return {ok:true};
  };
  await assert.rejects(execute(state,fields,request),/Correo no confirmado/);
  assert.equal(state.rep.id,'new-rep');
  await execute(state,{...fields,email:'otro@example.com'},request);
  assert.deepEqual(calls.map(c=>c.url),['/api/pagos/crear-rapido','/api/invoices/new-rep/email','/api/invoices/new-rep/email']);
  assert.equal(calls[0].body.expected_date,'2026-11-09');
  assert.equal(calls[2].body.email,'otro@example.com');
  assert.equal(calls[2].body.scheduled_for,undefined);
  console.log('OK: si falla el correo se reenvía el REP ya emitido, sin volver a timbrar.');
})().catch(error=>{console.error(error);process.exitCode=1;});
