(function(root){
  'use strict';
  const normalize=value=>String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  function matches(text,query){return normalize(query).split(/\s+/).filter(Boolean).every(term=>normalize(text).includes(term));}
  function mount(doc,fetcher){
    const $=id=>doc.getElementById(id),library=$('reportLibrary');if(!library)return;
    const manual=$('manualReportsPanel'),equipment=$('reportEquipmentPanel'),search=$('reportLibrarySearch'),button=$('loadRecentReports'),status=$('recentReportsStatus'),dismiss=$('dismissRecentStatus');
    $('reportLibraryContent').append(equipment);
    library.before($('reportsAdvanced'));
    let tab='draft';
    function filter(){
      let count=0;
      for(const row of manual.querySelectorAll('[data-manual-status]')){const valid=(tab==='completed'?row.dataset.manualStatus==='completed':row.dataset.manualStatus!=='completed')&&matches(row.textContent,search.value);row.hidden=!valid;if(valid)count++;}
      for(const empty of manual.querySelectorAll('.manual-records>.empty'))empty.hidden=true;
      $('reportLibraryEmpty').hidden=count>0;
      for(const row of equipment.querySelectorAll('.item'))row.hidden=!matches(row.textContent,search.value);
    }
    function select(value){
      tab=value;manual.hidden=value==='equipment';equipment.hidden=value!=='equipment';
      for(const item of doc.querySelectorAll('[data-report-tab]')){const active=item.dataset.reportTab===value;item.setAttribute('aria-selected',String(active));item.tabIndex=active?0:-1;if(active&&value!=='equipment')manual.setAttribute('aria-labelledby',item.id);}
      filter();
    }
    const tabs=[...doc.querySelectorAll('[data-report-tab]')];
    tabs.forEach((item,index)=>{item.onclick=()=>select(item.dataset.reportTab);item.onkeydown=event=>{let target;if(event.key==='ArrowRight')target=(index+1)%tabs.length;if(event.key==='ArrowLeft')target=(index+tabs.length-1)%tabs.length;if(event.key==='Home')target=0;if(event.key==='End')target=tabs.length-1;if(target!==undefined){event.preventDefault();tabs[target].focus();select(tabs[target].dataset.reportTab);}};});
    search.oninput=filter;
    dismiss.onclick=()=>{status.textContent='';status.classList.remove('is-error');dismiss.hidden=true;};
    async function load(){
      if(button.disabled)return;
      button.disabled=true;button.textContent='Cargando…';button.setAttribute('aria-busy','true');status.textContent='Consultando los reportes recientes…';status.classList.remove('is-error');dismiss.hidden=true;
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),45000);
      try{
        const response=await fetcher(button.dataset.url,{signal:controller.signal,headers:{Accept:'text/html'},cache:'no-store'});
        if(!response.ok||response.redirected)throw Error('No se pudieron consultar los reportes. Comprueba tu sesión e inténtalo otra vez.');
        const incoming=new root.DOMParser().parseFromString(await response.text(),'text/html'),list=incoming.getElementById('recentReportsList');
        if(!list)throw Error('HSC no devolvió la lista de reportes. Inténtalo nuevamente.');
        const warning=[...incoming.querySelectorAll('.flash')].map(el=>el.textContent.trim()).filter(text=>/google|sheets|cach|configura/i.test(normalize(text))).join(' ');
        if(warning&&!list.querySelector('.item'))throw Error('Google no confirmó la consulta. Puedes cerrar este aviso y volver a intentarlo.');
        const target=$('recentReportsList');target.replaceChildren(...[...list.childNodes].map(node=>doc.importNode(node,true)));
        button.dataset.loaded='true';filter();status.textContent=warning||'Reportes recientes actualizados.';dismiss.hidden=false;
      }catch(error){status.textContent=error.name==='AbortError'?'La consulta tardó demasiado. Puedes cerrar este aviso y volver a intentarlo.':error.name==='TypeError'?'No se pudo contactar HSC. Revisa tu conexión y vuelve a intentarlo.':error.message;status.classList.add('is-error');dismiss.hidden=false;
      }finally{clearTimeout(timer);button.disabled=false;button.setAttribute('aria-busy','false');button.textContent=button.dataset.loaded==='true'?'Actualizar recientes':'Cargar recientes';}
    }
    button.onclick=load;select(button.dataset.loaded==='true'?'equipment':'draft');
    return {select,filter,load};
  }
  if(typeof module!=='undefined')module.exports={matches,mount};
  if(root.document)mount(root.document,root.fetch.bind(root));
})(typeof window!=='undefined'?window:globalThis);
