(function(root){
  'use strict';
  const isReceipt=action=>Boolean(action.receipt&&action.url==='/api/operaciones/expenses');
  const isEntityPhoto=action=>['equipment','client'].includes(action.entityPhoto);
  const isFault=action=>action.url==='/api/operaciones/faults';
  const entityKeys=action=>action.entityPhoto==='equipment'?(action.body?.items||[]).map(row=>'equipment:'+row.id):action.entityPhoto==='client'?['client:'+action.body.id]:isFault(action)?['fault:'+action.body.id]:/^\/api\/operaciones\/faults\/[^/]+\//.test(action.url||'')?['fault:'+decodeURIComponent(action.url.split('/')[4])]:[];
  const needsPhotoRepair=action=>isEntityPhoto(action)&&Boolean(action.photoBlocked);
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
  async function faultForm(entry){try{return await form(entry);}catch(error){error.message=error.message.replace(/del comprobante/g,'de la falla').replace(/El comprobante/g,'La fotografía');throw error;}}
  async function sendFault(action,{post,persist,upload}){
    const completed=Number(action.faultPhotosUploaded||0);
    if(!Number.isInteger(completed)||completed<0||completed>(action.files||[]).length)throw Error('El progreso de fotos no es válido. Conservamos la falla y sus fotos para revisión.');
    const result=action.faultSavedResult||await post(action.url,action.body);
    if(!result?.fault?.id)throw Object.assign(Error('No se confirmó el registro de la falla.'),{status:502});
    if(!action.faultSavedResult){action.faultSavedResult=result;await persist(action);}
    for(let i=completed;i<(action.files||[]).length;i++){
      action.faultPhotoIndex=i;await persist(action);
      // Keep evidence positions stable: omitting Foto2 must not move Foto3 onto it.
      if(!(action.faultOmittedPhotos||[]).includes(i))await upload(`/api/operaciones/faults/${encodeURIComponent(result.fault.id)}/evidence/${i+1}`,await faultForm(action.files[i]));
      action.faultPhotosUploaded=i+1;await persist(action);
    }
    return result;
  }
  async function replaceFaultPhoto(action,file,without=false){
    const position=Number(action.faultPhotoIndex||0);
    if(!isFault(action)||!action.faultPhotoBlocked||!Number.isInteger(position)||position<Number(action.faultPhotosUploaded||0)||position>=(action.files||[]).length)throw Error('Esta foto ya no está pendiente de revisión. Actualiza el detalle.');
    const replacement=without?null:await prepare(file),files=action.files.slice();files[position]=replacement;
    const omitted=(action.faultOmittedPhotos||[]).filter(index=>index!==position);
    if(without)omitted.push(position);
    return {...action,files,faultOmittedPhotos:omitted,faultPhotoBlocked:false,faultPhotoRetryAfter:0,error:''};
  }
  function mount(api){
    const doc=root.document,button=doc.createElement('button');button.type='button';button.className='mini-btn';button.hidden=true;api.banner.append(button);
    const modal=doc.createElement('div');modal.className='modal';modal.id='receiptRecoveryModal';modal.innerHTML='<div class="sheet"><h2>Comprobantes pendientes</h2><p>Los registros y los reportes se conservan. Revisa la foto que necesita atención; los demás envíos pueden continuar.</p><div data-receipt-rows></div><p data-receipt-result role="status"></p><button type="button" class="secondary" data-close-receipts>Cerrar</button></div>';doc.body.append(modal);
    let rows=[],busy=false,lastSignature='';
    const candidates=()=>rows.filter(action=>isReceipt(action)&&(action.error||action.receiptBlocked)||needsPhotoRepair(action)||isFault(action)&&action.faultPhotoBlocked);
    function render(){
      const pending=candidates();button.hidden=!pending.length;button.textContent=`Revisar fotos pendientes (${pending.length})`;
      modal.querySelector('h2').textContent='Fotos pendientes de revisión';
      modal.querySelector('[data-receipt-rows]').innerHTML=pending.map(action=>isFault(action)?`<article class="form-section" style="padding:12px;margin:10px 0"><b>Foto ${Number(action.faultPhotoIndex||0)+1} de falla · ${api.escape(action.body?.equipment_id||action.body?.id||'')}</b><p>${api.escape(action.error||'La foto requiere revisión.')}</p><p>Puedes sustituir u omitir sólo esta foto. La falla y las otras fotos se conservan.</p><label class="secondary" style="display:block;margin:8px 0">Seleccionar otra foto JPG, PNG o WebP<input type="file" accept="image/jpeg,image/png,image/webp" data-replace-fault="${api.escape(action.id)}" ${busy?'disabled':''}></label><button type="button" class="secondary" data-without-fault="${api.escape(action.id)}" ${busy?'disabled':''}>Continuar sin esta foto</button></article>`:`<article class="form-section" style="padding:12px;margin:10px 0"><b>${api.escape(action.body?.concept||action.body?.items?.map(item=>item.id).join(', ')||action.body?.id||'Foto pendiente')}</b><p>${api.escape(action.body?.expense_date||'')} · ${api.escape(action.body?.amount||'')} · ${api.escape(action.body?.id||action.id)}</p><p>${api.escape(action.error||'La foto requiere revisión.')}</p><label class="secondary" style="display:block;margin:8px 0">Seleccionar otra foto JPG, PNG o WebP<input type="file" accept="image/jpeg,image/png,image/webp" data-replace-receipt="${api.escape(action.id)}" ${busy?'disabled':''}></label><button type="button" class="secondary" data-without-receipt="${api.escape(action.id)}" ${busy?'disabled':''}>Conservar registro sin subir esta foto</button></article>`).join('')||'<p>No quedan fotos con errores.</p>';
      modal.querySelectorAll('[data-without-fault]').forEach(item=>item.onclick=async()=>{if(busy||!root.confirm('¿Continuar sin esta foto de la falla? Se quitará sólo esta fotografía de la cola local. La falla, las demás fotos y lo ya guardado en el servidor se conservan. Si no tienes otra copia, podrías perder esta foto definitivamente.'))return;busy=true;render();try{await api.repairFault(item.dataset.withoutFault,null,true);modal.querySelector('[data-receipt-result]').textContent='Foto omitida. La falla y las demás fotos continúan su envío.';}catch(error){modal.querySelector('[data-receipt-result]').textContent=error.message;}finally{busy=false;render();}});
      modal.querySelectorAll('[data-replace-fault]').forEach(input=>input.onchange=async()=>{const file=input.files[0];if(!file||busy)return;busy=true;render();try{await api.repairFault(input.dataset.replaceFault,file);modal.querySelector('[data-receipt-result]').textContent='Foto corregida. Se reanudará el envío.';}catch(error){modal.querySelector('[data-receipt-result]').textContent=error.message;}finally{busy=false;render();}});
      modal.querySelectorAll('[data-replace-receipt]').forEach(input=>input.onchange=async()=>{const file=input.files[0];if(file)await repair(input.dataset.replaceReceipt,file,false);});
      modal.querySelectorAll('[data-without-receipt]').forEach(item=>item.onclick=async()=>{if(root.confirm('¿Dejar de enviar esta foto? Se conserva el registro, pero se quitará esta fotografía pendiente de la cola. No se elimina ningún reporte. Si no tienes otra copia de la foto, no podrás recuperarla desde esta cola.'))await repair(item.dataset.withoutReceipt,null,true);});
    }
    async function repair(id,file,without){if(busy)return;busy=true;render();const message=modal.querySelector('[data-receipt-result]');message.textContent='Guardando la corrección…';try{await api.repair(id,file,without);message.textContent='Corrección guardada. Se reanudará el envío.';}catch(error){message.textContent=error.message;}finally{busy=false;render();}}
    button.onclick=()=>{render();modal.classList.add('open');};modal.querySelector('[data-close-receipts]').onclick=()=>modal.classList.remove('open');
    return {refresh(value){rows=value;const signature=JSON.stringify(candidates().map(row=>[row.id,row.error,row.receiptBlocked,row.faultPhotoIndex,row.body?.concept]));if(signature!==lastSignature){lastSignature=signature;render();}}};
  }
  const api={isReceipt,isEntityPhoto,isFault,entityKeys,needsPhotoRepair,legacyFailure,prepare,form,faultForm,sendFault,replaceFaultPhoto,mount};if(typeof module!=='undefined')module.exports=api;else root.HscReceiptQueue=api;
})(typeof window!=='undefined'?window:globalThis);
