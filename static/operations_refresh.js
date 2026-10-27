/* HSC release 20261027-scoped-billing-search.
 * Keep only one comparison string; never persist images or request credentials. */
(function(root){
  'use strict';
  const keys=['clients','equipment','reports','faults','tasks','expenses','worklists','observation_options','stats','scope_valid','assigned_client_id'];
  function signature(payload,local){return JSON.stringify([keys.map(key=>payload[key]??null),local]);}
  function createGate(){
    let last=null;
    return {changed:next=>next!==last,commit:next=>{last=next;},reset:()=>{last=null;}};
  }
  function createPhotoHistory(limit=120){
    const urls=new Set();
    return {has:url=>urls.has(url),add:url=>{urls.delete(url);urls.add(url);while(urls.size>limit)urls.delete(urls.values().next().value);},clear:()=>urls.clear()};
  }
  const api={signature,createGate,createPhotoHistory};
  if(typeof module!=='undefined')module.exports=api;else root.HscRefresh=api;
})(typeof window!=='undefined'?window:globalThis);
