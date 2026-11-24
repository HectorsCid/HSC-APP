const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
  constructor(){this.children=[];this.style={};this.classList={add(){},remove(){}};this.parts={};}
  append(...children){this.children.push(...children);}
  replaceChildren(){this.children=[];}
  querySelector(key){return this.parts[key]??=new Element();}
}
(async()=>{
  const body=new Element(),banner=new Element();let confirmResult=false,confirmation='',discarded=[],rejectDiscard=false;
  let rows=[{id:'old',createdAt:Date.parse('2026-11-11T20:45:20Z'),url:'/api/operaciones/faults',body:{id:'F1'},files:[new Blob(['photo'])]},
    {id:'new',createdAt:Date.parse('2026-11-20T20:45:20Z'),url:'/api/operaciones/worklists',body:{id:'L1'},files:[]}];
  const context=vm.createContext({document:{body,createElement:()=>new Element()},confirm:text=>{confirmation=text;return confirmResult;},navigator:{clipboard:{writeText:async()=>{}}}});
  vm.runInContext(fs.readFileSync('static/pending_actions_audit.js','utf8'),context);
  context.HscPendingAudit.mount({banner,read:async()=>structuredClone(rows),discard:async id=>{if(rejectDiscard)throw Error('Espera a que termine el envío actual.');discarded.push(id);rows=rows.filter(row=>row.id!==id);}});
  await banner.children[0].onclick();const modal=body.children[0],container=modal.querySelector('[data-actions]');
  assert.equal(container.children.length,2);
  await container.children[0].children.at(-1).onclick();assert.equal(discarded.length,0,'cancel preserves action');
  assert(confirmation.includes('1 foto(s)'));assert(confirmation.includes('definitivamente'));
  confirmResult=true;rejectDiscard=true;
  await container.children[0].children.at(-1).onclick();assert.equal(rows.length,2,'busy drain does not discard');
  assert(modal.querySelector('[data-summary]').textContent.includes('Espera'));
  rejectDiscard=false;
  await container.children[0].children.at(-1).onclick();
  assert.deepEqual(discarded,['old']);assert.equal(rows.length,1);assert.equal(rows[0].id,'new');
  assert.equal(container.children.length,1,'refresh after explicit single discard');
  console.log('OK: cancellation, photo warning, busy guard, explicit single discard, no automatic bulk deletion.');
})().catch(error=>{console.error(error);process.exitCode=1;});
