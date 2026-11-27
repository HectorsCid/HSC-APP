(()=>{
  function mount(editor){
    const list=editor.querySelector('[data-branch-list]'),template=editor.querySelector('[data-branch-template]');
    const error=editor.querySelector('[data-branch-error]'),empty=editor.querySelector('[data-branch-empty]');
    const rows=()=>[...list.querySelectorAll('input[name="sucursal_nombre"]')];
    const config=editor.querySelector('[data-lot-config]'), initial=JSON.parse(config?.textContent||'{}');
    function setup(row){
      if(!config)return;
      const field=row.querySelector('[name=sucursal_nombre]'), saved=initial[field.value]||{};
      const box=document.createElement('details');box.style.gridColumn='1/-1';
      box.innerHTML='<summary style="cursor:pointer">Configurar lote</summary><p>Contactos de cotización</p><div data-people></div><p>Pólizas</p><div data-policies></div><button type="button" class="btn branch-add">Crear o vincular póliza</button><small>Para conservar su historial, elige el nombre de la póliza existente.</small>';
      row.append(box);
      const people=box.querySelector('[data-people]'), policies=box.querySelector('[data-policies]');
      let chosen=new Set(saved.contacts||[]);
      row.lotConfig=()=>({contacts:[...chosen],policies:[...policies.querySelectorAll('input')].map(x=>x.value.trim()).filter(Boolean)});
      box.ontoggle=()=>{
        if(!box.open)return;people.replaceChildren();
        for(const cr of document.querySelectorAll('#contactList .contact-row')){
          const n=cr.querySelector('[name=contacto_nombre]').value.trim();
          if(!n || !['compras','ambos'].includes(cr.querySelector('select').value))continue;
          const label=document.createElement('label'),check=document.createElement('input');
          check.type='checkbox';check.style.width='auto';check.checked=chosen.has(n);
          check.onchange=()=>check.checked?chosen.add(n):chosen.delete(n);
          label.append(check,' '+n);people.append(label);
        }
        if(!people.children.length)people.textContent='Agrega primero una persona de atención para cotizaciones.';
      };
      function addPolicy(n){const p=document.createElement('input');p.maxLength=100;p.placeholder='Nombre de la póliza';p.setAttribute('list','existingPolicies');p.value=n;policies.append(p);return p;}
      for(const n of saved.policies||[])addPolicy(n);
      box.querySelector('button').onclick=()=>addPolicy('').focus();
    }
    rows().forEach(f=>setup(f.closest('.branch-row')));
    const clear=()=>{error.hidden=true;rows().forEach(input=>input.setCustomValidity(''));empty.hidden=rows().length>0;};
    editor.querySelector('[data-add-branch]').addEventListener('click',()=>{
      const row=template.content.firstElementChild.cloneNode(true);list.append(row);setup(row);clear();row.querySelector('input').focus();
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
      if(config){
        let h=editor.querySelector('[name=branch_settings]');
        if(!h){h=document.createElement('input');h.type='hidden';h.name='branch_settings';editor.append(h);}
        h.value=JSON.stringify(Object.fromEntries(rows().filter(f=>f.value.trim()).map(f=>[f.value,f.closest('.branch-row').lotConfig()])));
      }
    });
    clear();
  }
  document.querySelectorAll('[data-branch-editor]').forEach(mount);
})();
