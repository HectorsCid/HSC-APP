(function(root,factory){const lib=factory();if(typeof module==='object'&&module.exports)module.exports=lib;else root.HscRepairs=lib})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const fields=['client_name','equipment_name','address','model','serial','location','service_date','symptom','diagnosis','work','parts','result','recommendations','refrigerant','pressure_low','pressure_high','temperature','amperage','measurement_notes','received_by','outcome'];
  const stages={before:'Antes',during:'Durante',after:'Después'},outcomes={working:'Trabajando bien',follow_up:'Requiere seguimiento',stopped:'Fuera de servicio'};
  function visible(row,query='',equipment=''){const d=row.data;return (!equipment||d.equipment_key===equipment)&&(!query||JSON.stringify(d).toLocaleLowerCase().includes(query.toLocaleLowerCase()))}
  function label(row){return row.localState==='pending'?'Pendiente de envío':row.localState==='blocked'?'Revisar envío':row.localState==='draft'?'Borrador en este dispositivo':row.data.status==='completed'?'Visita finalizada':row.data.status==='uploading'?'Fotos pendientes':'Borrador'}
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
    let records=[],selected='',equipmentFilter='',next=null,remoteIds=[],editor=null,writeChain=Promise.resolve(),timer,searchTimer,photoBusy=false,syncBusy=false,loadToken=0,lastError='',urls=[];
    const access=api.account.isOwner||api.role==='technician';
    if(!access)return null;
    const banner=document.createElement('div');banner.id='repairSyncBanner';banner.className='repair-sync-notice';banner.hidden=true;banner.innerHTML='<span></span> <button class="mini-btn" type="button">Enviar reparaciones</button>';$('.topbar').after(banner);banner.querySelector('button').onclick=()=>sync(true);
    function db(){return new Promise((resolve,reject)=>{const req=indexedDB.open('hsc-repairs-'+api.account.id,1);req.onupgradeneeded=()=>req.result.createObjectStore('records',{keyPath:'id'});req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(Error('No se pudo abrir el respaldo local de reparaciones.'))})}
    async function storage(mode,item){const connection=await db();try{return await new Promise((resolve,reject)=>{const tx=connection.transaction('records',mode==='list'?'readonly':'readwrite'),store=tx.objectStore('records'),req=mode==='list'?store.getAll():store.put(item);tx.oncomplete=()=>resolve(req.result);tx.onerror=()=>reject(Error('No se pudo respaldar la reparación. Revisa el espacio disponible; no cierres la captura.'));tx.onabort=tx.onerror})}finally{connection.close()}}
    function serial(job){const promise=writeChain.catch(()=>{}).then(job);writeChain=promise;return promise}
    async function put(row){await serial(()=>storage('put',row));records=[row,...records.filter(r=>r.id!==row.id)];status()}
    async function request(url,options={}){const response=await api.fetch(url,options,45000);let payload;try{payload=await response.json()}catch(_){throw Error('Respuesta incompleta; se conserva la reparación para reintentar.')}if(!response.ok||!payload.ok)throw Object.assign(Error(payload.error||'No se confirmó el envío.'),{status:response.status});return payload}
    function currentRow(){return records.find(r=>r.id===selected)}
    function status(){const pending=records.filter(r=>['pending','blocked'].includes(r.localState));banner.hidden=!pending.length;banner.querySelector('span').textContent=pending.length+' reparación(es) pendientes · '+(pending.find(r=>r.error)?.error||'Respaldadas en este dispositivo.');banner.querySelector('button').disabled=syncBusy;$('#repairConnection').textContent=lastError||'El historial consultado y los borradores quedan disponibles en este dispositivo.';const count=records.filter(r=>r.localState==='draft').length;document.querySelectorAll('[data-repair-summary]').forEach(el=>el.textContent=pending.length?pending.length+' pendientes de envío':count?count+' borradores':'Historial por cliente y equipo')}
    function photoUrl(row,pid,full=false){const file=(row.files||[]).find(f=>f.id===pid);if(file){const url=URL.createObjectURL(file.blob);urls.push(url);return url}return '/api/operaciones/repairs/'+encodeURIComponent(row.id)+'/photos/'+encodeURIComponent(pid)+(full?'?full=1':'')}
    function clearUrls(){urls.forEach(url=>URL.revokeObjectURL(url));urls=[]}
    function summary(data){return (data.result||data.symptom||'Sin observaciones todavía').slice(0,180)}
    function cards(){const query=$('#repairSearch').value.trim();const rows=records.filter(r=>visible(r,query,equipmentFilter)&&(r.localState!=='synced'||remoteIds.includes(r.id)||!navigator.onLine||lastError)).sort((a,b)=>String(b.data.service_date).localeCompare(String(a.data.service_date))||b.id.localeCompare(a.id));$('#repairCards').innerHTML=rows.map(row=>`<button class="user-card repair-card" type="button" data-open-repair="${esc(row.id)}"><span class="repair-state">${esc(label(row))}</span><small>${esc(row.data.service_date)} · ${esc(row.data.client_name)}</small><b>${esc(row.data.equipment_name)}</b><small>${esc(summary(row.data))}</small><small>${esc(row.data.author||api.account.name)} · ${(row.data.photos||[]).length} fotos</small></button>`).join('')||'<p class="empty">Aún no hay reparaciones en esta consulta. Puedes registrar la primera visita.</p>';$('#repairCards').querySelectorAll('[data-open-repair]').forEach(b=>b.onclick=()=>open(b.dataset.openRepair));$('#repairScope').textContent=equipmentFilter?'Historial del equipo seleccionado · todas las rondas':'Todos los clientes y equipos';$('#repairMore').hidden=next===null;status()}
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
      $('#repairPhotoHistory').innerHTML=(d.photos||[]).map(p=>`<figure class="repair-photo"><a href="${esc(photoUrl(row,p.id,true))}" target="_blank" rel="noopener"><img loading="lazy" src="${esc(photoUrl(row,p.id))}" alt="${esc(p.caption||stages[p.stage])}"></a><figcaption>${esc(stages[p.stage])} · ${esc(p.caption||'')}</figcaption></figure>`).join('');
      const own=api.account.isOwner||d.created_by===api.account.id||!d.created_by;
      $('#repairEdit').hidden=!own||!api.canWrite();
      $('#repairEdit').disabled=row.localState==='pending';
      $('#repairEdit').textContent=row.localState==='blocked'?'Corregir propuesta local':'Editar esta visita';
      $('#repairRepeat').hidden=!api.canWrite();
      $('#repairRemision').hidden=row.localState!=='synced'||d.status!=='completed';
      $('#repairRemision').href='/api/operaciones/repairs/'+encodeURIComponent(row.id)+'/remision';
      $('#repairResolve').hidden=row.localState!=='blocked';$('#repairCompare').hidden=true;
    }
    function open(id){selected=id;try{localStorage.setItem('hsc-repair-selected-'+api.account.id,id)}catch(_){}detail();api.show('repair')}
    function option(value,text){return `<option value="${esc(value)}">${esc(text)}</option>`}
    function clientsOptions(){const state=api.state(),known=state.clients;const extras=new Map(records.filter(r=>!r.data.client_id).map(r=>[r.data.client_key,r.data.client_name]));$('#repairClient').innerHTML=option('','Nuevo / fuera del catálogo')+known.map(c=>option('client:'+c.id,c.name)).join('')+[...extras].map(([id,name])=>option(id,name+' · bitácora')).join('')}
    function equipmentOptions(){const data=editor.data,state=api.state();const known=state.equipment.filter(e=>e.client_id===data.client_id&&data.client_id);const extras=new Map(records.filter(r=>r.data.client_key===data.client_key&&!r.data.equipment_id).map(r=>[r.data.equipment_key,r.data.equipment_name]));$('#repairEquipment').innerHTML=option('','Nuevo / fuera del catálogo')+known.map(e=>option('equipment:'+e.id,e.name)).join('')+[...extras].map(([id,name])=>option(id,name+' · bitácora')).join('')}
    function fill(){fields.forEach(k=>form.elements[k].value=editor.data[k]||'');$('#repairClient').value=editor.data.client_id?'client:'+editor.data.client_id:editor.data.client_key;$('#repairEquipment').value=editor.data.equipment_id?'equipment:'+editor.data.equipment_id:editor.data.equipment_key;const persisted=(editor.data.revision||0)>0;$('#repairClient').disabled=persisted;$('#repairEquipment').disabled=persisted;form.elements.client_name.readOnly=!!editor.data.client_id;form.elements.equipment_name.readOnly=!!editor.data.equipment_id;renderPhotos()}
    async function edit(row=null,repeat=false){if(!api.canWrite()){api.toast('No tienes permiso para capturar reportes.');return}if(syncBusy){api.toast('Espera a que termine el envío actual.');return}if(row&&!repeat&&row.localState==='pending'){api.toast('Primero confirma el envío pendiente.');return}clearUrls();const old=row?.data||{},state=api.state(),id=repeat||!row?'REP_'+uid():row.id;const localDate=new Date();localDate.setMinutes(localDate.getMinutes()-localDate.getTimezoneOffset());const context=equipmentFilter?state.equipment.find(e=>'equipment:'+e.id===equipmentFilter):null,client=state.clients.find(c=>c.id===context?.client_id);let data;
      if(row&&!repeat)data={...old,photos:(old.photos||[]).map(p=>({...p}))};else{data={id,revision:0,client_id:old.client_id||client?.id||'',equipment_id:old.equipment_id||context?.id||'',client_key:old.client_key||(client?'client:'+client.id:'external:'+uid()),equipment_key:old.equipment_key||(context?'equipment:'+context.id:'external:'+uid()),client_name:old.client_name||client?.name||'',equipment_name:old.equipment_name||context?.name||'',address:old.address||'',model:old.model||context?.model||'',serial:old.serial||context?.serial||'',location:old.location||context?.location||'',service_date:localDate.toISOString().slice(0,10),photos:[],outcome:'working',created_by:api.account.id,author:api.account.name};}
      editor={id,data,localState:'draft',files:row&&!repeat?[...(row.files||[])]:[],base:row?.base||((old.revision||0)>0?old:null)};selected=id;form.reset();clientsOptions();equipmentOptions();fill();$('#repairDraftStatus').textContent='Los cambios se respaldan automáticamente en este dispositivo.';api.show('repairEditor');await backup();
    }
    function capture(){if(!editor)return;fields.forEach(k=>editor.data[k]=form.elements[k].value.trim())}
    async function backup(){clearTimeout(timer);timer=null;if(!editor)return true;capture();const row=structuredClone(editor);try{await put(row);$('#repairDraftStatus').textContent='Borrador respaldado en este dispositivo.';lastError='';return true}catch(error){lastError=error.message;$('#repairDraftStatus').textContent=error.message;throw error}}
    function autosave(){clearTimeout(timer);$('#repairDraftStatus').textContent='Guardando en este dispositivo…';timer=setTimeout(()=>backup().catch(()=>{}),400)}
    form.addEventListener('input',autosave);form.addEventListener('change',autosave);
    $('#repairClient').onchange=()=>{capture();const value=$('#repairClient').value,state=api.state(),found=state.clients.find(c=>'client:'+c.id===value),other=records.find(r=>r.data.client_key===value)?.data;Object.assign(editor.data,{client_id:found?.id||'',client_key:value||'external:'+uid(),client_name:found?.name||other?.client_name||'',address:found?.address||other?.address||'',equipment_id:'',equipment_key:'external:'+uid(),equipment_name:'',model:'',serial:'',location:''});equipmentOptions();fill();autosave()};
    $('#repairEquipment').onchange=()=>{capture();const value=$('#repairEquipment').value,found=api.state().equipment.find(e=>'equipment:'+e.id===value),other=records.find(r=>r.data.equipment_key===value)?.data;Object.assign(editor.data,{equipment_id:found?.id||'',equipment_key:value||'external:'+uid(),equipment_name:found?.name||other?.equipment_name||'',model:found?.model||other?.model||'',serial:found?.serial||other?.serial||'',location:found?.location||other?.location||''});fill();autosave()};
    function renderPhotos(){clearUrls();$('#repairEditorPhotos').innerHTML=(editor.data.photos||[]).map(p=>`<figure class="repair-photo"><img loading="lazy" src="${esc(photoUrl(editor,p.id))}" alt="${esc(stages[p.stage])}"><figcaption>${esc(stages[p.stage])}</figcaption><input data-caption="${esc(p.id)}" value="${esc(p.caption||'')}" maxlength="200" placeholder="Descripción opcional" aria-label="Descripción de foto"><button class="secondary" type="button" data-remove-photo="${esc(p.id)}">Quitar</button></figure>`).join('');$('#repairEditorPhotos').querySelectorAll('[data-caption]').forEach(el=>el.oninput=()=>{editor.data.photos.find(p=>p.id===el.dataset.caption).caption=el.value;autosave()});$('#repairEditorPhotos').querySelectorAll('[data-remove-photo]').forEach(el=>el.onclick=()=>{editor.data.photos=editor.data.photos.filter(p=>p.id!==el.dataset.removePhoto);editor.files=editor.files.filter(p=>p.id!==el.dataset.removePhoto);renderPhotos();autosave()})}
    async function addPhotos(input){if(photoBusy)return;photoBusy=true;const files=[...input.files],stage=$('#repairPhotoStage').value;input.value='';try{for(let i=0;i<files.length;i++){const file=files[i];$('#repairPhotoStatus').textContent=`Comprimiendo y respaldando ${i+1} de ${files.length}…`;const blob=await api.compress(file);if(blob.size>2*1024*1024)throw Error('La foto sigue pesando más de 2 MB después de comprimirla. Elige otra resolución.');const id=uid();editor.data.photos.push({id,stage,caption:''});editor.files.push({id,blob,name:blob.name||'foto.jpg'});await backup();renderPhotos()}$('#repairPhotoStatus').textContent=editor.data.photos.length+' fotos respaldadas.';}catch(error){$('#repairPhotoStatus').textContent=error.message;api.toast(error.message)}finally{photoBusy=false}}
    $('#repairCamera').onclick=()=>{if(!photoBusy)$('#repairCameraInput').click()};$('#repairGallery').onclick=()=>{if(!photoBusy)$('#repairGalleryInput').click()};$('#repairCameraInput').onchange=event=>addPhotos(event.target);$('#repairGalleryInput').onchange=event=>addPhotos(event.target);
    async function leave(){if(api.current()!=='repairEditor'||!editor)return true;if(photoBusy){api.toast('Espera a que termine el respaldo de las fotos.');return false}try{await backup();return true}catch(error){api.toast(error.message);return false}}
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
    async function init(){try{records=await storage('list');selected=localStorage.getItem('hsc-repair-selected-'+api.account.id)||'';remoteIds=records.map(r=>r.id);cards();if(api.current()==='repair')detail();if(api.current()==='repairEditor'){if(currentRow())await edit(currentRow());else api.show('repairs')}await sync();await refresh();if(api.current()==='repair')detail()}catch(error){lastError=error.message;status()}}
    init();return {leave,sync,list,isBusy:()=>syncBusy||photoBusy,hasPending:()=>records.some(r=>r.localState!=='synced'),onView:name=>{if(name==='repair')detail();if(name==='repairs')cards()},editorActive:()=>!!editor};
  }
  return {mount,visible,label,transmit};
});
