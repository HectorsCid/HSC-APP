(function(root){
  'use strict';
  function fullyVisible(rect,top,bottom){return rect.top>=top-1&&rect.bottom<=bottom+1;}
  function attach(list,doc,win){
    let lastY=0,pageGesture=false,active=false;
    list.addEventListener('touchstart',event=>{
      active=event.touches.length===1;if(!active)return;
      lastY=event.touches[0].clientY;
      const header=doc.querySelector('.topbar'),nav=doc.querySelector('.bottom-nav');
      const top=header?Math.max(0,header.getBoundingClientRect().bottom)+8:8;
      const bottom=nav?Math.min(win.innerHeight,nav.getBoundingClientRect().top)-8:win.innerHeight-8;
      pageGesture=!fullyVisible(list.getBoundingClientRect(),top,bottom);
    },{passive:true});
    list.addEventListener('touchmove',event=>{
      if(!active||event.touches.length!==1){active=false;return;}
      const y=event.touches[0].clientY,delta=lastY-y;lastY=y;
      // Decide once per gesture: never switch midway and surprise the user.
      if(pageGesture&&event.cancelable){event.preventDefault();win.scrollBy(0,delta);}
    },{passive:false});
    for(const name of ['touchend','touchcancel'])list.addEventListener(name,()=>{active=false;},{passive:true});
  }
  if(typeof module!=='undefined')module.exports={fullyVisible,attach};
  if(root.document){
    const style=root.document.createElement('style');
    style.textContent='@media(pointer:coarse){#equipmentList,#roundReportList{max-height:max(120px,min(390px,calc(100dvh - 260px)));overscroll-behavior-y:auto;margin-left:4px;margin-right:10px}}';
    root.document.head.append(style);
    for(const id of ['equipmentList','roundReportList']){const list=root.document.getElementById(id);if(list)attach(list,root.document,root);}
  }
})(typeof window!=='undefined'?window:globalThis);
