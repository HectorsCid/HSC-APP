/* HSC administrator equipment removal v1 */
(function(root){
  'use strict';
  function mount(api){
    const button=document.getElementById('removeOperationalEquipment');
    if(!button||!api.isOwner)return null;
    const modal=document.createElement('div');modal.className='modal';modal.id='equipmentRemovalModal';
    modal.innerHTML='<div class="sheet" role="dialog" aria-modal="true" aria-labelledby="equipmentRemovalTitle"><h2 id="equipmentRemovalTitle">Retirar equipo</h2><p id="equipmentRemovalName"></p><div id="equipmentRemovalDetails"></div><p id="equipmentRemovalError" role="alert"></p><div class="sheet-actions"><button type="button" class="secondary" id="equipmentRemovalClose">Cerrar</button><button type="button" class="primary" id="equipmentRemovalConfirm" hidden>Confirmar</button></div></div>';
    document.body.appendChild(modal);
    const find=id=>modal.querySelector('#'+id),confirm=find('equipmentRemovalConfirm'),close=find('equipmentRemovalClose');
    let plan=null,busy=false,opener=null;
    function dismiss(){if(busy)return;modal.classList.remove('open');plan=null;opener?.focus();}
    close.onclick=dismiss;
    modal.addEventListener('keydown',event=>{if(event.key==='Escape'){event.stopPropagation();dismiss();}});
    function message(text){find('equipmentRemovalError').textContent=text;}
    button.onclick=async()=>{
      if(busy)return;
      const selected=api.selected();if(!selected?.id)return;
      if(selected.pendingUpload){api.toast('Primero sincroniza los cambios pendientes de este equipo antes de retirarlo.');return;}
      if(!navigator.onLine){api.toast('Conéctate a internet para revisar y retirar un equipo.');return;}
      opener=button;plan=null;busy=true;confirm.hidden=true;close.disabled=true;
      find('equipmentRemovalName').textContent=selected.id+' · '+selected.name;
      find('equipmentRemovalDetails').textContent='Revisando reportes, fotos y movimientos…';message('');
      modal.classList.add('open');close.focus();
      try{
        plan=await api.post('/api/operaciones/equipment/'+encodeURIComponent(selected.id)+'/removal-preview',{});
        if(plan.already_done){find('equipmentRemovalDetails').textContent='Este equipo ya fue eliminado o dado de baja. Actualiza la lista.';return;}
        const archive=plan.action==='archive';
        find('equipmentRemovalTitle').textContent=archive?'Dar de baja al equipo':'Eliminar equipo';
        const descriptions=Object.entries(plan.references||{}).map(([label,count])=>api.escape(label)+': '+Number(count));
        find('equipmentRemovalDetails').innerHTML=(archive?
          '<p>Saldrá del catálogo activo. Se conservarán sus reportes, fotografías, movimientos y listas.</p><p>'+descriptions.join('<br>')+'</p>':
          '<p>No tiene reportes, fotos ni movimientos vinculados. Se eliminará del catálogo; quedará un respaldo administrativo.</p>')+
          '<p>La sincronización con la Hoja Matriz no lo volverá a agregar. Su ID quedará reservado.</p>';
        confirm.textContent=archive?'Confirmar baja':'Confirmar eliminación';confirm.hidden=false;
      }catch(error){plan=null;find('equipmentRemovalDetails').textContent='No se ha confirmado ninguna acción.';message(error.message||'No se pudo revisar el equipo.');}
      finally{busy=false;close.disabled=false;close.focus();}
    };
    confirm.onclick=async()=>{
      if(busy||!plan||plan.already_done)return;
      const target=plan;busy=true;confirm.disabled=true;close.disabled=true;message('');
      confirm.textContent=target.action==='archive'?'Dando de baja…':'Eliminando…';
      try{
        const result=await api.post('/api/operaciones/equipment/'+encodeURIComponent(target.id)+'/remove',{
          confirm:true,fingerprint:target.fingerprint});
        if(!result.applied&&!result.already_done)throw Error('El servidor no confirmó la acción. Vuelve a consultar.');
        plan=null;confirm.hidden=true;
        find('equipmentRemovalDetails').textContent=result.action==='archive'?'Equipo dado de baja. Su historial se conservó.':'Equipo eliminado del catálogo.';
        if(await api.refresh()===false){message('La acción se guardó, pero no se pudo actualizar la lista. Recarga la aplicación.');return;}
        modal.classList.remove('open');api.showClient();api.toast(result.action==='archive'?'Equipo dado de baja; historial conservado.':'Equipo eliminado.');
      }catch(error){plan=null;confirm.hidden=true;message((error.message||'No se pudo confirmar la acción.')+' Puedes cerrar y volver a consultar el equipo.');}
      finally{busy=false;confirm.disabled=false;close.disabled=false;close.focus();}
    };
    return {dismiss};
  }
  root.HscEquipmentRetirement={mount};
})(typeof window!=='undefined'?window:globalThis);
