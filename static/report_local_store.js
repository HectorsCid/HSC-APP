/* Each editor owns its record. Field proposals retain concurrent alternatives. */
(function(root){
  'use strict';
  const value=x=>x==null?'':String(x),uid=()=>globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(16).slice(2)}`;
  function legacyId(row){let hash=14695981039346656037n;for(const char of JSON.stringify([row.key,row.id,row.editReportId,row.data,row.photoMutationIds,row.photoCount]))hash=BigInt.asUintN(64,(hash^BigInt(char.codePointAt(0)))*1099511628211n);return 'legacy-'+hash.toString(16)}
  function create(storage,accountId,editorId,notify=()=>{}){
    const prefix=`hsc-report-journal-v1:${encodeURIComponent(accountId)}:`,legacy=`hsc-report-drafts-v2-${accountId}`;
    const recordKey=(key,editor)=>`${prefix}record:${encodeURIComponent(key)}:${encodeURIComponent(editor)}`;
    const parse=key=>{try{return JSON.parse(storage.getItem(key)||'null')}catch(_){return null}};
    const entries=kind=>{const out=[];for(let i=0;i<storage.length;i++){const key=storage.key(i);if(key?.startsWith(prefix+kind+':')){const row=parse(key);if(row?.accountId===accountId)out.push([key,row])}}return out};
    function migrate(){
      const rows=parse(legacy)||{};
      for(const [key,row] of Object.entries(rows)){
        const source=JSON.stringify(row),marker=prefix+'legacy:'+encodeURIComponent(key);
        if(storage.getItem(marker)===source)continue;
        const proposals={};for(const [field,item] of Object.entries(row.data||{}))if(value(item)!==value(row.baseValues?.[field]))proposals[field]={id:uid(),value:item,base:row.baseValues?.[field],seen:[]};
        storage.setItem(recordKey(key,'legacy'),JSON.stringify({...row,key,accountId,editorId:'legacy',version:uid(),time:Date.now(),proposals}));
        storage.setItem(marker,source);
      }
    }
    function drafts(){
      migrate();const groups={},out={};
      for(const [,row] of entries('record'))(groups[row.key]||=[]).push(row);
      const photoStates=entries('photo').map(([,row])=>row);
      for(const [key,rows] of Object.entries(groups)){
        rows.sort((a,b)=>Number(a.revision??-1)-Number(b.revision??-1)||a.time-b.time||a.editorId.localeCompare(b.editorId));
        const latest=rows.at(-1),own=rows.find(row=>row.editorId===editorId),base={...(latest.serverPayload||latest.baseValues||{})},data={...base},conflicts=[],proposalIds={};
        const fields=new Set(rows.flatMap(row=>Object.keys(row.data||{})));
        for(const field of fields){
          if(!(field in data))data[field]=latest.data?.[field];
          const proposals=rows.flatMap(row=>row.proposals?.[field]?[{...row.proposals[field],editorId:row.editorId}]:[]);
          const superseded=new Set(proposals.flatMap(p=>p.seen||[]));
          const candidates=proposals.filter(p=>!superseded.has(p.id));
          proposalIds[field]=candidates.map(p=>p.id);
          const dirty=candidates.filter(p=>value(p.value)!==value(base[field]));
          if(!dirty.length)continue;
          const selected=dirty.find(p=>p.editorId===editorId)||dirty.at(-1);
          data[field]=selected.value;
          base[field]=selected.base;
          const alternatives=[...new Set(dirty.map(p=>value(p.value)))];
          if(alternatives.length>1)conflicts.push({field,local:selected.value,remote:dirty.find(p=>value(p.value)!==value(selected.value)).value,source:'window'});
        }
        const pending=new Set(rows.flatMap(row=>row.pendingPhotoIds||[]));
        for(const photo of photoStates.filter(p=>p.key===key))photo.uploaded||photo.deleted?pending.delete(photo.mutationId):pending.add(photo.mutationId);
        out[key]={...latest,key,accountId,data,baseValues:base,localConflicts:conflicts,
          conflicts:[...(own?.conflicts||latest.conflicts||[]).filter(c=>c.source!=='window'),...conflicts],
          pendingPhotoIds:[...pending],localVersions:Object.fromEntries(rows.map(row=>[row.editorId,row.version])),proposalIds};
      }
      return out;
    }
    function save(context,data,forceFields=[]){
      if(context.accountId&&context.accountId!==accountId)throw Error('Este borrador pertenece a otra cuenta.');
      const key=context.key,old=parse(recordKey(key,editorId)),proposals={...(old?.proposals||{})},before=old?.data||context.localBaseline||{};
      for(const [field,item] of Object.entries(data)){
        if(field.startsWith('_'))continue;
        if(value(item)!==value(before[field])||forceFields.includes(field)){
          const prior=proposals[field];
          proposals[field]={id:uid(),value:item,base:context.localBaseline?.[field],seen:[...new Set([...(context.proposalIds?.[field]||[]),...(prior?.seen||[]),...(prior?[prior.id]:[])])]};
        }
        if(!forceFields.includes(field)&&value(item)===value(context.localBaseline?.[field]))delete proposals[field];
      }
      const row={key,accountId,editorId,version:uid(),time:Date.now(),client:context.client,equipment:context.equipment,round:context.round,
        draftId:context.draftId||'',editReportId:context.editReportId||'',revision:context.revision??-1,
        serverPayload:Object.keys(context.serverPayload||{}).length?context.serverPayload:undefined,
        baseValues:context.localBaseline||context.baseValues||{},data:{...data},proposals,
        conflicts:context.conflicts||[],completedRemotely:Boolean(context.completedRemotely),pendingPhotoIds:context.pendingPhotoIds||old?.pendingPhotoIds||[]};
      storage.setItem(recordKey(key,editorId),JSON.stringify(row));notify(key);
      return drafts()[key];
    }
    function forget(key,versions){
      for(const [storageKey,row] of entries('record'))if(row.key===key&&(versions?versions[row.editorId]===row.version:row.editorId===editorId))storage.removeItem(storageKey);
      notify(key);
    }
    function resolve(key,field,chosen){
      const row=drafts()[key];if(!row)return null;
      return save({...row,localBaseline:row.baseValues},{...row.data,[field]:chosen},[field]);
    }
    function photo(key,mutationId,uploaded,deleted=false){
      storage.setItem(`${prefix}photo:${encodeURIComponent(key)}:${encodeURIComponent(mutationId)}`,JSON.stringify({accountId,key,mutationId,uploaded,deleted}));notify(key);
    }
    function rememberPending(upload){
      if(upload.accountId&&upload.accountId!==accountId)throw Error('El envío pertenece a otra cuenta.');
      const row={...upload,accountId,editorId:upload.editorId||editorId};
      if(!row.submissionId)throw Error('Falta la identidad del envío.');
      if(storage.getItem(prefix+'retired:'+encodeURIComponent(row.submissionId)))return false;
      storage.setItem(prefix+'pending:'+encodeURIComponent(row.submissionId),JSON.stringify(row));notify(row.key);
      return true;
    }
    function pending(){
      const out={};for(const [,row] of entries('pending'))if(!storage.getItem(prefix+'retired:'+encodeURIComponent(row.submissionId)))out[out[row.key]?row.key+':'+row.submissionId:row.key]=row;return out;
    }
    function forgetPending(key,submissionId){
      if(!submissionId)return;
      storage.setItem(prefix+'retired:'+encodeURIComponent(submissionId),'1');
      for(const [storageKey,row] of entries('pending'))if(row.key===key&&row.submissionId===submissionId)storage.removeItem(storageKey);
      notify(key);
    }
    return {prefix,drafts,save,forget,resolve,photo,pending,rememberPending,forgetPending};
  }
  const api={create,legacyId};if(typeof module!=='undefined'&&module.exports)module.exports=api;root.HscReportLocal=api;
})(typeof globalThis!=='undefined'?globalThis:this);
