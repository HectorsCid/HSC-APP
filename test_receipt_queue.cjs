const assert=require('node:assert/strict');
const queue=require('./static/receipt_queue.js');
(async()=>{
  for(const [bytes,type,ext] of [[[255,216,255,224],'image/jpeg','jpg'],[[137,80,78,71,13,10,26,10],'image/png','png'],[Array.from(Buffer.from('RIFF0000WEBP')),'image/webp','webp']]){
    const original=new Blob([new Uint8Array(bytes)]),prepared=await queue.prepare(original);
    assert.equal(prepared.name,'comprobante.'+ext);assert.equal(prepared.blob.type,type);
    const body=await queue.form(structuredClone(prepared));assert.equal(body.get('file').name,prepared.name);assert.equal(body.get('file').size,original.size);
    assert.deepEqual(new Uint8Array(await original.arrayBuffer()),new Uint8Array(bytes));
  }
  for(const invalid of [null,{},new Blob([]),new Blob(['not a photo']),new Blob([new Uint8Array(15*1024*1024+1)])])await assert.rejects(queue.prepare(invalid),error=>error.receiptRepair&&error.status===400);
  assert.equal((await queue.prepare({blob:new Blob([new Uint8Array([255,216])]),name:'foto.jpg'})).name,'foto.jpg');
  console.log('OK: comprobantes restaurados sin nombre, formatos y validación sin eliminar originales.');
})().catch(error=>{console.error(error);process.exitCode=1});
