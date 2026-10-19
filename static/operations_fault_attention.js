(function(root){
  'use strict';
  function mount(api){
    const $=id=>document.getElementById(id),esc=api.escape;
    const history=document.createElement('section');history.id='faultAttentionHistory';
    $('faultResolutionSummary').after(history);
    const photos=document.createElement('div');photos.id='faultAttentionPhotos';
    photos.innerHTML='<h3>Fotos del trabajo realizado</h3><p>Hasta 6 fotos por atención · no genera PDF.</p><div id="faultAttentionSlots" class="report-evidence-slot-grid"></div><label class="secondary photo-trigger">Elegir varias del teléfono<input id="faultAttentionBulk" type="file" accept="image/*" multiple hidden></label><p id="faultAttentionMessage" role="status"></p>';
    $('faultResolutionField').append(photos);
    let faultId='',slots=Array(6).fill(null),busy=false,ident='',frozen=null,draftNotes='',draftStatus='Revisada';
    function clear(){slots.forEach(p=>{if(p)URL.revokeObjectURL(p.url);});slots=Array(6).fill(null);ident='';frozen=null;draftNotes='';draftStatus='Revisada';}
    const dirty=()=>api.canWrite&&(slots.some(Boolean)||frozen||draftNotes.trim());
    $('faultResolutionNotes').oninput=()=>draftNotes=$('faultResolutionNotes').value;
    $('faultAttentionStatus').onchange=()=>draftStatus=$('faultAttentionStatus').value;
    root.addEventListener?.('beforeunload',event=>{if(busy||dirty()){event.preventDefault();event.returnValue='';}});
    function render(){
      $('faultAttentionSlots').innerHTML=slots.map((photo,i)=>`<div class="fault-attention-slot"><b>Foto ${i+1}</b>${photo?`<img src="${esc(photo.url)}" alt="Foto ${i+1}"><button type="button" class="secondary" data-attention-remove="${i}" ${busy||frozen?'disabled':''}>Quitar</button>`:`<label class="secondary photo-trigger">Tomar foto<input type="file" accept="image/*" capture="environment" data-attention-slot="${i}" ${busy||frozen?'disabled':''} hidden></label><label class="secondary photo-trigger">Subir foto<input type="file" accept="image/*" data-attention-slot="${i}" ${busy||frozen?'disabled':''} hidden></label>`}</div>`).join('');
      photos.querySelectorAll('[data-attention-slot]').forEach(input=>input.onchange=()=>prepare([input.files[0]],Number(input.dataset.attentionSlot)));
      photos.querySelectorAll('[data-attention-remove]').forEach(button=>button.onclick=()=>{const i=Number(button.dataset.attentionRemove);URL.revokeObjectURL(slots[i].url);slots[i]=null;render();});
      $('faultAttentionBulk').disabled=busy||Boolean(frozen);
      $('faultResolutionNotes').disabled=busy||Boolean(frozen);$('faultAttentionStatus').disabled=busy||Boolean(frozen);
      $('resolveFault').disabled=busy;
    }
    async function prepare(files,start){
      files=files.filter(Boolean);if(busy||frozen||!files.length)return;
      const free=slots.map((p,i)=>!p?i:-1).filter(i=>i>=0);
      if(files.length>free.length){$('faultAttentionMessage').textContent='Sólo hay seis espacios. Quita una foto para cambiarla.';return;}
      busy=true;render();$('faultAttentionMessage').textContent='Preparando fotografías…';
      try{for(let n=0;n<files.length;n++){const i=start===undefined?free[n]:start,file=await api.compress(files[n]);if(file.size>2*1024*1024)throw Error('La foto sigue siendo demasiado grande. Elige otra imagen.');slots[i]={file,url:URL.createObjectURL(file)};}$('faultAttentionMessage').textContent=`${slots.filter(Boolean).length} de 6 fotos preparadas. Aún no están guardadas.`;}
      catch(error){$('faultAttentionMessage').textContent=error.message;}
      finally{busy=false;$('faultAttentionBulk').value='';render();}
    }
    $('faultAttentionBulk').onchange=()=>prepare([...$('faultAttentionBulk').files]);
    async function load(id){
      history.innerHTML='<p>Consultando historial de atención…</p>';
      try{const response=await api.fetch(`/api/operaciones/faults/${encodeURIComponent(id)}/attentions`),data=await response.json();if(!response.ok||!data.ok)throw Error(data.error||'No se pudo consultar el historial.');if(id!==faultId)return;
        history.innerHTML='<h3>Historial de atención</h3>'+(data.attentions.map(row=>`<article class="scope"><b>${esc(row.status)} · ${esc(new Date(row.created_at).toLocaleString('es-MX'))}</b><small>${esc(row.actor_name)}</small><p style="white-space:pre-wrap">${esc(row.notes)}</p><div class="evidence-grid">${row.photos.map(photo=>`<figure class="evidence-thumb"><a href="${esc(photo.url)}" target="_blank" rel="noopener"><img src="${esc(photo.url)}" alt="Foto ${photo.position}" loading="lazy"></a><figcaption>Foto ${photo.position}</figcaption></figure>`).join('')}</div></article>`).join('')||'<p>Sin atenciones registradas.</p>');
      }catch(error){if(id===faultId)history.textContent=error.message;}
    }
    function open(fault){
      if(fault.id!==faultId){clear();faultId=fault.id;$('faultAttentionMessage').textContent='';}
      photos.hidden=!api.canWrite;
      if(api.canWrite){$('faultResolutionField').hidden=false;$('resolveFault').hidden=false;$('faultResolutionNotes').value=frozen?.notes||draftNotes;$('faultAttentionStatus').value=frozen?.status||draftStatus;$('resolveFault').textContent=frozen?'Reintentar guardado':'Guardar atención y fotos';render();}
      load(faultId);
    }
    if(api.canWrite)$('resolveFault').onclick=async()=>{
      if(busy||!faultId)return;
      if(!frozen){const notes=$('faultResolutionNotes').value.trim();if(!notes){$('faultAttentionMessage').textContent='Describe el trabajo realizado antes de guardar.';return;}
        ident=crypto.randomUUID();frozen={notes,status:$('faultAttentionStatus').value,slots:[...slots]};}
      const id=faultId;busy=true;render();$('resolveFault').textContent='Guardando atención y fotos…';$('faultAttentionMessage').textContent='Enviando. Espera la confirmación antes de cerrar.';
      try{const form=new FormData();form.append('id',ident);form.append('notes',frozen.notes);form.append('status',frozen.status);frozen.slots.forEach((photo,i)=>{if(photo)form.append(`photo_${i+1}`,photo.file,photo.file.name);});
        const response=await api.fetch(`/api/operaciones/faults/${encodeURIComponent(id)}/attentions`,{method:'POST',body:form},180000),data=await response.json();if(!response.ok||!data.ok)throw Error(data.error||'No se confirmó el guardado.');
        clear();$('faultResolutionNotes').value='';$('faultAttentionMessage').textContent='Atención y fotografías guardadas.';api.toast('Atención guardada sin generar PDF.');await load(id);api.refresh();
      }catch(error){$('faultAttentionMessage').textContent=error.message+' Conserva esta pantalla y pulsa Reintentar guardado; no se duplicará.';}
      finally{busy=false;$('resolveFault').textContent=frozen?'Reintentar guardado':'Guardar otra atención';render();}
    };
    const close=$('closeFaultDetail');close.onclick=()=>{if(busy){api.toast('Espera a que termine el envío.');return;}if(dirty()&&!confirm('Hay una captura sin confirmar. ¿Cerrar esta pantalla? No borra atenciones ya guardadas.'))return;clear();$('faultDetailModal').classList.remove('open');};
    return {open,canOpen:id=>{if(busy){api.toast('Espera a que termine el envío.');return false;}return id===faultId||!dirty()||confirm('¿Descartar la captura sin confirmar antes de abrir otra falla?');}};
  }
  root.HscFaultAttention={mount};
})(typeof window!=='undefined'?window:globalThis);
