const assert=require('node:assert/strict'),{create}=require('./static/photo_memory_cache.js');
(async()=>{
  let calls=0,revoked=[],fail=false;
  const cache=create({maxEntries:2,maxBytes:10,urlApi:{createObjectURL:()=>`blob:${calls}`,revokeObjectURL:url=>revoked.push(url)},fetch:async()=>{calls++;if(fail)throw Error('offline');return {ok:true,headers:{get:()=> 'image/webp'},blob:async()=>new Blob(['1234'])};}});
  const [first,duplicate]=await Promise.all([cache.get('a'),cache.get('a')]);assert.equal(first,duplicate);assert.equal(calls,1);
  assert.equal(await cache.get('a'),first);assert.equal(calls,1);
  await cache.get('b');await cache.get('c');assert.deepEqual(revoked,[first]);
  fail=true;await assert.rejects(cache.get('d'));fail=false;await cache.get('d');assert.equal(calls,5);
  console.log('OK: miniaturas reutilizadas, solicitudes simultáneas agrupadas y caché acotada.');
})().catch(error=>{console.error(error);process.exitCode=1;});
