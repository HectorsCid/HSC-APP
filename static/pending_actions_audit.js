(function(root){
  'use strict';
  function describe(action){
    const routes={'/api/operaciones/equipment':'Equipo','/api/operaciones/clients':'Cliente','/api/operaciones/expenses':'Gasto','/api/operaciones/tasks':'Actividad','/api/operaciones/faults':'Falla','/api/operaciones/worklists':'Lista de trabajo'};
    const type=routes[action.url]||(/\/expenses\/[^/]+\/status$/.test(action.url||'')?'Estado de gasto':'Otra acción');
    const ids=Array.isArray(action.body?.items)?action.body.items.map(row=>String(row.id||'')).slice(0,200):[String(action.body?.id||'')];
    const created=Number(action.createdAt);
    return {id:String(action.id||''),type,records:ids.filter(Boolean),created:Number.isFinite(created)&&created>0?new Date(created).toISOString():'Sin fecha',
      state:action.photoBlocked||action.receiptBlocked||action.blocked?'Requiere revisión':action.error?'Último intento fallido':'Pendiente',
      recordConfirmed:Boolean(action.entitySavedResult||action.receiptSavedExpense),
      photos:(action.files||[]).map(entry=>{const blob=entry?.blob||entry;return {bytes:Number(blob?.size||0),type:String(blob?.type||''),readable:Boolean(blob&&typeof blob.arrayBuffer==='function'&&blob.size)};}),
      error:String(action.error||'').replace(/https?:\/\/\S+/g,'[enlace omitido]').slice(0,300)};
  }
  function report(actions){
    const rows=actions.slice().sort((a,b)=>Number(a.createdAt||0)-Number(b.createdAt||0)).map(describe),counts={};
    for(const row of rows)counts[row.type]=(counts[row.type]||0)+1;
    return {version:1,total:rows.length,counts,actions:rows};
  }
  function mount({banner,read}){
    const doc=root.document,button=doc.createElement('button');button.type='button';button.className='mini-btn';button.textContent='Detalle de pendientes';banner.append(button);
    const modal=doc.createElement('div');modal.className='modal';modal.innerHTML='<div class="sheet"><h2>Detalle de pendientes</h2><p>Consulta de solo lectura. No se borra ni se envía ninguna acción. El diagnóstico incluye IDs y mensajes de error, no fotos ni contraseñas. Revisa el texto antes de compartirlo.</p><p data-summary role="status"></p><textarea data-output readonly aria-label="Diagnóstico de acciones pendientes" style="width:100%;min-height:220px"></textarea><div class="sheet-actions"><button type="button" class="secondary" data-close>Cerrar</button><button type="button" class="primary" data-copy>Copiar diagnóstico</button></div></div>';doc.body.append(modal);
    const output=modal.querySelector('[data-output]'),summary=modal.querySelector('[data-summary]');
    button.onclick=async()=>{button.disabled=true;try{const result=report(await read());output.value=JSON.stringify(result,null,2);summary.textContent=`${result.total} acciones · `+Object.entries(result.counts).map(([type,n])=>`${n} ${type}`).join(' · ');modal.classList.add('open');}catch(_){summary.textContent='No se pudo leer la cola local. No se modificaron sus datos.';output.value='';modal.classList.add('open');}finally{button.disabled=false;}};
    modal.querySelector('[data-copy]').onclick=async()=>{try{await root.navigator.clipboard.writeText(output.value);summary.textContent='Diagnóstico copiado. Puedes pegarlo en el chat.';}catch(_){output.focus();output.select();summary.textContent='Mantén presionado el texto y elige Copiar.';}};
    modal.querySelector('[data-close]').onclick=()=>modal.classList.remove('open');
  }
  const api={report,mount};if(typeof module!=='undefined')module.exports=api;else root.HscPendingAudit=api;
})(typeof window!=='undefined'?window:globalThis);
