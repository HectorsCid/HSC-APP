const assert=require('node:assert/strict'),{report}=require('./static/pending_actions_audit.js');
const input=[{id:'x',createdAt:2,url:'/api/operaciones/equipment',entityPhoto:'equipment',entitySavedResult:{secret:'hidden'},photoBlocked:true,body:{items:[{id:'UDA47',name:'Private name'}],password:'secret'},files:[new Blob(['photo'])],error:'Bad photo'},
{id:'y',createdAt:1,url:'/api/operaciones/expenses',body:{id:'G1',amount:150,concept:'Private concept',send_key:'secret'}}];
const result=report(input),serialized=JSON.stringify(result);
assert.equal(result.total,2);assert.deepEqual(result.counts,{'Gasto':1,'Equipo':1});
assert.equal(result.actions[1].recordConfirmed,true);assert.equal(result.actions[1].photos[0].readable,true);
for(const secret of ['Private name','Private concept','send_key','password','hidden'])assert(!serialized.includes(secret));
assert.equal(input[0].id,'x');assert.equal(input[0].files[0].size,5);
console.log('OK: diagnóstico de solo lectura, ordenado, sin contenido de fotos ni cuerpos privados.');
