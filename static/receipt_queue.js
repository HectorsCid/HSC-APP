(function(root){
  'use strict';
  const isReceipt=action=>Boolean(action.receipt&&action.url==='/api/operaciones/expenses');
  const legacyFailure=action=>isReceipt(action)&&/selecciona una fotograf[ií]a del comprobante/i.test(action.error||'');
  async function prepare(entry){
    const blob=entry?.blob||entry;
    const invalid=message=>Object.assign(Error(message),{status:400,receiptRepair:true});
    if(!blob||typeof blob.slice!=='function'||typeof blob.arrayBuffer!=='function'||!blob.size)throw invalid('No se puede leer la foto del comprobante guardada en este dispositivo. Selecciónala nuevamente.');
    if(blob.size>15*1024*1024)throw invalid('El comprobante supera 15 MB. Elige otra fotografía.');
    let head;try{head=new Uint8Array(await blob.slice(0,16).arrayBuffer());}catch(_){throw invalid('El teléfono no pudo recuperar la foto del comprobante. Selecciónala nuevamente.');}
    const type=head[0]===255&&head[1]===216?'image/jpeg':head[0]===137&&head[1]===80&&head[2]===78&&head[3]===71?'image/png':String.fromCharCode(...head.slice(0,4))==='RIFF'&&String.fromCharCode(...head.slice(8,12))==='WEBP'?'image/webp':'';
    if(!type)throw invalid('El comprobante no es una foto JPG, PNG o WebP legible. Elige otra fotografía.');
    const ext={'image/jpeg':'jpg','image/png':'png','image/webp':'webp'}[type];
    const name=String(entry?.name||blob.name||'').split(/[\\/]/).pop().trim();
    // Blob + explicit filename survives a restored iOS File without a name.
    return {blob:new root.Blob([blob],{type}),name:name&&/\.(jpe?g|png|webp)$/i.test(name)?name:'comprobante.'+ext};
  }
  async function form(entry){const file=await prepare(entry),body=new root.FormData();body.append('file',file.blob,file.name);return body;}
  function mount(api){
    const doc=root.document,button=doc.createElement('button');button.type='button';button.className='mini-btn';button.hidden=true;api.banner.append(button);
    const modal=doc.createElement('div');modal.className='modal';modal.id='receiptRecoveryModal';modal.innerHTML='<div class="sheet"><h2>Comprobantes pendientes</h2><p>Los gastos y los reportes se conservan. Revisa la foto que necesita atención; los demás envíos pueden continuar.</p><div data-receipt-rows></div><p data-receipt-result role="status"></p><button type="button" class="secondary" data-close-receipts>Cerrar</button></div>';doc.body.append(modal);
    let rows=[],busy=false,lastSignature='';
    const candidates=()=>rows.filter(action=>isReceipt(action)&&(action.error||action.receiptBlocked));
    function render(){
      const pending=candidates();button.hidden=!pending.length;button.textContent=`Revisar comprobantes (${pending.length})`;
      modal.querySelector('[data-receipt-rows]').innerHTML=pending.map(action=>`<article class="form-section" style="padding:12px;margin:10px 0"><b>${api.escape(action.body?.concept||'Gasto')}</b><p>${api.escape(action.body?.expense_date||'')} · ${api.escape(action.body?.amount||'')} · ${api.escape(action.body?.id||action.id)}</p><p>${api.escape(action.error||'La foto requiere revisión.')}</p><label class="secondary" style="display:block;margin:8px 0">Seleccionar otra foto<input type="file" accept="image/jpeg,image/png,image/webp" data-replace-receipt="${api.escape(action.id)}" ${busy?'disabled':''}></label><button type="button" class="secondary" data-without-receipt="${api.escape(action.id)}" ${busy?'disabled':''}>Conservar gasto sin subir esta foto</button></article>`).join('')||'<p>No quedan comprobantes con errores.</p>';
      modal.querySelectorAll('[data-replace-receipt]').forEach(input=>input.onchange=async()=>{const file=input.files[0];if(file)await repair(input.dataset.replaceReceipt,file,false);});
      modal.querySelectorAll('[data-without-receipt]').forEach(item=>item.onclick=async()=>{if(root.confirm('¿Dejar de enviar esta foto? Se conserva el gasto, pero se quitará esta fotografía pendiente de la cola. No se elimina ningún reporte. Si no tienes otra copia de la foto, no podrás recuperarla desde esta cola.'))await repair(item.dataset.withoutReceipt,null,true);});
    }
    async function repair(id,file,without){if(busy)return;busy=true;render();const message=modal.querySelector('[data-receipt-result]');message.textContent='Guardando la corrección…';try{await api.repair(id,file,without);message.textContent='Corrección guardada. Se reanudará el envío.';}catch(error){message.textContent=error.message;}finally{busy=false;render();}}
    button.onclick=()=>{render();modal.classList.add('open');};modal.querySelector('[data-close-receipts]').onclick=()=>modal.classList.remove('open');
    return {refresh(value){rows=value;const signature=JSON.stringify(candidates().map(row=>[row.id,row.error,row.receiptBlocked,row.body?.concept]));if(signature!==lastSignature){lastSignature=signature;render();}}};
  }
  const api={isReceipt,legacyFailure,prepare,form,mount};if(typeof module!=='undefined')module.exports=api;else root.HscReceiptQueue=api;
})(typeof window!=='undefined'?window:globalThis);
