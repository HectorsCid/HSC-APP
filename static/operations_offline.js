(function(root){
  'use strict';
  function canSend(mode, connectionType, manual){
    if(manual)return true;
    if(mode==='offline')return false;
    return mode!=='wifi'||connectionType==='wifi';
  }
  function overlay(base, actions, pending=true){
    const out=JSON.parse(JSON.stringify(base));
    for(const name of ['clients','equipment','tasks','expenses','faults','worklists'])out[name]||=[];
    const upsert=(list,row)=>{const index=out[list].findIndex(item=>item.id===row.id);const value={...(index<0?{}:out[list][index]),...row,pending_upload:pending};if(index<0)out[list].push(value);else out[list][index]=value;};
    const worklistErrors=new Map(actions.filter(a=>a.url==='/api/operaciones/worklists'&&a.blocked).map(a=>[a.body.id,a.error]));
    for(const action of [...actions].sort((a,b)=>a.createdAt-b.createdAt)){
      const b=action.body||{},url=action.url;
      if(url==='/api/operaciones/equipment')for(const item of b.items||[])upsert('equipment',{...item,status:item.active===false?'Inactivo':'Activo',...(action.photoPreview?{photo_url:action.photoPreview,has_photo:true}:action.removePhoto?{photo_url:'',has_photo:false}:{})});
      else if(url==='/api/operaciones/clients')upsert('clients',{...b,...(action.photoPreview?{photo_url:action.photoPreview,has_photo:true}:action.removePhoto?{photo_url:'',has_photo:false}:{})});
      else if(url==='/api/operaciones/tasks')upsert('tasks',{status:'Pendiente',...b});
      else if(url==='/api/operaciones/expenses')upsert('expenses',{status:'Pendiente',...b});
      else if(url==='/api/operaciones/faults')upsert('faults',{status:'Reportada',...b});
      else if(url==='/api/operaciones/worklists')upsert('worklists',{...b,revision:Number(b.expected_revision||0)+1,sync_error:worklistErrors.get(b.id)||''});
      else {
        const m=url.match(/^\/api\/operaciones\/(tasks|expenses|faults|clients)\/([^/]+)\/(complete|status|resolve|round)$/);
        if(m)upsert(m[1],{id:decodeURIComponent(m[2]),...(m[3]==='complete'?{status:'Terminada'}:m[3]==='round'?{selected_round:b.round}:b)});
      }
    }
    return out;
  }
  const api={canSend,overlay};
  if(typeof module!=='undefined')module.exports=api;else root.HscOffline=api;
})(typeof window!=='undefined'?window:globalThis);
