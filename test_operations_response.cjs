const assert=require('node:assert/strict'),{read}=require('./static/operations_response.js');
for(const status of [200,400,502,503]){
  const result=read('<!DOCTYPE html><html>Error</html>',{status});
  assert.equal(result.ok,false);assert.equal(result.code,'invalid_server_response');assert.match(result.error,/conserva/);
}
assert.equal(read('<html>login</html>',{status:200,redirected:true}).code,'session_expired');
assert.equal(read('{}',{status:401}).code,'session_expired');
assert.deepEqual(read('{"ok":true,"report":{"id":"R1"}}',{status:200}),{ok:true,report:{id:'R1'}});
assert.equal(read('null',{status:200}).ok,false);
assert.equal(read('{"ok":false,"code":"report_conflict"}',{status:409}).code,'report_conflict');
console.log('OK: HTML de errores/login no confirma envíos ni produce errores de JSON.');
