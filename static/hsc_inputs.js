(()=>{
  const NON_WRITING_FIELD = /(?:^|[_-])(rfc|tax|email|correo|folio|uuid|id|cp|postal|codigo|clave|serie|telefono|celular|cantidad|precio|unidad|orden|oc)(?:$|[_-])/i;

  function fieldIdentity(field){
    return [field.id, field.name, field.type, field.placeholder]
      .filter(Boolean)
      .join('_')
      .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/gi, '_')
      .toLowerCase();
  }

  function isRfcField(field){
    const identity = [field.id, field.name]
      .filter(Boolean)
      .join('_')
      .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
      .replace(/[^a-z0-9]+/gi, '_')
      .toLowerCase();
    return /(?:^|_)rfc(?:$|_)/.test(identity)
      || /registro_?federal_?de_?contribuyentes/.test(identity);
  }

  function normalizeRfc(field, trim=false){
    const original = field.value || '';
    const normalized = (trim ? original.trim() : original).toUpperCase();
    if(original === normalized) return;
    const start = field.selectionStart;
    const end = field.selectionEnd;
    field.value = normalized;
    if(document.activeElement === field && start !== null && end !== null){
      field.setSelectionRange(start, end);
    }
  }

  function capitalizeSentenceStarts(field){
    if(field.dataset.hscSpellcheck !== 'active') return;
    const original = field.value || '';
    const normalized = original.replace(
      /(^|[.!?]\s+|\n\s*)([a-záéíóúüñ])/giu,
      (_, prefix, letter) => prefix + letter.toLocaleUpperCase('es-MX')
    );
    if(original === normalized) return;
    const start = field.selectionStart;
    const end = field.selectionEnd;
    field.value = normalized;
    if(document.activeElement === field && start !== null && end !== null){
      field.setSelectionRange(start, end);
    }
  }

  function configureField(field){
    if(!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement)) return;

    if(isRfcField(field)){
      field.spellcheck = false;
      field.autocapitalize = 'characters';
      field.style.textTransform = 'uppercase';
      normalizeRfc(field);
      return;
    }

    const explicitSpellcheck = (field.getAttribute('spellcheck') || '').toLowerCase();
    if(explicitSpellcheck === 'false') return;
    const type = (field.type || '').toLowerCase();
    if(field instanceof HTMLInputElement && type !== 'text') return;
    if(NON_WRITING_FIELD.test(fieldIdentity(field))){
      field.spellcheck = false;
      return;
    }
    field.lang = 'es-MX';
    field.setAttribute('lang', 'es-MX');
    field.spellcheck = true;
    field.setAttribute('spellcheck', 'true');
    field.setAttribute('autocorrect', 'on');
    field.autocapitalize = field instanceof HTMLTextAreaElement ? 'sentences' : 'words';
    field.setAttribute(
      'autocapitalize',
      field instanceof HTMLTextAreaElement ? 'sentences' : 'words'
    );
    field.dataset.hscSpellcheck = 'active';
  }

  function configureWithin(root){
    if(root.matches?.('input, textarea')) configureField(root);
    root.querySelectorAll?.('input, textarea').forEach(configureField);
  }

  function mount(){
    document.documentElement.lang = 'es-MX';
    configureWithin(document);
    document.addEventListener('input', event=>{
      if(isRfcField(event.target)) normalizeRfc(event.target);
      else if(event.target?.dataset?.hscSpellcheck === 'active') capitalizeSentenceStarts(event.target);
    });
    document.addEventListener('blur', event=>{
      if(isRfcField(event.target)) normalizeRfc(event.target, true);
    }, true);
    new MutationObserver(records=>records.forEach(record=>{
      record.addedNodes.forEach(node=>{
        if(node.nodeType === Node.ELEMENT_NODE) configureWithin(node);
      });
    })).observe(document.body, {childList:true, subtree:true});
  }

  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
