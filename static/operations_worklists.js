(function(root){
  'use strict';
  const progress=(list,reports)=>list.items.map(item=>{
    const matches=reports.filter(r=>r.equipment_id===item.equipment_id&&String(r.round)===String(item.round)&&r.completed);
    const report=matches.find(r=>!r.pending_upload)||matches[0];
    return {...item,report_id:report?.id||'',done:Boolean(report),pending_upload:Boolean(report?.pending_upload)};
  });
  function mount(api){
    const $=s=>document.querySelector(s),esc=api.escape,uid=()=>crypto.randomUUID();
    const key=`hsc-worklist-selection-${api.account.id}`;
    const returnButton=document.createElement('button');returnButton.id='returnToWorklist';returnButton.type='button';returnButton.className='secondary';returnButton.textContent='☑ Volver a mi jornada';returnButton.hidden=true;returnButton.onclick=()=>{api.show('worklist');render()};document.querySelector('[data-view="equipment"]').prepend(returnButton);
    let selected='';try{selected=localStorage.getItem(key)||''}catch(_){}
    let editor=null,busy=false;
    const today=()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`};
    const dateLabel=value=>new Date(value+'T12:00:00').toLocaleDateString('es-MX',{day:'numeric',month:'short',year:'numeric'});
    const clientName=id=>api.state().clients.find(c=>c.id===id)?.name||id;
    const personName=id=>id===api.account.id?api.account.name:api.state().users.find(u=>u.id===id)?.name||'Técnico';
    const row=()=>api.state().worklists.find(l=>l.id===selected&&(l.status!=='deleted'||l.sync_error));
    const choose=id=>{selected=id;try{localStorage.setItem(key,id)}catch(_){}api.show('worklist');render()};
    const equipmentName=id=>api.state().equipment.find(e=>e.id===id)?.name||'Equipo no disponible';
    function render(){
      const state=api.state(),all=state.worklists.filter(l=>(l.status!=='deleted'||l.sync_error)&&(api.account.isOwner||l.assigned_user_id===api.account.id));
      const active=all.filter(l=>l.status==='active');
      $('#worklistSummary').textContent=active.length?`${active.length} lista(s) abiertas · se conservan aunque cambie el día`:'Organiza los equipos en el orden que vas a trabajarlos';
      const history=$('#worklistFilter').value==='completed';
      const visible=all.filter(l=>(l.status==='completed')===history).sort((a,b)=>a.target_date.localeCompare(b.target_date)||a.title.localeCompare(b.title));
      $('#worklistCards').innerHTML=visible.map(list=>{
        const n=progress(list,state.reports).filter(i=>i.done).length;
        return `<button class="user-card" data-worklist="${esc(list.id)}"><span class="reminder-icon">☑</span><span class="card-copy"><b>${esc(list.title)}</b><small>${esc(clientName(list.client_id))} · ${esc(personName(list.assigned_user_id))}</small><small>${esc(dateLabel(list.target_date))} · ${n}/${list.items.length} con reporte${list.pending_upload?' · Cambios pendientes de enviar':''}</small></span><span class="chevron">›</span></button>`;
      }).join('')||'<div class="empty">No hay listas en esta sección.</div>';
      document.querySelectorAll('[data-worklist]').forEach(b=>b.onclick=()=>choose(b.dataset.worklist));
      const list=row();
      returnButton.hidden=!list||!list.items.some(i=>i.equipment_id===state.selectedEquipment);
      $('#worklistDetailContent').hidden=!list;$('#worklistMissing').hidden=Boolean(list);
      if(!list)return;
      $('#worklistTitle').textContent=list.title;
      $('#worklistConflict').hidden=!list.sync_error;$('#worklistConflictText').textContent=list.sync_error||'';
      const items=progress(list,state.reports);
      $('#worklistInfo').textContent=`${clientName(list.client_id)} · ${personName(list.assigned_user_id)} · ${dateLabel(list.target_date)} · ${items.filter(i=>i.done).length}/${items.length} con reporte${list.pending_upload?' · Guardada aquí, pendiente de enviar':''}`;
      $('#worklistClose').textContent=list.status==='completed'?'Reabrir lista':'Cerrar lista';
      $('#worklistItems').innerHTML=items.map((item,index)=>`<button class="equipment-card" data-work-item="${esc(item.id)}"><span class="reminder-icon">${item.done?'✓':index+1}</span><span class="card-copy"><b>${esc(equipmentName(item.equipment_id))}</b><small>${esc(item.equipment_id)} · R${esc(item.round)} · ${item.pending_upload?'Reporte guardado aquí · pendiente de enviar':item.done?'Ya tiene reporte':'Por hacer'}</small></span><span class="chevron">›</span></button>`).join('');
      document.querySelectorAll('[data-work-item]').forEach(b=>b.onclick=()=>{const item=list.items.find(i=>i.id===b.dataset.workItem);api.openEquipment(list.client_id,item.equipment_id,item.round);render()});
    }
    function renderPicker(){
      const selectedIds=new Set(editor.items.map(i=>i.equipment_id)),client=$('#worklistClient').value,search=$('#worklistSearch').value.trim().toLocaleLowerCase();
      const candidates=api.state().equipment.filter(e=>e.client_id===client&&e.active!==false&&!selectedIds.has(e.id)&&`${e.id} ${e.name}`.toLocaleLowerCase().includes(search));
      $('#worklistChoices').innerHTML=candidates.map(e=>`<button type="button" class="secondary work-choice" data-add-work="${esc(e.id)}">＋ ${esc(e.id)} · ${esc(e.name)}</button>`).join('')||'<p class="muted">No hay más equipos con ese filtro.</p>';
      $('#worklistSelected').innerHTML=editor.items.map((item,index)=>`<div class="work-selected"><span class="card-copy">${index+1}. ${esc(equipmentName(item.equipment_id))}<small>${esc(item.equipment_id)} · R${esc(item.round)}</small></span><span class="work-order"><button type="button" class="mini-btn" data-move-work="${index}" data-step="-1" aria-label="Subir ${esc(item.equipment_id)}" ${index===0?'disabled':''}>↑</button><button type="button" class="mini-btn" data-move-work="${index}" data-step="1" aria-label="Bajar ${esc(item.equipment_id)}" ${index===editor.items.length-1?'disabled':''}>↓</button><button type="button" class="mini-btn" data-remove-work="${index}" aria-label="Quitar ${esc(item.equipment_id)}">×</button></span></div>`).join('')||'<p class="muted">Agrega equipos a tu recorrido.</p>';
      $('#worklistSelectedCount').textContent=`Tu orden de trabajo · ${editor.items.length} equipos`;
      document.querySelectorAll('[data-add-work]').forEach(b=>b.onclick=()=>{if(editor.items.length>=200){api.toast('Máximo 200 equipos por lista.');return;}editor.items.push({id:uid(),equipment_id:b.dataset.addWork,round:$('#worklistRound').value});renderPicker()});
      document.querySelectorAll('[data-move-work]').forEach(b=>b.onclick=()=>{const i=Number(b.dataset.moveWork),j=i+Number(b.dataset.step);[editor.items[i],editor.items[j]]=[editor.items[j],editor.items[i]];renderPicker()});
      document.querySelectorAll('[data-remove-work]').forEach(b=>b.onclick=()=>{editor.items.splice(Number(b.dataset.removeWork),1);renderPicker()});
    }
    function edit(list=null){
      editor=list?JSON.parse(JSON.stringify(list)):{id:uid(),title:'Mi jornada',client_id:api.state().selectedClient||api.state().clients[0]?.id||'',assigned_user_id:api.account.id,target_date:today(),items:[],status:'active',revision:0};
      $('#worklistName').value=editor.title;$('#worklistDate').value=editor.target_date;
      $('#worklistClient').innerHTML=api.state().clients.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');$('#worklistClient').value=editor.client_id;
      const people=[{id:api.account.id,name:api.account.name},...api.state().users.filter(u=>u.role==='technician'&&u.status==='active'&&u.id!==api.account.id)];
      if(!people.some(p=>p.id===editor.assigned_user_id))people.push({id:editor.assigned_user_id,name:'Responsable de esta lista'});
      $('#worklistAssignee').innerHTML=people.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');$('#worklistAssignee').value=editor.assigned_user_id;$('#worklistAssigneeField').hidden=!api.account.isOwner;
      $('#worklistRound').value=api.round(editor.client_id);$('#worklistSearch').value='';$('#worklistEditorError').textContent='';renderPicker();$('#worklistEditor').classList.add('open');
      if(api.account.isOwner)api.loadUsers().then(()=>{if(!$('#worklistEditor').classList.contains('open'))return;const chosen=$('#worklistAssignee').value;for(const user of api.state().users.filter(u=>u.role==='technician'&&u.status==='active'))if(![...$('#worklistAssignee').options].some(o=>o.value===user.id))$('#worklistAssignee').add(new Option(user.name,user.id));$('#worklistAssignee').value=chosen}).catch(()=>api.toast('No se pudo actualizar la lista de técnicos; puedes trabajar con los que ya están cargados.'));
    }
    async function save(list){
      if(busy)return false;busy=true;$('#saveWorklist').disabled=true;
      try{const mutation=uid();await api.save({id:`worklist:${mutation}`,url:'/api/operaciones/worklists',body:{...list,mutation_id:mutation,expected_revision:list.revision||0},files:[],label:'Guardar jornada'});api.send();return true}
      catch(error){api.toast(error.message);$('#worklistEditorError').textContent=error.message;return false}
      finally{busy=false;$('#saveWorklist').disabled=false}
    }
    $('#openWorklists').onclick=()=>{api.show('worklists');render();if(api.account.isOwner)api.loadUsers().then(render).catch(()=>{})};
    $('#newWorklist').onclick=()=>edit();$('#worklistFilter').onchange=render;
    $('#worklistEdit').onclick=()=>{if(row())edit(row())};
    $('#closeWorklistEditor').onclick=()=>$('#worklistEditor').classList.remove('open');
    $('#worklistSearch').oninput=renderPicker;
    $('#worklistClient').onchange=()=>{if(editor.items.length&&!confirm('Cambiar de cliente quitará los equipos de esta lista, no sus reportes.')){$('#worklistClient').value=editor.client_id;return;}editor.client_id=$('#worklistClient').value;editor.items=[];$('#worklistRound').value=api.round(editor.client_id);renderPicker()};
    $('#worklistForm').onsubmit=async e=>{e.preventDefault();if(!editor.items.length){$('#worklistEditorError').textContent='Agrega al menos un equipo.';return;}const result={...editor,title:$('#worklistName').value.trim(),target_date:$('#worklistDate').value,assigned_user_id:$('#worklistAssignee').value};if(await save(result)){$('#worklistEditor').classList.remove('open');choose(result.id);api.toast('Lista guardada en este dispositivo; se enviará según tu preferencia.')}};
    $('#worklistTomorrow').onclick=async()=>{const list=row();if(!list)return;const d=new Date();d.setDate(d.getDate()+1);const date=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;await save({...list,target_date:date,status:'active'});render()};
    $('#worklistClose').onclick=async()=>{const list=row();if(!list)return;if(list.status==='active'&&progress(list,api.state().reports).some(i=>!i.done)&&!confirm('Quedan equipos sin reporte. ¿Cerrar sólo esta lista? No modifica equipos ni reportes.'))return;await save({...list,status:list.status==='active'?'completed':'active'});render()};
    $('#worklistDelete').onclick=async()=>{const list=row();if(list&&confirm('¿Eliminar esta lista? Los equipos y sus reportes se conservan.'))if(await save({...list,status:'deleted'})){api.show('worklists');render()}};
    $('#backToWorklists').onclick=()=>api.show('worklists');
    $('#worklistRestoreServer').onclick=async()=>{if(!confirm('¿Descartar los cambios pendientes de esta lista en este dispositivo y recuperar la versión confirmada? Los reportes y otras listas se conservan.'))return;try{await api.discardProposal(selected);render()}catch(error){api.toast(error.message)}};
    return {render};
  }
  const api={mount,progress};if(typeof module!=='undefined')module.exports=api;else root.HscWorklists=api;
})(typeof window!=='undefined'?window:globalThis);
