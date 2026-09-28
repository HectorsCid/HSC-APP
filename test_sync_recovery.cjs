const test = require('node:test');
const assert = require('node:assert/strict');
const recovery = require('./static/sync_recovery.js');
test('paused delivery shows every owner recovery detail', () => {
  const model = recovery.describe({state:'uncertain', requires_confirmation:true, entity_type:'report',entity_id:'TEST-1',operation_id:'op-A',attempt_id:'attempt-A',revision:9,started_at:'2026-09-28T10:00:00Z',last_error:'Respuesta perdida',waiting_changes:71});
  assert.equal(model.title, 'Sincronización pausada');
  for (const value of ['TEST-1','op-A','9','Respuesta perdida','71']) assert.ok(model.fields.some(([,v]) => String(v).includes(value)));
});
test('confirmation pins both operation and attempt and requires acknowledgement', () => {
  const target = {operation_id:'op-A',attempt_id:'attempt-A'};
  assert.throws(() => recovery.confirmation(target, false));
  assert.deepEqual(recovery.confirmation(target, true), {...target, confirm_no_request_in_flight:true});
  assert.throws(() => recovery.confirmation({operation_id:'op-A'}, true));
});
test('an active writer never offers manual recovery', () => {
  assert.equal(recovery.describe({state:'active',in_flight:true}).canResume,false);
});
