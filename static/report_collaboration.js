/* Pure reconciliation rules shared by the editor, offline queue and tests. */
(function(root){
  'use strict';
  const value = item => item == null ? '' : String(item);
  function changes(data, baseline={}){
    return Object.fromEntries(Object.entries(data).filter(([key,item]) => !key.startsWith('_') && value(item)!==value(baseline[key])));
  }
  function reconcile(local, baseline, remote, sent){
    const base={...remote}, values={...remote}, conflicts=[];
    const previous=sent||baseline||{};
    for(const [key,item] of Object.entries(local)){
      if(key.startsWith('_'))continue;
      if(value(item)===value(previous[key]) || value(item)===value(remote[key])){values[key]=remote[key]??'';base[key]=remote[key]??'';continue;}
      values[key]=item;
      base[key]=previous[key];
      if(value(remote[key])!==value(previous[key]))conflicts.push({field:key,local:item,remote:remote[key]});
    }
    return {values,base,conflicts};
  }
  function confirmed(upload, report){
    if(!upload.submissionId || report?.submission_id!==upload.submissionId)return false;
    if(report.client_id!==upload.client || report.equipment_id!==upload.equipment || String(report.round)!==String(upload.round))return false;
    const mutations=new Set((report.evidence||[]).map(item=>item.mutation_id));
    return (upload.photoMutationIds||(upload.photos||[]).map(photo=>photo.mutationId)).every(id=>mutations.has(id));
  }
  const api={changes,reconcile,confirmed};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  root.HscReportCollaboration=api;
})(typeof globalThis!=='undefined'?globalThis:this);
