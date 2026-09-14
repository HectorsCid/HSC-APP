const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('templates/app_operativa_demo.html','utf8');
const code=source.slice(source.indexOf("  $('#changeUserCompany').onclick="),source.indexOf('  function renderUsers(){'));
(async()=>{for(const answers of [[false],[true,false],[true,true]]){
 let asks=0,sends=0;const button={},select={value:'B'},user={id:'U',role:'client',name:'Prueba',email:'p@example.com',client_id:'A'};
 const ns={$:id=>id==='#changeUserCompany'?button:select,operationUsers:[user],selectedPermissionUser:'U',account:{isOwner:true},clientName:id=>id,confirm:msg=>{assert(msg.includes('Prueba'));assert(msg.includes('B'));return answers[asks++]},toast:()=>{},operationsPost:async(url,body)=>{sends++;assert.equal(body.expected_client_id,'A');assert(body.confirm_change&&body.confirm_access);return {user:{...user,client_id:'B'}}},renderUserCompanyEditor:()=>{},renderUsers:()=>{}};
 vm.runInNewContext(code,ns);await button.onclick();assert.equal(sends,answers.length===2&&answers[1]?1:0);assert.equal(asks,answers.length);
}console.log('OK: ninguna modificación hasta aceptar las dos confirmaciones.');})().catch(e=>{console.error(e);process.exitCode=1});
