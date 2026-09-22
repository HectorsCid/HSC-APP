(function(root,factory){const lib=factory();if(typeof module==='object'&&module.exports)module.exports=lib;else root.HscRepairs=lib})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const fields=['client_name','equipment_name','symptom','work','result','refrigerant','pressure_low','pressure_high','temperature','amperage'];
  const stages={before:'Antes',during:'Durante',after:'Después'},outcomes={working:'Trabajando bien',follow_up:'Requiere seguimiento',stopped:'Fuera de servicio'};
  function visible(row,query='',equipment=''){const d=row.data;return (!equipment||d.equipment_key===equipment)&&(!query||JSON.stringify(d).toLocaleLowerCase().includes(query.toLocaleLowerCase()))}
  function label(row){return row.localState==='pending'?'Pendiente de envío':row.localState==='blocked'?'Revisar envío':row.localState==='draft'?'Borrador en este dispositivo':row.data.status==='completed'?'Visita finalizada':row.data.status==='uploading'?'Fotos pendientes':'Borrador'}
  function deletableDraft(row){return !!row&&row.localState==='draft'&&Number(row.data.revision||0)===0&&row.data.status!=='completed'}
  function valvePosition(movements){return (movements||[]).reduce((sum,move)=>sum+move.steps,0)}
  function turnAmount(steps){const eighths=Math.abs(steps),whole=Math.floor(eighths/8),rest=eighths%8;const part=rest===0?'':rest%2?`${rest}/8`:rest%4?`${rest/2}/4`:'1/2';return [whole?`${whole}`:'',part].filter(Boolean).join(' ')||'0'}
  function turnUnit(steps){return Math.abs(steps)<8?'de vuelta':Math.abs(steps)===8?'vuelta':'vueltas'}
  function valveLabel(steps){return steps===0?'Posición original':`${turnAmount(steps)} ${turnUnit(steps)} a la ${steps>0?'derecha':'izquierda'} del inicio`}
  function movementLabel(steps){return `${turnAmount(steps)} ${turnUnit(steps)} a la ${steps>0?'derecha':'izquierda'}`}
  function valveDial(steps,start=steps){return `<span class="repair-valve-dial" role="img" aria-label="Inicio en gris; posición actual en verde: ${valveLabel(steps)}"><span class="repair-valve-origin"></span><span class="repair-valve-moving" style="transform:rotate(${start*45}deg)"></span></span>`}
  // A queued mutation is immutable: retries send exactly the same identity and bytes.
  async function transmit(row,request){
    const data=row.data,base='/api/operaciones/repairs',url=base+'/'+encodeURIComponent(data.id);
    const saved=await request(base,{method:'POST',body:JSON.stringify(data),headers:{'Content-Type':'application/json'}});
    if(saved.repair.mutation_id!==data.mutation_id)throw Object.assign(Error('Hay una versión posterior en el servidor. Compara ambas versiones.'),{status:409});
    for(const photo of data.photos||[]){const file=(row.files||[]).find(f=>f.id===photo.id);if(!file)continue;const body=new FormData();body.append('file',file.blob,file.name||'foto.jpg');body.append('mutation_id',data.mutation_id);await request(url+'/photos/'+encodeURIComponent(photo.id),{method:'POST',body})}
    return (await request(url+'/finish',{method:'POST',body:JSON.stringify({mutation_id:data.mutation_id}),headers:{'Content-Type':'application/json'}})).repair;
  }
  function mount(api){
    const $=s=>document.querySelector(s),esc=api.escape,form=$('#repairForm'),uid=()=>crypto.randomUUID().replaceAll('-','');
    let records=[],selected='',equipmentFilter='',next=null,remoteIds=[],editor=null,writeChain=Promise.resolve(),timer,searchTimer,deleteArmTimer,deleteArmedFor='',photoBusy=false,syncBusy=false,loadToken=0,lastError='',urls=[],valveWorkspaceId='',pendingValveSteps=0;
    const access=api.account.isOwner||api.role==='technician';
    if(!access)return null;
    const banner=document.createElement('div');banner.id='repairSyncBanner';banner.className='repair-sync-notice';banner.hidden=true;banner.innerHTML='<span></span> <button class="mini-btn" type="button">Enviar reparaciones</button>';$('.topbar').after(banner);banner.querySelector('button').onclick=()=>sync(true);
    function db(){return new Promise((resolve,reject)=>{const req=indexedDB.open('hsc-repairs-'+api.account.id,1);req.onupgradeneeded=()=>req.result.createObjectStore('records',{keyPath:'id'});req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(Error('No se pudo abrir el respaldo local de reparaciones.'))})}
    async function storage(mode,item){const connection=await db();try{return await new Promise((resolve,reject)=>{const tx=connection.transaction('records',mode==='list'?'readonly':'readwrite'),store=tx.objectStore('records'),req=mode==='list'?store.getAll():mode==='delete'?store.delete(item.id):store.put(item);tx.oncomplete=()=>resolve(req.result);tx.onerror=()=>reject(Error('No se pudo actualizar el respaldo local de reparaciones.'));tx.onabort=tx.onerror})}finally{connection.close()}}
    function serial(job){const promise=writeChain.catch(()=>{}).then(job);writeChain=promise;return promise}
    async function put(row){await serial(()=>storage('put',row));records=[row,...records.filter(r=>r.id!==row.id)];status()}
    async function request(url,options={}){const response=await api.fetch(url,options,45000);let payload;try{payload=await response.json()}catch(_){throw Error('Respuesta incompleta; se conserva la reparación para reintentar.')}if(!response.ok||!payload.ok)throw Object.assign(Error(payload.error||'No se confirmó el envío.'),{status:response.status});return payload}
    function currentRow(){return records.find(r=>r.id===selected)}
    function status(){const pending=records.filter(r=>['pending','blocked'].includes(r.localState));banner.hidden=!pending.length;banner.querySelector('span').textContent=pending.length+' reparación(es) pendientes · '+(pending.find(r=>r.error)?.error||'Respaldadas en este dispositivo.');banner.querySelector('button').disabled=syncBusy;$('#repairConnection').textContent=lastError||'El historial consultado y los borradores quedan disponibles en este dispositivo.';const count=records.filter(r=>r.localState==='draft').length;document.querySelectorAll('[data-repair-summary]').forEach(el=>el.textContent=pending.length?pending.length+' pendientes de envío':count?count+' borradores':'Historial por cliente y equipo')}
    function photoUrl(row,pid,full=false){const file=(row.files||[]).find(f=>f.id===pid);if(file){const url=URL.createObjectURL(file.blob);urls.push(url);return url}return '/api/operaciones/repairs/'+encodeURIComponent(row.id)+'/photos/'+encodeURIComponent(pid)+(full?'?full=1':'')}
    function clearUrls(){urls.forEach(url=>URL.revokeObjectURL(url));urls=[]}
    function summary(data){return (data.result||data.symptom||'Sin observaciones todavía').slice(0,180)}
    function cards(){const query=$('#repairSearch').value.trim();const rows=records.filter(r=>r.data.status!=='deleted'&&visible(r,query,equipmentFilter)&&(r.localState!=='synced'||remoteIds.includes(r.id)||!navigator.onLine||lastError)).sort((a,b)=>String(b.data.service_date).localeCompare(String(a.data.service_date))||b.id.localeCompare(a.id));$('#repairCards').innerHTML=rows.map(row=>`<button class="user-card repair-card" type="button" data-open-repair="${esc(row.id)}"><span class="repair-state">${esc(label(row))}</span><small>${esc(row.data.service_date)} · ${esc(row.data.client_name)} · ${esc(row.data.folio||row.id)}</small><b>${esc(row.data.equipment_name)}</b><small>${esc(summary(row.data))}</small><small>${esc(row.data.author||api.account.name)} · ${(row.data.photos||[]).length} fotos</small></button>`).join('')||'<p class="empty">Aún no hay reparaciones en esta consulta. Puedes registrar la primera visita.</p>';$('#repairCards').querySelectorAll('[data-open-repair]').forEach(b=>b.onclick=()=>open(b.dataset.openRepair));$('#repairScope').textContent=equipmentFilter?'Historial del equipo seleccionado · todas las rondas':'Todos los clientes y equipos';$('#repairMore').hidden=next===null;status()}
    async function refresh(more=false){
      const ticket=++loadToken;
      try{
        if(!api.shouldSync())throw Error('Modo sin conexión: mostrando lo guardado en este dispositivo.');
        const query=$('#repairSearch').value.trim(),params=new URLSearchParams({q:query,equipment:equipmentFilter,offset:String(more?next||0:0)});
        const payload=await request('/api/operaciones/repairs?'+params);
        if(ticket!==loadToken)return;
        const ids=[];
        for(const data of payload.repairs){
          ids.push(data.id);const local=records.find(r=>r.id===data.id);
          if(editor?.id===data.id&&api.current()==='repairEditor')continue;
          if(!local||(local.localState==='synced'&&data.revision>=local.data.revision)){
            if(local?.data.revision===data.revision&&local.data.status==='completed'&&data.status!=='completed')continue;
            await put({id:data.id,data,localState:'synced',files:local?.files||[]});
          }
        }
        remoteIds=more?[...new Set([...remoteIds,...ids])]:ids;next=payload.next_offset;lastError='';
      }catch(error){if(ticket!==loadToken)return;lastError=error.message||'Sin respuesta: mostrando el historial guardado.';}
      cards();
    }
    async function list(equipment=''){equipmentFilter=equipment;$('#repairSearch').value='';next=null;remoteIds=records.map(r=>r.id);api.show('repairs');cards();await refresh()}
    function detail(){const row=currentRow();if(!row){$('#repairDetail').innerHTML='<p>Selecciona una reparación desde el historial.</p>';return}const d=row.data;clearUrls();$('#repairDetail').innerHTML=`<span class="repair-state">${esc(label(row))}</span><h2>${esc(d.equipment_name)}</h2><p>${esc(d.client_name)} · ${esc(d.service_date)} · ${esc(d.author||api.account.name)}</p><p class="repair-folio">${esc(d.folio||'Folio pendiente de confirmación')}</p>${row.error?`<p class="repair-sync-notice">${esc(row.error)}</p>`:''}<p>${esc(d.model||'')} ${d.serial?'· Serie: '+esc(d.serial):''} ${d.location?'· '+esc(d.location):''}</p><div class="repair-kpis">${[['pressure_low','Baja','psi'],['pressure_high','Alta','psi'],['temperature','Temperatura','°C'],['amperage','Amperaje','A'],['refrigerant','Refrigerante','']].filter(([k])=>d[k]).map(([k,n,u])=>`<span>${n}<br><b>${esc(d[k])} ${u}</b></span>`).join('')}</div>${[['symptom','Qué fallaba'],['diagnosis','Diagnóstico'],['work','Trabajo realizado'],['parts','Partes utilizadas'],['result','Cómo quedó'],['measurement_notes','Condiciones de medición'],['recommendations','Recomendaciones']].filter(([k])=>d[k]).map(([k,n])=>`<h3>${n}</h3><p class="repair-detail">${esc(d[k])}</p>`).join('')}<p>${esc(outcomes[d.outcome]||'')}</p>`;
      $('#repairDetail').innerHTML+=(d.valve_adjustments||[]).filter(v=>v.movements?.length).map((v,index)=>`<div class="repair-valve"><div class="repair-valve-head"><h3>Válvula de expansión ${esc(v.name||index+1)}</h3>${valveDial(valvePosition(v.movements))}</div><p class="repair-valve-legend"><span class="origin">Inicio</span><span class="current">Posición final</span></p><p>${esc(valveLabel(valvePosition(v.movements)))}</p><p class="repair-valve-steps">Movimientos: ${esc(v.movements.map(m=>`${movementLabel(m.steps)}${m.pressure_low?' · Baja '+m.pressure_low+' psi':''}${m.pressure_high?' · Alta '+m.pressure_high+' psi':''}`).join(' | '))}</p></div>`).join('');
      $('#repairPhotoHistory').innerHTML=(d.photos||[]).map(p=>`<figure class="repair-photo"><a href="${esc(photoUrl(row,p.id,true))}" target="_blank" rel="noopener"><img loading="lazy" src="${esc(photoUrl(row,p.id))}" alt="${esc(p.caption||stages[p.stage])}"></a><figcaption>${esc(stages[p.stage])} · ${esc(p.caption||'')}</figcaption></figure>`).join('');
      const own=api.account.isOwner||d.created_by===api.account.id||!d.created_by;
      $('#repairEdit').hidden=!own||!api.canWrite();
      $('#repairEdit').disabled=row.localState==='pending';
      $('#repairEdit').textContent=row.localState==='blocked'?'Corregir propuesta local':'Editar esta visita';
      $('#repairDeleteDraft').hidden=!deletableDraft(row);
      $('#repairDeleteVisit').hidden=!(own&&api.canWrite()&&row.localState==='synced'&&d.status==='completed');
      $('#repairRepeat').hidden=!api.canWrite();
      $('#repairRemision').hidden=row.localState!=='synced'||d.status!=='completed';
      $('#repairRemision').href='/api/operaciones/repairs/'+encodeURIComponent(row.id)+'/remision';
      $('#repairResolve').hidden=row.localState!=='blocked';$('#repairCompare').hidden=true;
    }
    async function verifyCurrent(id){const local=records.find(r=>r.id===id);if(!local||local.localState!=='synced'||!api.shouldSync())return;try{const result=await request('/api/operaciones/repairs/'+encodeURIComponent(id));const fresh=records.find(r=>r.id===id);if(fresh?.localState==='synced'&&result.repair.revision>=fresh.data.revision){await put({...fresh,data:result.repair});if(selected===id&&api.current()==='repair')detail()}}catch(error){if(error.status!==404)return;const fresh=records.find(r=>r.id===id);if(!fresh)return;await put({...fresh,data:{...fresh.data,status:'deleted'},files:[]});if(selected===id&&api.current()==='repair'){selected='';try{localStorage.removeItem('hsc-repair-selected-'+api.account.id)}catch(_){}api.show('repairs');cards();api.toast('Esta visita fue retirada del historial.')}}}
    function resetDeleteConfirmation(clearStatus=true){clearTimeout(deleteArmTimer);deleteArmedFor='';$('#repairDeleteVisit').textContent='Retirar visita finalizada';if(clearStatus){$('#repairDeleteStatus').textContent='';$('#repairDeleteStatus').hidden=true}}
    function open(id){resetDeleteConfirmation();selected=id;try{localStorage.setItem('hsc-repair-selected-'+api.account.id,id)}catch(_){}detail();api.show('repair');verifyCurrent(id).catch(()=>{})}
    function option(value){return `<option value="${esc(value)}"></option>`}
    function clientsOptions(){const names=[...api.state().clients.map(c=>c.name),...records.map(r=>r.data.client_name)].filter(Boolean);$('#repairClientOptions').innerHTML=[...new Set(names)].map(option).join('')}
    function equipmentOptions(){const names=[...api.state().equipment.filter(e=>e.client_id===editor.data.client_id).map(e=>e.name),...records.filter(r=>r.data.client_key===editor.data.client_key).map(r=>r.data.equipment_name)].filter(Boolean);$('#repairEquipmentOptions').innerHTML=[...new Set(names)].map(option).join('')}
    function fill(){fields.forEach(k=>form.elements[k].value=editor.data[k]||'');const persisted=(editor.data.revision||0)>0;$('#repairClient').readOnly=persisted;$('#repairEquipment').readOnly=persisted;$('#repairDiscardDraft').hidden=!deletableDraft(editor);renderValves();renderPhotos()}
    async function edit(row=null,repeat=false){if(!api.canWrite()){api.toast('No tienes permiso para capturar reportes.');return}if(syncBusy){api.toast('Espera a que termine el envío actual.');return}if(row&&!repeat&&row.localState==='pending'){api.toast('Primero confirma el envío pendiente.');return}clearUrls();const old=row?.data||{},state=api.state(),id=repeat||!row?'REP_'+uid():row.id;const localDate=new Date();localDate.setMinutes(localDate.getMinutes()-localDate.getTimezoneOffset());const context=equipmentFilter?state.equipment.find(e=>'equipment:'+e.id===equipmentFilter):null,client=state.clients.find(c=>c.id===context?.client_id);let data;
      if(row&&!repeat)data={...old,photos:(old.photos||[]).map(p=>({...p}))};else{data={id,revision:0,client_id:old.client_id||client?.id||'',equipment_id:old.equipment_id||context?.id||'',client_key:old.client_key||(client?'client:'+client.id:'external:'+uid()),equipment_key:old.equipment_key||(context?'equipment:'+context.id:'external:'+uid()),client_name:old.client_name||client?.name||'',equipment_name:old.equipment_name||context?.name||'',address:old.address||'',model:old.model||context?.model||'',serial:old.serial||context?.serial||'',location:old.location||context?.location||'',service_date:localDate.toISOString().slice(0,10),photos:[],outcome:'working',created_by:api.account.id,author:api.account.name};}
      editor={id,data,localState:'draft',files:row&&!repeat?[...(row.files||[])]:[],base:row?.base||((old.revision||0)>0?old:null)};selected=id;form.reset();clientsOptions();equipmentOptions();fill();$('#repairDraftStatus').textContent='Los cambios se respaldan automáticamente en este dispositivo.';api.show('repairEditor');await backup();
    }
    function capture(){if(!editor)return;fields.forEach(k=>editor.data[k]=form.elements[k].value.trim());if(Number(editor.data.revision||0)>0)return;const state=api.state(),same=(a,b)=>String(a||'').trim().toLocaleLowerCase()===String(b||'').trim().toLocaleLowerCase();const clients=state.clients.filter(c=>same(c.name,editor.data.client_name)),client=clients.length===1?clients[0]:null;editor.data.client_id=client?.id||'';editor.data.client_key=client?'client:'+client.id:(editor.data.client_key?.startsWith('external:')?editor.data.client_key:'external:'+uid());const equipment=state.equipment.filter(e=>e.client_id===client?.id&&same(e.name,editor.data.equipment_name));const match=equipment.length===1?equipment[0]:null;editor.data.equipment_id=match?.id||'';editor.data.equipment_key=match?'equipment:'+match.id:(editor.data.equipment_key?.startsWith('external:')?editor.data.equipment_key:'external:'+uid())}
    async function backup(){clearTimeout(timer);timer=null;if(!editor)return true;capture();const row=structuredClone(editor);try{await put(row);$('#repairDraftStatus').textContent='Borrador respaldado en este dispositivo.';lastError='';return true}catch(error){lastError=error.message;$('#repairDraftStatus').textContent=error.message;throw error}}
    function autosave(){clearTimeout(timer);$('#repairDraftStatus').textContent='Guardando en este dispositivo…';timer=setTimeout(()=>backup().catch(()=>{}),400)}
    form.addEventListener('input',autosave);form.addEventListener('change',autosave);
    $('#repairClient').onchange=()=>{capture();equipmentOptions();autosave()};
    $('#repairEquipment').onchange=()=>{capture();autosave()};
    $('#repairTempSign').onclick=()=>{const input=form.elements.temperature;input.value=input.value.startsWith('-')?input.value.slice(1):'-'+input.value;input.focus();autosave()};
    function renderValves(){
      const valves=editor.data.valve_adjustments||[];
      $('#repairValves').innerHTML=valves.map((valve,index)=>`<div class="repair-valve-summary"><span><b>${esc(valve.name||'Válvula '+(index+1))}</b><br><small>${esc(valveLabel(valvePosition(valve.movements)))} · ${valve.movements.length} cambio(s)</small></span><button class="secondary" type="button" data-open-valve="${esc(valve.id)}">Abrir panel</button></div>`).join('');
      $('#repairValves').querySelectorAll('[data-open-valve]').forEach(button=>button.onclick=()=>openValveWorkspace(button.dataset.openValve));
    }
    function closeValveWorkspace(){if(pendingValveSteps&&!confirm('Hay un giro preparado sin aplicar. ¿Salir y descartarlo?'))return false;pendingValveSteps=0;valveWorkspaceId='';$('#repairValveWorkspace').hidden=true;document.body.classList.remove('repair-valve-open');renderValves();return true}
    function openValveWorkspace(id){valveWorkspaceId=id;pendingValveSteps=0;$('#repairValveWorkspace').hidden=false;document.body.classList.add('repair-valve-open');renderValveWorkspace();$('#repairValveClose').focus()}
    function renderValveWorkspace(openStep=-1){
      const valve=(editor.data.valve_adjustments||[]).find(v=>v.id===valveWorkspaceId);if(!valve){closeValveWorkspace();return}
      const steps=valvePosition(valve.movements),box=$('#repairValveWorkspaceBody');
      box.innerHTML=`<label>Nombre de la válvula<input id="repairValveName" value="${esc(valve.name||'')}" maxlength="100" placeholder="Ej. Circuito 1"></label><div class="repair-valve-head">${valveDial(steps)}</div><p class="repair-valve-legend" style="justify-content:center"><span class="origin">Inicio</span><span class="current">Posición actual</span></p><p class="repair-valve-amount">${esc(valveLabel(steps))}</p><div class="repair-valve-control"><h3>Preparar un cambio</h3><p class="muted">Toca cuantas veces necesites. Sólo se registra cuando pulses “Aplicar cambio”.</p><div class="repair-actions"><button class="secondary" type="button" data-prepare="-1">↶ ⅛ izquierda</button><button class="secondary" type="button" data-prepare="1">↷ ⅛ derecha</button></div><p class="repair-valve-amount">${pendingValveSteps?esc(movementLabel(pendingValveSteps)):'Sin giro preparado'}</p><div class="repair-actions"><button class="secondary" id="repairValveClear" type="button" ${pendingValveSteps?'':'disabled'}>Reiniciar</button><button class="primary" id="repairValveApply" type="button" ${pendingValveSteps?'':'disabled'}>Aplicar cambio</button></div></div><h3>Historial de cambios</h3>${valve.movements.length?valve.movements.map((move,index)=>`<details class="repair-valve-step" ${index===openStep?'open':''}><summary>Paso ${index+1} · ${esc(movementLabel(move.steps))}${move.pressure_low?' · Baja '+esc(move.pressure_low)+' psi':''}${move.pressure_high?' · Alta '+esc(move.pressure_high)+' psi':''}</summary><p class="muted">Presiones después de este cambio (opcionales)</p><div class="repair-valve-pressure-grid"><label>Baja · psi<input inputmode="decimal" maxlength="40" data-step-pressure="low" data-step-index="${index}" value="${esc(move.pressure_low||'')}" placeholder="Ej. 33"></label><label>Alta · psi<input inputmode="decimal" maxlength="40" data-step-pressure="high" data-step-index="${index}" value="${esc(move.pressure_high||'')}"></label></div><button class="secondary" type="button" data-remove-step="${index}">Quitar este paso</button></details>`).join(''):'<p class="muted">Todavía no has aplicado cambios.</p>'}<button class="danger" id="repairValveRemove" type="button">Quitar esta válvula</button>`;
      $('#repairValveName').oninput=event=>{valve.name=event.target.value;autosave()};
      function preview(){const position=steps+pendingValveSteps,dial=box.querySelector('.repair-valve-dial');dial.querySelector('.repair-valve-moving').style.transform=`rotate(${position*45}deg)`;dial.setAttribute('aria-label',`Inicio en gris; ${pendingValveSteps?'vista previa':'posición actual'} en verde: ${valveLabel(position)}`);const amounts=box.querySelectorAll('.repair-valve-amount');amounts[0].textContent=pendingValveSteps?`${valveLabel(position)} · vista previa`:valveLabel(steps);amounts[1].textContent=pendingValveSteps?movementLabel(pendingValveSteps):'Sin giro preparado';$('#repairValveClear').disabled=!pendingValveSteps;$('#repairValveApply').disabled=!pendingValveSteps}
      box.querySelectorAll('[data-prepare]').forEach(button=>button.onclick=()=>{pendingValveSteps=Math.max(-80,Math.min(80,pendingValveSteps+Number(button.dataset.prepare)));preview()});
      $('#repairValveClear').onclick=()=>{pendingValveSteps=0;preview()};
      $('#repairValveApply').onclick=()=>{if(!pendingValveSteps)return;valve.movements.push({steps:pendingValveSteps,at:new Date().toISOString(),pressure_low:'',pressure_high:''});pendingValveSteps=0;renderValveWorkspace(valve.movements.length-1);renderValves();autosave();box.querySelector('.repair-valve-step[open] input')?.focus()};
      box.querySelectorAll('[data-step-pressure]').forEach(input=>input.oninput=()=>{const move=valve.movements[Number(input.dataset.stepIndex)];move[input.dataset.stepPressure==='low'?'pressure_low':'pressure_high']=input.value.trim();if(Number(input.dataset.stepIndex)===valve.movements.length-1)form.elements[input.dataset.stepPressure==='low'?'pressure_low':'pressure_high'].value=input.value.trim();autosave()});
      box.querySelectorAll('[data-remove-step]').forEach(button=>button.onclick=()=>{if(!confirm('¿Quitar este cambio del historial de la válvula?'))return;valve.movements.splice(Number(button.dataset.removeStep),1);renderValveWorkspace();renderValves();autosave()});
      $('#repairValveRemove').onclick=()=>{if(!confirm('¿Quitar esta válvula y todos sus cambios?'))return;editor.data.valve_adjustments=editor.data.valve_adjustments.filter(v=>v.id!==valve.id);pendingValveSteps=0;closeValveWorkspace();autosave()};
      preview();
    }
    $('#repairValveClose').onclick=closeValveWorkspace;
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('#repairValveWorkspace').hidden){event.preventDefault();closeValveWorkspace()}});
    $('#repairAddValve').onclick=()=>{editor.data.valve_adjustments ||= [];const valve={id:uid(),name:'',movements:[]};editor.data.valve_adjustments.push(valve);renderValves();autosave();$('#repairValveSection').scrollIntoView({block:'start',behavior:'smooth'});openValveWorkspace(valve.id)};
    function renderPhotos(){clearUrls();$('#repairEditorPhotos').innerHTML=(editor.data.photos||[]).map(p=>`<figure class="repair-photo"><img loading="lazy" src="${esc(photoUrl(editor,p.id))}" alt="${esc(stages[p.stage])}"><figcaption>${esc(stages[p.stage])}</figcaption><input data-caption="${esc(p.id)}" value="${esc(p.caption||'')}" maxlength="200" placeholder="Descripción opcional" aria-label="Descripción de foto"><button class="secondary" type="button" data-remove-photo="${esc(p.id)}">Quitar</button></figure>`).join('');$('#repairEditorPhotos').querySelectorAll('[data-caption]').forEach(el=>el.oninput=()=>{editor.data.photos.find(p=>p.id===el.dataset.caption).caption=el.value;autosave()});$('#repairEditorPhotos').querySelectorAll('[data-remove-photo]').forEach(el=>el.onclick=()=>{editor.data.photos=editor.data.photos.filter(p=>p.id!==el.dataset.removePhoto);editor.files=editor.files.filter(p=>p.id!==el.dataset.removePhoto);renderPhotos();autosave()})}
    async function addPhotos(input){if(photoBusy)return;photoBusy=true;const files=[...input.files],stage=$('#repairPhotoStage').value;input.value='';try{for(let i=0;i<files.length;i++){const file=files[i];$('#repairPhotoStatus').textContent=`Comprimiendo y respaldando ${i+1} de ${files.length}…`;const blob=await api.compress(file);if(blob.size>2*1024*1024)throw Error('La foto sigue pesando más de 2 MB después de comprimirla. Elige otra resolución.');const id=uid();editor.data.photos.push({id,stage,caption:''});editor.files.push({id,blob,name:blob.name||'foto.jpg'});await backup();renderPhotos()}$('#repairPhotoStatus').textContent=editor.data.photos.length+' fotos respaldadas.';}catch(error){$('#repairPhotoStatus').textContent=error.message;api.toast(error.message)}finally{photoBusy=false}}
    $('#repairCamera').onclick=()=>{if(!photoBusy)$('#repairCameraInput').click()};$('#repairGallery').onclick=()=>{if(!photoBusy)$('#repairGalleryInput').click()};$('#repairCameraInput').onchange=event=>addPhotos(event.target);$('#repairGalleryInput').onchange=event=>addPhotos(event.target);
    async function leave(){if(api.current()!=='repairEditor'||!editor)return true;if(photoBusy){api.toast('Espera a que termine el respaldo de las fotos.');return false}if(!$('#repairValveWorkspace').hidden&&!closeValveWorkspace())return false;try{await backup();return true}catch(error){api.toast(error.message);return false}}
    async function deleteDraft(id){
      if(photoBusy){api.toast('Espera a que terminen de guardarse las fotos.');return}
      const row=records.find(r=>r.id===id);
      if(!deletableDraft(row)){api.toast('Sólo se puede eliminar aquí un borrador local no enviado.');return}
      if(!confirm('¿Eliminar este borrador y sus fotos de este dispositivo? Esta acción no borra reparaciones finalizadas.'))return;
      clearTimeout(timer);timer=null;
      try{await serial(()=>storage('delete',{id}));records=records.filter(r=>r.id!==id);remoteIds=remoteIds.filter(value=>value!==id);if(editor?.id===id)editor=null;if(selected===id)selected='';try{localStorage.removeItem('hsc-repair-selected-'+api.account.id)}catch(_){}clearUrls();api.show('repairs');cards();api.toast('Borrador eliminado de este dispositivo.')}
      catch(error){api.toast(error.message)}
    }
    $('#repairDeleteDraft').onclick=()=>deleteDraft(selected);
    $('#repairDeleteVisit').onclick=async()=>{
      const row=currentRow(),button=$('#repairDeleteVisit'),notice=$('#repairDeleteStatus');
      if(!row||row.localState!=='synced'||row.data.status!=='completed')return;
      notice.hidden=false;
      if(!api.shouldSync()){notice.textContent='Para retirar una visita finalizada, cambia a modo en línea y vuelve a intentarlo.';return}
      if(deleteArmedFor!==row.id){deleteArmedFor=row.id;button.textContent='Confirmar retiro de '+(row.data.folio||row.id);notice.textContent='Toca de nuevo para retirar esta visita. Su folio y evidencia quedarán resguardados; no se podrá editar ni abrir su remisión.';clearTimeout(deleteArmTimer);deleteArmTimer=setTimeout(()=>resetDeleteConfirmation(),15000);return}
      button.disabled=true;clearTimeout(deleteArmTimer);notice.textContent='Confirmando retiro en el servidor…';
      try{
        const result=await request('/api/operaciones/repairs/'+encodeURIComponent(row.id)+'/delete',{method:'POST',body:JSON.stringify({mutation_id:uid(),expected_revision:row.data.revision}),headers:{'Content-Type':'application/json'}});
        if(result.repair?.status!=='deleted'||result.repair.id!==row.id)throw Error('El servidor no confirmó el retiro de esta visita.');
        const tombstone={...row,data:result.repair,localState:'synced',files:[]};let localWarning=false;
        try{await put(tombstone)}catch(_){records=[tombstone,...records.filter(item=>item.id!==row.id)];localWarning=true}
        remoteIds=remoteIds.filter(id=>id!==row.id);selected='';resetDeleteConfirmation();try{localStorage.removeItem('hsc-repair-selected-'+api.account.id)}catch(_){}api.show('repairs');cards();api.toast(localWarning?'Visita retirada en el servidor. El respaldo local no se actualizó; vuelve a abrir con conexión.':'Visita '+(result.repair.folio||'')+' retirada del historial.');
      }catch(error){resetDeleteConfirmation(false);notice.textContent='No se pudo confirmar: '+(error.message||'Revisaremos si el servidor recibió la solicitud.');verifyCurrent(row.id).catch(()=>{})}
      finally{button.disabled=false}
    };
    $('#repairDiscardDraft').onclick=()=>deleteDraft(editor?.id);
    $('#repairSaveDraft').onclick=async()=>{if(!await leave())return;const id=editor.id;editor=null;open(id);api.toast('Borrador guardado en este dispositivo.')};
    form.onsubmit=async event=>{event.preventDefault();if(photoBusy)return;capture();if(!form.reportValidity())return;if(!editor.data.symptom||!editor.data.work||!editor.data.result){api.toast('Indica qué fallaba, qué hiciste y cómo quedó.');return}const button=$('#repairFinish');button.disabled=true;try{await backup();const row=structuredClone(editor);row.data={...row.data,status:'completed',expected_revision:row.data.revision||0,mutation_id:uid()};row.localState='pending';await put(row);editor=null;open(row.id);api.toast('Visita guardada. El envío continúa en segundo plano.');sync()}catch(error){api.toast(error.message)}finally{button.disabled=false}};
    async function sync(manual=false){
      if(syncBusy||!api.shouldSync(manual))return;
      const run=async()=>{
        syncBusy=true;let changed=false;
        try{
          await writeChain.catch(()=>{});records=await storage('list');
          const pending=records.filter(r=>r.localState==='pending');
          for(const row of pending){
            status();changed=true;
            try{
              const data=await transmit(row,request);
              const fresh=(await storage('list')).find(r=>r.id===row.id);
              if(fresh?.data.mutation_id!==row.data.mutation_id)continue;
              await put({...row,data,localState:'synced',error:'',base:null});
              remoteIds=[...new Set([...remoteIds,row.id])];
              api.toast('Reparación confirmada: '+data.equipment_name);
            }catch(error){
              row.error=error.message;
              row.localState=[400,403,409,413].includes(error.status)?'blocked':'pending';
              await put(row);if(row.localState==='pending')break;
            }
          }
        }catch(error){lastError=error.message}
        finally{syncBusy=false;status();if(changed){if(api.current()==='repairs')cards();if(api.current()==='repair')detail()}}
      };
      if(navigator.locks)await navigator.locks.request('hsc-repair-sync-'+api.account.id,{ifAvailable:true},lock=>lock?run():undefined);else await run();
    }
    $('#repairResolve').onclick=async()=>{const local=currentRow();try{const data=(await request('/api/operaciones/repairs/'+encodeURIComponent(local.id))).repair;const box=$('#repairCompare');box.hidden=false;box.innerHTML='<h3>Versión confirmada en el servidor</h3><pre></pre><h3>Tu propuesta pendiente</h3><pre></pre><button class="secondary" type="button">Guardar mi propuesta como siguiente revisión</button>';const describe=d=>fields.map(k=>`${form.elements[k]?.closest('label')?.firstChild?.textContent?.trim()||k}: ${d[k]||'—'}`).join('\n')+`\nFotografías: ${(d.photos||[]).length}`;box.querySelectorAll('pre')[0].textContent=describe(data);box.querySelectorAll('pre')[1].textContent=describe(local.data);box.querySelector('button').onclick=async()=>{if(!confirm('¿Confirmas aplicar tu propuesta completa sobre la versión mostrada? La revisión anterior quedará conservada.'))return;try{const row={...local,data:{...local.data,expected_revision:data.revision,revision:data.revision,mutation_id:uid()},localState:'pending',error:''};await put(row);detail();sync(true)}catch(error){api.toast(error.message)}};}catch(error){api.toast(error.message)}};
    $('#repairNew').onclick=()=>edit().catch(e=>api.toast(e.message));$('#repairEdit').onclick=()=>edit(currentRow()).catch(e=>api.toast(e.message));$('#repairRepeat').onclick=()=>edit(currentRow(),true).catch(e=>api.toast(e.message));$('#repairAll').onclick=()=>list();$('#repairSearchGo').onclick=()=>{cards();refresh()};$('#repairSearch').oninput=()=>{cards();clearTimeout(searchTimer);searchTimer=setTimeout(()=>refresh(),400)};$('#repairMore').onclick=()=>refresh(true);
    document.querySelectorAll('[data-open-repairs]').forEach(b=>b.onclick=()=>list(b.dataset.openRepairs==='equipment'?'equipment:'+api.state().selectedEquipment:''));
    window.addEventListener('online',()=>sync());document.addEventListener('visibilitychange',()=>{if(!document.hidden)sync()});window.addEventListener('beforeunload',event=>{if(photoBusy||(api.current()==='repairEditor'&&(timer||lastError))){event.preventDefault();event.returnValue=''}});setInterval(()=>{if(!document.hidden)sync()},30000);
    async function init(){try{records=await storage('list');selected=localStorage.getItem('hsc-repair-selected-'+api.account.id)||'';remoteIds=records.map(r=>r.id);cards();if(api.current()==='repair'){detail();verifyCurrent(selected).catch(()=>{})}if(api.current()==='repairEditor'){if(currentRow())await edit(currentRow());else api.show('repairs')}await sync();await refresh();if(api.current()==='repair')detail()}catch(error){lastError=error.message;status()}}
    init();return {leave,sync,list,isBusy:()=>syncBusy||photoBusy,hasPending:()=>records.some(r=>r.localState!=='synced'),onView:name=>{if(name==='repair')detail();if(name==='repairs')cards()},editorActive:()=>!!editor};
  }
  return {mount,visible,label,deletableDraft,transmit,valvePosition,valveLabel,movementLabel,turnAmount};
});
