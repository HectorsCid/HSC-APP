(function(root){
  'use strict';
  function describe(action){
    const routes={'/api/operaciones/equipment':'Equipo','/api/operaciones/clients':'Cliente','/api/operaciones/expenses':'Gasto','/api/operaciones/tasks':'Actividad','/api/operaciones/faults':'Falla','/api/operaciones/worklists':'Lista de trabajo'};
    const type=routes[action.url]||(/\/clients\/[^/]+\/round$/.test(action.url||'')?'Selección de ronda':/\/expenses\/[^/]+\/status$/.test(action.url||'')?'Estado de gasto':'Otra acción');
    const ids=Array.isArray(action.body?.items)?action.body.items.map(row=>String(row.id||'')).slice(0,200):[String(action.body?.id||'')];
    const created=Number(action.createdAt);
    return {id:String(action.id||''),type,records:ids.filter(Boolean),created:Number.isFinite(created)&&created>0?new Date(created).toISOString():'Sin fecha',
      state:action.photoBlocked||action.receiptBlocked||action.faultPhotoBlocked||action.blocked?'Requiere revisión':action.error?'Último intento fallido':'Pendiente',
      recordConfirmed:Boolean(action.entitySavedResult||action.receiptSavedExpense||action.faultSavedResult),
      photos:(action.files||[]).filter(Boolean).map(entry=>{const blob=entry?.blob||entry;return {bytes:Number(blob?.size||0),type:String(blob?.type||''),readable:Boolean(blob&&typeof blob.arrayBuffer==='function'&&blob.size)};}),
      error:String(action.error||'').replace(/https?:\/\/\S+/g,'[enlace omitido]').slice(0,300)};
  }
  function report(actions){
    const rows=actions.slice().sort((a,b)=>Number(a.createdAt||0)-Number(b.createdAt||0)).map(describe),counts={};
    for(const row of rows)counts[row.type]=(counts[row.type]||0)+1;
    return {version:1,total:rows.length,counts,actions:rows};
  }
  function ageDays(action,now=Date.now()){
    const created=Number(action.createdAt);
    return Number.isFinite(created)&&created>0?Math.max(0,Math.floor((now-created)/86400000)):null;
  }
  function localDate(action){
    const created=Number(action.createdAt);
    return Number.isFinite(created)&&created>0?new Intl.DateTimeFormat('es-MX',{dateStyle:'medium',timeStyle:'short',hourCycle:'h23',timeZone:'America/Mexico_City'}).format(new Date(created))+' · hora de Ciudad de México':'Sin fecha registrada';
  }
  function discardWarning(action){
    const row=describe(action),photos=row.photos.length;
    return `¿Descartar sólo este pendiente local?\n${row.type} · ${localDate(action)}\n${row.records.join(', ')||row.id}\n\nSe perderá este cambio pendiente de enviar. No se borrará ni se revertirá lo que ya esté guardado en el servidor. No se cancelarán otros pendientes relacionados.`+(photos?`\n\nATENCIÓN: contiene ${photos} foto(s). Las copias de esta cola se eliminarán; si no tienes otro respaldo, podrías perderlas definitivamente.`:'')+'\n\nEsta acción no se puede deshacer.';
  }
  function mount({banner,read,discard}){
    const doc=root.document,button=doc.createElement('button');button.type='button';button.className='mini-btn';button.textContent='Detalle de pendientes';banner.append(button);
    const modal=doc.createElement('div');modal.className='modal';modal.innerHTML='<div class="sheet" style="max-height:90dvh;overflow:auto"><h2>Detalle de pendientes</h2><p>Fechas en hora de Ciudad de México. Los de 7 días o más se señalan como antiguos; no se elimina nada automáticamente. Descartar quita sólo el envío local, no borra datos del servidor.</p><p data-summary role="status"></p><div data-actions style="max-height:42dvh;overflow-y:auto"></div><details><summary>Diagnóstico para soporte</summary><p>No incluye fotos ni el contenido completo de los cambios. No es un respaldo recuperable.</p><textarea data-output readonly aria-label="Diagnóstico de acciones pendientes" style="width:100%;min-height:160px"></textarea><button type="button" class="secondary" data-copy>Copiar diagnóstico</button></details><div class="sheet-actions"><button type="button" class="secondary" data-close>Cerrar</button></div></div>';doc.body.append(modal);
    const output=modal.querySelector('[data-output]'),summary=modal.querySelector('[data-summary]');
    let busy=false;
    async function refresh(){
      const actions=(await read()).slice().sort((a,b)=>Number(a.createdAt||0)-Number(b.createdAt||0)),result=report(actions);
      output.value=JSON.stringify(result,null,2);summary.textContent=`${result.total} acciones · `+Object.entries(result.counts).map(([type,n])=>`${n} ${type}`).join(' · ');
      const container=modal.querySelector('[data-actions]');container.replaceChildren();
      for(const action of actions){
        const row=describe(action),article=doc.createElement('article');article.className='form-section';article.style.cssText='padding:12px;margin:8px 0';
        const title=doc.createElement('b');title.textContent=row.type+' · '+(row.records.join(', ')||row.id);article.append(title);
        const date=doc.createElement('p'),age=ageDays(action);date.textContent=localDate(action)+(age===null?'':` · ${age} día(s)`)+(age!==null&&age>=7?' · Antiguo':'');article.append(date);
        const status=doc.createElement('p');status.textContent=row.state+(row.photos.length?` · ${row.photos.length} foto(s)`:'')+(row.error?' · '+row.error:'');article.append(status);
        if(discard){const remove=doc.createElement('button');remove.type='button';remove.className='danger';remove.textContent='Descartar este pendiente';remove.onclick=async()=>{
          if(busy)return;
          busy=true;remove.disabled=true;
          try{
            const current=(await read()).find(item=>item.id===action.id);
            if(!current){await refresh();return;}
            if(!root.confirm(discardWarning(current)))return;
            await discard(current.id);await refresh();summary.textContent='Pendiente descartado de este dispositivo. Los demás se conservan.';
          }catch(error){summary.textContent=error.message||'No se pudo descartar. Actualiza el detalle.';}
          finally{busy=false;remove.disabled=false;}
        };article.append(remove);}
        container.append(article);
      }
    }
    button.onclick=async()=>{if(busy)return;button.disabled=true;try{await refresh();modal.classList.add('open');}catch(_){summary.textContent='No se pudo leer la cola local. No se modificaron sus datos.';output.value='';modal.classList.add('open');}finally{button.disabled=false;}};
    modal.querySelector('[data-copy]').onclick=async()=>{try{await root.navigator.clipboard.writeText(output.value);summary.textContent='Diagnóstico copiado. Puedes pegarlo en el chat.';}catch(_){output.focus();output.select();summary.textContent='Mantén presionado el texto y elige Copiar.';}};
    modal.querySelector('[data-close]').onclick=()=>modal.classList.remove('open');
  }
  const api={report,ageDays,localDate,discardWarning,mount};if(typeof module!=='undefined')module.exports=api;else root.HscPendingAudit=api;
})(typeof window!=='undefined'?window:globalThis);
