(function(root){
  'use strict';
  function mount(api){
    const esc=api.escape;
    const panel=document.createElement('section');panel.className='workspace-panel partner-worklists';
    panel.innerHTML='<div class="section-head"><h2>Listas de equipos</h2><button type="button" class="primary" id="partnerGenerateList">＋ Generar lista</button></div><div id="partnerWorklistCards"></div>';
    document.querySelector('.partner-summary').after(panel);
    const modal=document.createElement('div');modal.className='modal';modal.id='partnerListEditor';
    modal.innerHTML='<div class="sheet"><h2>Generar lista</h2><form id="partnerListForm"><div class="field"><label for="partnerListName">Nombre de la lista</label><input id="partnerListName" maxlength="120" required placeholder="Ej. Equipos para mantenimiento"></div><p>Selecciona los equipos que quieres incluir.</p><div id="partnerListChoices" class="work-scroll"></div><p id="partnerListError" role="alert"></p><div class="sheet-actions"><button type="button" class="secondary" id="partnerListCancel">Cerrar</button><button type="submit" class="primary" id="partnerListSave">Guardar lista</button></div></form></div>';
    document.body.append(modal);
    const $=id=>document.getElementById(id);
    document.querySelector('.partner-summary').append($('partnerGenerateList'));
    let draft=null,busy=false;
    $('partnerGenerateList').hidden=!api.canCreate;
    function render(){
      const state=api.state();
      const lists=state.worklists.filter(l=>l.client_id===state.clientId&&l.origin==='client'&&l.status!=='deleted');
      $('partnerWorklistCards').innerHTML=lists.map(list=>`<details class="client-worklist-card"><summary><b>${esc(list.title)}</b> · ${list.items.length} equipos${list.status==='completed'?' · Cerrada':''}</summary><ul>${list.items.map(item=>`<li>${esc(item.equipment_id)} · ${esc(state.equipment.find(e=>e.id===item.equipment_id)?.name||'Equipo')} · R${esc(item.round)}</li>`).join('')}</ul></details>`).join('')||'<p class="muted">Todavía no hay listas para esta empresa.</p>';
    }
    $('partnerGenerateList').onclick=()=>{
      if(busy||!api.canCreate)return;
      const state=api.state();
      draft={id:crypto.randomUUID(),mutation_id:crypto.randomUUID(),client_id:state.clientId,round:String(state.round)};
      $('partnerListName').value='';$('partnerListError').textContent='';
      $('partnerListChoices').innerHTML=state.equipment.filter(e=>e.client_id===state.clientId&&e.active!==false).map(e=>`<label class="partner-list-choice"><input type="checkbox" value="${esc(e.id)}"> <span>${esc(e.id)} · ${esc(e.name)}</span></label>`).join('');
      modal.classList.add('open');$('partnerListName').focus();
    };
    $('partnerListCancel').onclick=()=>{if(!busy)modal.classList.remove('open');};
    $('partnerListForm').onsubmit=async event=>{
      event.preventDefault();if(busy||!draft)return;
      const selected=[...$('partnerListChoices').querySelectorAll('input:checked')];
      if(!selected.length||selected.length>200){$('partnerListError').textContent='Selecciona entre 1 y 200 equipos.';return;}
      const date=new Date(),today=`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
      busy=true;$('partnerListSave').disabled=true;$('partnerListSave').textContent='Guardando…';
      try{
        const result=await api.save({id:draft.id,mutation_id:draft.mutation_id,expected_revision:0,client_id:draft.client_id,title:$('partnerListName').value.trim(),target_date:today,status:'active',items:selected.map(input=>({id:crypto.randomUUID(),equipment_id:input.value,round:draft.round}))});
        api.accept(result.worklist);modal.classList.remove('open');draft=null;render();api.toast('Lista guardada. HSC ya puede verla en tu empresa.');
      }catch(error){$('partnerListError').textContent=error.message;}
      finally{busy=false;$('partnerListSave').disabled=false;$('partnerListSave').textContent='Guardar lista';}
    };
    render();return {render};
  }
  root.HscPartnerLists={mount};
})(typeof window!=='undefined'?window:globalThis);
