(()=>{
  const storageKey = 'hsc-theme';
  const preferred = localStorage.getItem(storageKey)
    || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  document.documentElement.dataset.theme = preferred;

  function mount(){
    if(document.querySelector('[data-hsc-global-theme]')) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'hsc-global-theme';
    button.dataset.hscGlobalTheme = '';
    button.setAttribute('role', 'switch');
    button.innerHTML = '<span class="hsc-global-theme-track"><span class="hsc-global-theme-knob"></span></span><span class="hsc-global-theme-label"></span>';
    const sync = ()=>{
      const dark = document.documentElement.dataset.theme === 'dark';
      button.setAttribute('aria-checked', String(dark));
      button.setAttribute('aria-label', dark ? 'Cambiar a modo claro' : 'Cambiar a modo oscuro');
      button.querySelector('.hsc-global-theme-label').textContent = dark ? 'Oscuro' : 'Claro';
    };
    button.addEventListener('click', ()=>{
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      localStorage.setItem(storageKey, next);
      sync();
    });
    sync();
    document.body.appendChild(button);
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
