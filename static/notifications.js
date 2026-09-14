/* Bandeja compartida del panel administrativo; no envía ni timbra documentos. */
(()=>{
  if(window.hscNoticesLoaded)return;window.hscNoticesLoaded=true;
  let button=document.querySelector('[data-hsc-notices]');
  if(!button){const target=document.querySelector('.topbar,.st-actions');if(!target)return;button=document.createElement('button');button.type='button';button.className='icon-btn';button.dataset.hscNotices='';button.innerHTML='🔔 <span data-notice-count></span>';target.append(button);}
  const api='/api/operaciones/avisos';
  const style=document.createElement('style');
  style.textContent=`.hsc-notices{box-sizing:border-box;width:min(640px,calc(100vw - 24px));max-height:85dvh;overflow:auto;border:1px solid #475569;border-radius:18px;background:#111d30;color:#eef5ff;padding:22px;font:15px/1.5 system-ui}.hsc-notices::backdrop{background:#020617b8}.hsc-notices h2{font-size:23px;margin:0}.hsc-notices header{display:flex;justify-content:space-between;align-items:center;gap:12px}.hsc-notices button,.hsc-notices select,.hsc-notices a{background:#203452;color:#eef5ff;border:1px solid #526987;border-radius:9px;padding:8px 11px;cursor:pointer;font:inherit}.hsc-notices article{padding:14px 0;border-bottom:1px solid #334155}.hsc-notices p{margin:6px 0;overflow-wrap:anywhere}.hsc-notices small{color:#b6c6da}.hsc-notices .unread{border-left:3px solid #60a5fa;padding-left:12px}.hsc-notices nav{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}.hsc-notices details{margin:12px 0;padding:12px;background:#182940;border-radius:10px}.hsc-notices .notice-actions{display:flex;gap:8px;margin-top:9px}`;
  document.head.append(style);
  const modern=document.createElement('style');modern.textContent=`
  .hsc-notices{--notice-bg:#f6f8fc;--notice-card:#fff;--notice-text:#172b46;--notice-muted:#62748a;--notice-line:#dfe7f1;--notice-accent:#1264d6;background:var(--notice-bg);color:var(--notice-text);border:1px solid var(--notice-line);border-radius:26px;padding:24px;width:min(680px,calc(100vw - 24px));max-height:88dvh;box-shadow:0 28px 90px #061a3b45}
  html[data-theme="dark"] .hsc-notices{--notice-bg:#0e1929;--notice-card:#16253a;--notice-text:#edf4ff;--notice-muted:#a6b8cf;--notice-line:#293c55;--notice-accent:#82b7ff;color-scheme:dark}
  .hsc-notices::backdrop{background:#06132688;backdrop-filter:blur(5px)}
  .hsc-notices header h2{font-size:25px;letter-spacing:-.6px}.hsc-notices small{color:var(--notice-muted)}
  .hsc-notices button,.hsc-notices select,.hsc-notices a{background:var(--notice-card);color:var(--notice-text);border:1px solid var(--notice-line);border-radius:12px;min-height:40px;box-sizing:border-box;text-decoration:none}
  .hsc-notices button:hover,.hsc-notices a:hover{border-color:var(--notice-accent)}.hsc-notices button:focus-visible,.hsc-notices a:focus-visible{outline:2px solid var(--notice-accent);outline-offset:3px}
  .hsc-notices header button{font-size:23px;width:42px;padding:0}.hsc-notices nav{gap:8px;margin:20px 0 14px}.hsc-notices select{flex:1;min-width:140px}
  .hsc-notices article{display:grid;grid-template-columns:42px minmax(0,1fr);gap:12px;background:var(--notice-card);border:1px solid var(--notice-line);padding:16px;margin:10px 0;border-radius:18px;position:relative}
  .hsc-notices article.unread{border-left:3px solid var(--notice-accent);padding-left:14px}.hsc-notices article strong{font-size:16px;line-height:1.35;display:block}.hsc-notices article p{font-size:14px;color:var(--notice-muted);line-height:1.5}
  .hsc-notices .notice-icon{display:grid;place-items:center;width:40px;height:40px;border-radius:13px;background:#4285ed18;color:var(--notice-accent)}.hsc-notices .notice-icon svg{width:23px;height:23px}
  .hsc-notices article[data-category="fallas"] .notice-icon{background:#ff9c2420;color:#d77812}.hsc-notices article[data-category="pagos"] .notice-icon,.hsc-notices article[data-category="complementos"] .notice-icon{background:#22b78518;color:#159569}
  .hsc-notices .notice-actions{gap:8px;flex-wrap:wrap;margin-top:12px}.hsc-notices .notice-actions a{color:var(--notice-accent);font-weight:600}.hsc-notices .notice-actions button{font-size:13px;color:var(--notice-muted)}
  .hsc-notices details{background:transparent;border:1px solid var(--notice-line);border-radius:14px;padding:12px;color:var(--notice-muted);font-size:13px}.hsc-notices summary{cursor:pointer;font-weight:600}
  .hsc-notices .notice-empty{text-align:center;padding:40px 16px;border:1px dashed var(--notice-line);border-radius:18px;color:var(--notice-muted)}.hsc-notices .notice-device{font-size:13px;color:var(--notice-muted)}
  @media(max-width:480px){.hsc-notices{padding:18px;border-radius:22px}.hsc-notices article{padding:13px;gap:10px}.hsc-notices article.unread{padding-left:11px}.hsc-notices header h2{font-size:22px}}
  `;document.head.append(modern);
  const dialog=document.createElement('dialog');dialog.className='hsc-notices';dialog.setAttribute('aria-label','Centro de avisos HSC');
  dialog.innerHTML='<header><h2>Centro de avisos</h2><button type="button" aria-label="Cerrar avisos">×</button></header><p><small>Bandeja compartida de administración HSC.</small></p><nav><select aria-label="Filtrar avisos"><option value="">Todos</option><option value="unread">Sin leer</option><option value="gastos">Gastos de técnicos</option><option value="facturas">Facturas</option><option value="pagos">Pagos y complementos</option><option value="respaldos">Respaldos</option><option value="reportes">Reportes</option></select><button type="button" data-refresh>Actualizar</button></nav><details><summary>Estado de procesos y próximas facturas</summary><div data-jobs></div></details><div data-items aria-live="polite"></div>';
  document.body.append(dialog);
  dialog.querySelector('header+p small').textContent='Avisos de tu cuenta HSC.';
  const faultOption=document.createElement('option');faultOption.value='fallas';faultOption.textContent='Fallas y resoluciones';dialog.querySelector('select').append(faultOption);
  const pushStatus=document.createElement('p');pushStatus.className='notice-device';pushStatus.setAttribute('aria-live','polite');dialog.querySelector('nav').after(pushStatus);
  const activate=document.createElement('button');activate.type='button';activate.textContent='Activar y probar en este dispositivo';activate.onclick=()=>window.hscTestServerNotification?.();dialog.querySelector('nav').append(activate);
  let data={items:[]};
  dialog.querySelector('details summary').textContent='Configuración y estado de entrega';
  dialog.append(dialog.querySelector('details'));
  dialog.querySelector('details summary').after(activate);
  const symbols={fallas:'<path d="m12 3 10 18H2L12 3Z"/><path d="M12 9v5m0 3v1"/>',pagos:'<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M3 10h18m-13 5h4"/>',correo:'<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m3 6 9 7 9-7"/>',default:'<path d="M6 3h9l4 4v14H6V3Z"/><path d="M14 3v5h5M9 12h7m-7 4h5"/>'};
  const el=(tag,text)=>{const node=document.createElement(tag);node.textContent=text;return node};
  const date=value=>value?new Date(value).toLocaleString('es-MX'):'Sin registro';
  const status=value=>({ok:'Correcto',error:'Requiere atención',complete:'Terminado',completed:'Terminado',awaiting_confirmation:'Por confirmar',pending:'Pendiente',cancelled:'Detenido',running:'En proceso'}[value]||value||'Pendiente');
  function render(){
    const picker=dialog.querySelector('select'),oldFilter=picker.value;picker.replaceChildren();for(const [value,label] of Object.entries({'':'Todos','unread':'Sin leer',...(data.categories||{})})){const option=el('option',label);option.value=value;picker.append(option)}picker.value=[...picker.options].some(option=>option.value===oldFilter)?oldFilter:'';
    const filter=picker.value,list=dialog.querySelector('[data-items]');list.replaceChildren();
    dialog.querySelector('header+p small').textContent=data.unread?`${data.unread} aviso${data.unread===1?' nuevo':'s nuevos'} · ${data.role==='technician'?'Mi trabajo':data.role==='client'?'Mi empresa':'Administración HSC'}`:'Estás al día · Aquí encontrarás tus próximos avisos';
    const rows=data.items.filter(row=>!filter||(filter==='unread'?!row.read:row.category===filter));
    if(!rows.length){const empty=el('p','Todo en orden. No hay avisos en esta selección.');empty.className='notice-empty';list.append(empty)}
    for(const row of rows){
      const article=el('article','');if(!row.read)article.className='unread';
      article.dataset.category=row.category;const icon=el('span','');icon.className='notice-icon';icon.setAttribute('aria-hidden','true');icon.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${symbols[row.category]||symbols.default}</svg>`;const copy=el('div','');copy.append(el('strong',row.title),el('p',row.body),el('small',date(row.created_at)));article.append(icon,copy);
      const actions=el('div','');actions.className='notice-actions';
      if(row.url?.startsWith('/')&&!row.url.startsWith('//')){const link=el('a','Abrir');link.href=row.url;actions.append(link);}
      if(!row.read){const read=el('button','Marcar leído');read.onclick=async()=>{read.disabled=true;try{const result=await fetch(`${api}/${encodeURIComponent(row.id)}/read`,{method:'POST'});if(!result.ok)throw Error();row.read=true;data.unread=Math.max(0,data.unread-1);updateButton();render();}catch{read.textContent='No se guardó. Reintentar';read.disabled=false;}};actions.append(read);}
      copy.append(actions);list.append(article);
    }
    const jobs=dialog.querySelector('[data-jobs]');jobs.replaceChildren();
    pushStatus.textContent=!data.push?.configured?'Falta configurar el envío en el servidor.':`Dispositivos vinculados: ${data.push.devices}. Permiso aquí: ${typeof Notification==='undefined'?'no disponible':Notification.permission}.`;
    for(const delivery of data.push?.deliveries||[]){const labels={accepted:'Aceptado por el servicio push (no confirma que se haya visto)',pending:'Pendiente de reintento',failed:'No enviado',expired:'Suscripción vencida',cancelled:'Cancelado'};jobs.append(el('p',`${delivery.title}: ${labels[delivery.status]||delivery.status} · ${delivery.attempts} intento(s). ${delivery.error||''}`));}
    for(const [key,label] of (data.role==='admin'?[['facturas_programadas','Revisión de facturas'],['reportes_automaticos','Reportes automáticos']]:[])){
      const job=data.jobs?.[key];jobs.append(el('p',`${label}: ${job?`${status(job.status)} · ${date(job.checked_at)}`:'sin comprobación registrada todavía'}`));
      if(job?.last_error)jobs.append(el('small',job.last_error));
      if(key==='facturas_programadas'&&job&&Date.now()-new Date(job.checked_at).getTime()>40*60*1000)jobs.append(el('p','La última comprobación tiene más de 40 minutos. Revisa el ejecutor de Render.'));
    }
    for(const row of data.schedules||[])jobs.append(el('p',`${row.name}: ${row.active?`próxima ${date(row.next_run)}`:'pausada'} · ${status(row.last_status)}${row.last_error?' · '+row.last_error:''}`));
    if(data.role==='admin')jobs.append(el('small','Las facturas programadas se revisan periódicamente.'));
  }
  function updateButton(){
    const unread=Number(data.unread||0),count=button.querySelector('[data-notice-count]');
    button.classList.toggle('has-unread',unread>0);
    button.setAttribute('aria-label',unread?`Abrir centro de avisos, ${unread} sin leer`:'Abrir centro de avisos');
    if(count)count.textContent=unread>99?'99+':String(unread||'');
  }
  let loading=false;
  async function load(){if(loading)return;loading=true;try{const response=await fetch(api,{cache:'no-store'});if(response.status===401||response.status===403){button.hidden=true;return;}const result=await response.json();if(!response.ok||!result.ok)throw Error(result.error||'No se pudieron cargar los avisos.');data=result;updateButton();render();}catch(error){dialog.querySelector('[data-items]').replaceChildren(el('p',error.message));}finally{loading=false}}
  button.onclick=()=>{dialog.showModal();load();};dialog.querySelector('header button').onclick=()=>dialog.close();dialog.querySelector('[data-refresh]').onclick=load;dialog.querySelector('select').onchange=render;
  setInterval(()=>{if(!document.hidden&&navigator.onLine)load()},30000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)load()});
  window.addEventListener('online',load);
  window.addEventListener('hsc-push-status',event=>{pushStatus.textContent=event.detail;});
  load();
})();
