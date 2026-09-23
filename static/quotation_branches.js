(()=>{
  function mount(editor){
    const list=editor.querySelector('[data-branch-list]'),template=editor.querySelector('[data-branch-template]');
    const error=editor.querySelector('[data-branch-error]'),empty=editor.querySelector('[data-branch-empty]');
    const rows=()=>[...list.querySelectorAll('input[name="sucursal_nombre"]')];
    const clear=()=>{error.hidden=true;rows().forEach(input=>input.setCustomValidity(''));empty.hidden=rows().length>0;};
    editor.querySelector('[data-add-branch]').addEventListener('click',()=>{
      const row=template.content.firstElementChild.cloneNode(true);list.append(row);clear();row.querySelector('input').focus();
    });
    list.addEventListener('click',event=>{
      const button=event.target.closest('[data-remove-branch]');if(!button)return;
      button.closest('.branch-row').remove();clear();editor.querySelector('[data-add-branch]').focus();
    });
    list.addEventListener('input',clear);
    editor.closest('form').addEventListener('submit',event=>{
      clear();const seen=new Set();let count=0;
      for(const input of rows()){
        const name=input.value.normalize('NFC').trim().replace(/\s+/gu,' '),key=name.normalize('NFKC').toLocaleLowerCase('es-MX');
        if(!name)continue;
        let problem='';
        if(name.length>100||name==='.'||name==='..'||/[<>:"/\\|?*\x00-\x1f]/u.test(name)||name.endsWith('.'))problem='Usa un nombre de hasta 100 caracteres, sin símbolos como / o \\.';
        else if(seen.has(key))problem=`La sucursal «${name}» está repetida. Déjala una sola vez.`;
        else if(++count>100)problem='Puedes guardar hasta 100 sucursales por cliente.';
        if(problem){event.preventDefault();error.textContent=problem;error.hidden=false;input.setCustomValidity(problem);input.reportValidity();input.focus();return;}
        seen.add(key);input.value=name;
      }
    });
    clear();
  }
  document.querySelectorAll('[data-branch-editor]').forEach(mount);
})();
