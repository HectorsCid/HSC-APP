(function(root){
  // Per-page/account only; no persistent cache of private photos.
  function create({fetch:request=root.fetch.bind(root),urlApi=root.URL,maxBytes=8*1024*1024,maxEntries=120}={}){
    const entries=new Map(),pending=new Map();let bytes=0;
    function trim(){for(const [key,row] of entries){if(bytes<=maxBytes&&entries.size<=maxEntries)break;entries.delete(key);bytes-=row.size;urlApi.revokeObjectURL(row.url);}}
    async function get(key){
      if(entries.has(key)){const row=entries.get(key);entries.delete(key);entries.set(key,row);return row.url;}
      if(pending.has(key))return pending.get(key);
      const promise=(async()=>{const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);try{const response=await request(key,{cache:'force-cache',credentials:'same-origin',signal:controller.signal});if(!response.ok||response.redirected||!String(response.headers.get('Content-Type')||'').startsWith('image/'))throw Error('Miniatura no disponible');const blob=await response.blob();if(!blob.size||blob.size>maxBytes)throw Error('Miniatura demasiado grande');const url=urlApi.createObjectURL(blob);entries.set(key,{url,size:blob.size});bytes+=blob.size;trim();return url;}finally{clearTimeout(timer);}})();
      pending.set(key,promise);try{return await promise;}finally{pending.delete(key);}
    }
    return {get};
  }
  const api={create};if(typeof module!=='undefined')module.exports=api;else root.HscPhotoMemory=api;
})(typeof window!=='undefined'?window:globalThis);
