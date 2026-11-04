(function(root){
  'use strict';
  const key=user=>'hsc-client-grid-order-v1-'+user;
  function read(user,storage){try{storage=storage||root.localStorage;const ids=JSON.parse(storage.getItem(key(user))||'[]');return Array.isArray(ids)?ids.filter(id=>typeof id==='string'):[];}catch(_){return [];}}
  function ordered(rows,user,storage){const rank=new Map(read(user,storage).map((id,index)=>[id,index]));return rows.slice().sort((a,b)=>(rank.get(a[1])??Infinity)-(rank.get(b[1])??Infinity));}
  function move(ids,from,to){const result=ids.slice(),i=result.indexOf(from),j=result.indexOf(to);if(i<0||j<0||i===j)return result;result.splice(i,1);result.splice(j,0,from);return result;}
  function save(user,ids,storage){try{storage=storage||root.localStorage;storage.setItem(key(user),JSON.stringify([...new Set(ids)]));return true;}catch(_){return false;}}
  function attach(container,api){
    let timer=null,source=null,target=null,startX=0,startY=0,dragging=false,suppressUntil=0;
    const doc=container.ownerDocument,win=doc.defaultView;
    function clean(){clearTimeout(timer);timer=null;source?.classList.remove('client-dragging');target?.classList.remove('client-drop-target');source=null;target=null;dragging=false;}
    function start(event,point){
      if(event.target.closest('.client-order-actions'))return;
      const card=event.target.closest('[data-client]');if(!card)return;
      clean();source=card;startX=point.clientX;startY=point.clientY;
      timer=setTimeout(()=>{
        if(!api.canStart()){suppressUntil=Date.now()+1000;api.toast('Limpia la búsqueda antes de ordenar');clean();return;}
        dragging=true;suppressUntil=Date.now()+1000;source.classList.add('client-dragging');api.onStart();
      },450);
    }
    function moving(event,point){
      if(!source)return;
      if(!dragging){if(Math.hypot(point.clientX-startX,point.clientY-startY)>10)clean();return;}
      if(event.cancelable)event.preventDefault();
      const over=doc.elementFromPoint(point.clientX,point.clientY)?.closest('.client-order-row')?.querySelector('[data-client]');
      if(over&&over!==source){target?.classList.remove('client-drop-target');target=over;target.classList.add('client-drop-target');}
      if(point.clientY<110)win.scrollBy(0,-14);else if(point.clientY>win.innerHeight-110)win.scrollBy(0,14);
    }
    function end(){const from=source?.dataset.client,to=target?.dataset.client,commit=dragging&&from&&to;if(dragging)suppressUntil=Date.now()+700;clean();if(commit)api.onMove(from,to);}
    container.addEventListener('touchstart',event=>{if(event.touches.length===1)start(event,event.touches[0]);else clean();},{passive:true});
    container.addEventListener('touchmove',event=>{if(event.touches.length===1)moving(event,event.touches[0]);else clean();},{passive:false});
    container.addEventListener('touchend',end,{passive:true});container.addEventListener('touchcancel',clean,{passive:true});
    container.addEventListener('pointerdown',event=>{if(event.pointerType!=='touch'&&event.button===0)start(event,event);});
    doc.addEventListener('pointermove',event=>{if(event.pointerType!=='touch')moving(event,event);});
    doc.addEventListener('pointerup',event=>{if(event.pointerType!=='touch')end();});
    container.addEventListener('click',event=>{if(dragging||Date.now()<suppressUntil){event.preventDefault();event.stopImmediatePropagation();}},true);
    container.addEventListener('contextmenu',event=>{if(event.target.closest('[data-client]'))event.preventDefault();});
    container.addEventListener('dragstart',event=>event.preventDefault());win.addEventListener('blur',clean);
  }
  const lib={read,ordered,move,save,attach};if(typeof module!=='undefined')module.exports=lib;else root.HscClientGrid=lib;
})(typeof window!=='undefined'?window:globalThis);
