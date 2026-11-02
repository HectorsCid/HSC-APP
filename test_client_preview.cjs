const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('templates/app_operativa_demo.html','utf8');
const context=vm.createContext({serverRole:'admin',account:{isOwner:true},clientPreview:false,currentMode:'partner'});
for(const name of ['isClientPresentation','assertPreviewReadOnly']){
  const source=html.match(new RegExp('  function '+name+'\\([^\\n]+'))[0];
  vm.runInContext(source,context);
}
assert.equal(context.isClientPresentation(),false);
context.clientPreview=true;assert.equal(context.isClientPresentation(),true);
assert.throws(()=>context.assertPreviewReadOnly(),/Vista de prueba/);
context.clientPreview=false;assert.doesNotThrow(()=>context.assertPreviewReadOnly());
context.serverRole='client';assert.equal(context.isClientPresentation(),true);
context.serverRole='technician';context.account.isOwner=false;context.clientPreview=true;
assert.equal(context.isClientPresentation(),false);
for(const marker of ['previewOriginalPost','previewOriginalQueue',"$('.topbar').after($('#clientPreviewTools'))",'!clientPreview&&','Salir de vista cliente',"get canCreate(){return isClientPresentation();}"])
  assert.ok(html.includes(marker),marker);
console.log('OK: vista cliente, límites de rol, bloqueo de escritura y salida sin cambiar sesión');
