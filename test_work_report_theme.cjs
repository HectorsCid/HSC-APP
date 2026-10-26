const assert=require('node:assert/strict'),fs=require('node:fs');
const html=fs.readFileSync('templates/reporte_diag_form.html','utf8');
const theme=fs.readFileSync('static/hsc_theme.css','utf8');
const options=html.match(/select option\{([^}]+)\}/)[1];
assert.ok(options.includes('background:var(--hsc-theme-field,#111827)'));
assert.ok(options.includes('color:var(--hsc-theme-text,var(--text))'));
function luminance(hex){const rgb=hex.replace('#','').match(/../g).map(n=>parseInt(n,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return .2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2];}
for(const [name,block] of [['claro',theme.match(/:root\{([^}]+)/)[1]],['oscuro',theme.match(/html\[data-theme="dark"\]\{([^}]+)/)[1]]]){
  const background=block.match(/--hsc-theme-field:(#[a-f0-9]{6})/)[1],color=block.match(/--hsc-theme-text:(#[a-f0-9]{6})/)[1];
  const values=[luminance(background),luminance(color)].sort((a,b)=>b-a);
  const contrast=(values[0]+.05)/(values[1]+.05);
  assert.ok(contrast>=7,`Contraste insuficiente en modo ${name}: ${contrast}`);
}
console.log('OK: desplegables del reporte de trabajo con contraste claro y oscuro, sin cambiar formularios ni PDF.');
