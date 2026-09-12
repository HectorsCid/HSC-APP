/* Bandeja compartida del panel administrativo; no envía ni timbra documentos. */
(()=>{
  if (/^\/(acceso|app-operativa-demo|partner)/.test(location.pathname)) return;
  const style=document.createElement('style');
  style.textContent=`.hsc-notice-button{position:fixed;right:16px;bottom:18px;z-index:9990;border:1px solid #64748b;border-radius:24px;padding:11px 15px;background:#152238;color:#fff;font:600 14px system-ui;cursor:pointer}.hsc-notices{box-sizing:border-box;width:min(640px,calc(100vw - 24px));max-height:85dvh;overflow:auto;border:1px solid #475569;border-radius:18px;background:#111d30;color:#eef5ff;padding:22px;font:15px/1.5 system-ui}.hsc-notices::backdrop{background:#020617b8}.hsc-notices h2{font-size:23px;margin:0}.hsc-notices header{display:flex;justify-content:space-between;align-items:center;gap:12px}.hsc-notices button,.hsc-notices select,.hsc-notices a{background:#203452;color:#eef5ff;border:1px solid #526987;border-radius:9px;padding:8px 11px;cursor:pointer;font:inherit}.hsc-notices article{padding:14px 0;border-bottom:1px solid #334155}.hsc-notices p{margin:6px 0;overflow-wrap:anywhere}.hsc-notices small{color:#b6c6da}.hsc-notices .unread{border-left:3px solid #60a5fa;padding-left:12px}.hsc-notices nav{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}.hsc-notices details{margin:12px 0;padding:12px;background:#182940;border-radius:10px}.hsc-notices .notice-actions{display:flex;gap:8px;margin-top:9px}`;
  document.head.append(style);
  const button=document.createElement('button');button.className='hsc-notice-button';button.textContent='Avisos';button.setAttribute('aria-label','Abrir centro de avisos');
  const dialog=document.createElement('dialog');dialog.className='hsc-notices';dialog.setAttribute('aria-label','Centro de avisos HSC');
  dialog.innerHTML='<header><h2>Centro de avisos</h2><button type="button" aria-label="Cerrar avisos">×</button></header><p><small>Bandeja compartida de administración HSC.</small></p><nav><select aria-label="Filtrar avisos"><option value="">Todos</option><option value="unread">Sin leer</option><option value="facturas">Facturas</option><option value="pagos">Pagos y complementos</option><option value="respaldos">Respaldos</option><option value="reportes">Reportes</option></select><button type="button" data-refresh>Actualizar</button></nav><details><summary>Estado de procesos y próximas facturas</summary><div data-jobs></div></details><div data-items aria-live="polite"></div>';
  document.body.append(button,dialog);
  let data={items:[]};
  const el=(tag,text)=>{const node=document.createElement(tag);node.textContent=text;return node};
  const date=value=>value?new Date(value).toLocaleString('es-MX'):'Sin registro';
  const status=value=>({ok:'Correcto',error:'Requiere atención',complete:'Terminado',completed:'Terminado',awaiting_confirmation:'Por confirmar',pending:'Pendiente',cancelled:'Detenido',running:'En proceso'}[value]||value||'Pendiente');
  function render(){
    const filter=dialog.querySelector('select').value,list=dialog.querySelector('[data-items]');list.replaceChildren();
    const rows=data.items.filter(row=>!filter||(filter==='unread'?!row.read:row.category===filter));
    if(!rows.length)list.append(el('p','No hay avisos en esta selección.'));
    for(const row of rows){
      const article=el('article','');if(!row.read)article.className='unread';
      article.append(el('strong',row.title),el('p',row.body),el('small',date(row.created_at)));
      const actions=el('div','');actions.className='notice-actions';
      if(row.url?.startsWith('/')&&!row.url.startsWith('//')){const link=el('a','Abrir');link.href=row.url;actions.append(link);}
      if(!row.read){const read=el('button','Marcar leído');read.onclick=async()=>{read.disabled=true;try{const result=await fetch(`/api/notifications/${encodeURIComponent(row.id)}/read`,{method:'POST'});if(!result.ok)throw Error();row.read=true;data.unread=Math.max(0,data.unread-1);updateButton();render();}catch{read.textContent='No se guardó. Reintentar';read.disabled=false;}};actions.append(read);}
      article.append(actions);list.append(article);
    }
    const jobs=dialog.querySelector('[data-jobs]');jobs.replaceChildren();
    for(const [key,label] of [['facturas_programadas','Revisión de facturas'],['reportes_automaticos','Reportes automáticos']]){
      const job=data.jobs?.[key];jobs.append(el('p',`${label}: ${job?`${status(job.status)} · ${date(job.checked_at)}`:'sin comprobación registrada todavía'}`));
      if(job?.last_error)jobs.append(el('small',job.last_error));
      if(key==='facturas_programadas'&&job&&Date.now()-new Date(job.checked_at).getTime()>40*60*1000)jobs.append(el('p','La última comprobación tiene más de 40 minutos. Revisa el ejecutor de Render.'));
    }
    for(const row of data.schedules||[])jobs.append(el('p',`${row.name}: ${row.active?`próxima ${date(row.next_run)}`:'pausada'} · ${status(row.last_status)}${row.last_error?' · '+row.last_error:''}`));
    jobs.append(el('small','El ejecutor comprueba periódicamente; el aviso de cada plantilla corresponde a su ejecución mensual. No se timbra desde esta pantalla.'));
  }
  function updateButton(){button.textContent=`Avisos${data.unread?' · '+data.unread:''}`;}
  async function load(){try{const response=await fetch('/api/notifications');if(response.status===401||response.status===403){button.hidden=true;return;}const result=await response.json();if(!response.ok||!result.ok)throw Error(result.error||'No se pudieron cargar los avisos.');data=result;updateButton();render();}catch(error){dialog.querySelector('[data-items]').replaceChildren(el('p',error.message));}}
  button.onclick=()=>{dialog.showModal();load();};dialog.querySelector('header button').onclick=()=>dialog.close();dialog.querySelector('[data-refresh]').onclick=load;dialog.querySelector('select').onchange=render;
  // Sin solicitudes continuas en segundo plano ni avisos mensuales repetidos.
  load();
})();
