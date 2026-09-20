const assert=require('node:assert/strict');
const {createJournal}=require('./static/operations_payments.js');

// Transaction model for failure/race tests. The end-to-end browser check also
// closes/reopens a real tab to exercise its actual persistent IndexedDB.
function indexedFixture(){
  const rows=new Map();let tail=Promise.resolve();
  const db={createObjectStore(){},close(){},transaction(){
    let readRequest,readKey,result,aborted=false;
    const writes=[];
    const tx={abort(){aborted=true},objectStore(){return {
      get(key){readKey=key;readRequest={};return readRequest},
      put(row){if(fixture.failWrites)throw Error('QuotaExceeded');writes.push(['put',structuredClone(row)])},
      delete(key){if(fixture.failWrites)throw Error('QuotaExceeded');writes.push(['delete',key])}
    }}};
    const run=async()=>{readRequest.result=structuredClone(rows.get(readKey));readRequest.onsuccess();await Promise.resolve();
      if(aborted){tx.onabort();return}
      for(const [op,value] of writes){if(op==='put')rows.set(value.account,value);else rows.delete(value)}
      tx.oncomplete();
    };
    tail=tail.then(run);return tx;
  }};
  const fixture={rows,failWrites:false,open(){const req={result:db};queueMicrotask(()=>req.onsuccess());return req}};
  return fixture;
}
(async()=>{
  const idb=indexedFixture(),a=createJournal('owner',idb);
  const first={id:'T1',name:'Uno',body:{mutation_id:'first-payment',action:'payment',amount:'100'}};
  const second={id:'T2',name:'Dos',body:{mutation_id:'second-payment',action:'payment',amount:'200'}};
  assert.equal(await a.read(),null);
  assert.deepEqual(await a.claim(first),first);
  const reopened=createJournal('owner',idb);
  assert.deepEqual(await reopened.read(),first,'new page recovers exactly the old mutation ID');
  assert.deepEqual(await reopened.claim(second),first,'cannot overwrite an unconfirmed payment');
  assert.deepEqual(await a.clear('wrong-id'),first,'a stale tab must not remove another pending operation');
  assert.equal(await a.clear(first.body.mutation_id),null);
  assert.equal(await reopened.read(),null);
  const results=await Promise.all([a.claim(first),reopened.claim(second)]);
  assert.deepEqual(results[0],results[1],'two tabs atomically select one pending operation');
  const other=createJournal('another-admin',idb);
  assert.equal(await other.read(),null,'other signed-in accounts cannot see this journal');
  await other.claim(second);
  assert.deepEqual(await a.read(),first);
  await a.clear(first.body.mutation_id);
  idb.failWrites=true;
  await assert.rejects(a.claim(first),/QuotaExceeded/);
  assert.equal(await a.read(),null,'a failed local commit cannot appear durable');
  idb.failWrites=false;
  await a.claim(first);idb.failWrites=true;
  await assert.rejects(a.clear(first.body.mutation_id),/QuotaExceeded/);
  assert.deepEqual(await a.read(),first,'failed cleanup retains retry ID after server success');
  await assert.rejects(createJournal('owner',null).read(),/respaldo/);
  idb.rows.set('broken',{account:'broken',value:{id:'T1',body:{action:'payment'}}});
  await assert.rejects(createJournal('broken',idb).read(),/movimiento local/);
  console.log('Payment journal: durable reopen, account isolation, simultaneous tabs, quota failures, safe cleanup OK');
})().catch(error=>{console.error(error);process.exit(1)});
