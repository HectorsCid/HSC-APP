(function(root){
  function read(body,response){
    const status=response.status;
    const failure=(message,code)=>({ok:false,error:message,code,http_status:status});
    if(status===401||response.redirected)return failure('La sesión venció. Inicia sesión con la misma cuenta; tus pendientes se conservan.','session_expired');
    let payload;
    try{payload=JSON.parse(body);}catch(_){
      return failure(status>=500?'El servidor está temporalmente no disponible. No se confirmó el envío; el respaldo local se conserva.':`HSC no devolvió una respuesta válida (HTTP ${status}). El respaldo local se conserva.`, 'invalid_server_response');
    }
    if(!payload||typeof payload!=='object'||Array.isArray(payload))return failure('Respuesta incompleta de HSC. No se confirmó el envío; el respaldo local se conserva.','invalid_server_response');
    return payload;
  }
  const api={read};if(typeof module!=='undefined')module.exports=api;else root.HscOperationsResponse=api;
})(typeof window!=='undefined'?window:globalThis);
