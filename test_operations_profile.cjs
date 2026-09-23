const fs=require('node:fs'),assert=require('node:assert/strict'),vm=require('node:vm');
const page=fs.readFileSync('templates/app_operativa_demo.html','utf8');
const profile=fs.readFileSync('templates/_operations_profile.html','utf8');
const css=fs.readFileSync('static/operations_profile.css','utf8');
assert(page.includes("{% include '_operations_profile.html' %}"));
for(const obsolete of ['profileTasks','profileReports','profileClients','profileExpenseShortcut']){
  assert(!page.includes(obsolete)&&!profile.includes(obsolete),`${obsolete} removed, including JS references`);
}
assert(!profile.includes('Resumen'));
for(const id of ['openExpenses','openUsersProfile','profileAvatar','profileName','profileRole',
  'editOwnProfile','profileLogout','profileThemeToggle','offlineSyncState','offlineSyncDescription',
  'offlineSyncMode','syncPendingNow','syncGoogleProfile','profileSyncState','openSyncDiagnostics','openProfileNotices']){
  assert.equal((profile.match(new RegExp(`id="${id}"`,'g'))||[]).length,1,`${id} exactly once`);
}
assert(profile.indexOf('id="openExpenses"')<profile.indexOf('id="profileSettingsHeading"'));
assert.match(profile,/<details[^>]*id="profileAppearance">/);
assert.match(profile,/<details[^>]*id="profileConnection">/);
assert(!/<details[^>]*\bopen(?:\s|>)/.test(profile));
assert.match(profile,/<summary[^>]*>[\s\S]*?id="offlineSyncState"[\s\S]*?<\/summary>/);
assert.match(profile,/<div class="profile-admin-tools" data-admin-only>/);
assert.match(profile,/id="openUsersProfile"[^>]*data-admin-only/);
assert.match(page,/const profileLogout=\$\('#profileLogout'\)/);
assert.match(page,/logoutButton.onclick=profileLogout.onclick=logoutOperations/);
assert.match(page,/backgroundReportUploads.size/);
assert.match(page,/Tienes acciones pendientes de enviar/);
assert.match(page,/\$\('#openProfileNotices'\).onclick=.*bell.click\(\)/);
assert.match(css,/focus-visible/);
assert.match(css,/prefers-reduced-motion/);
assert(fs.readFileSync('static/service-worker.js','utf8').includes("'/static/operations_profile.css'"),'Profile styles available offline');

const elements=new Map(),el=id=>{
  if(!elements.has(id))elements.set(id,{dataset:{},textContent:'',hidden:false,classList:{toggle(){}}});
  return elements.get(id);
};
const admin=el('admin'),permissions=el('permissions');permissions.dataset.permission='editIds';
const ctx=vm.createContext({document:{body:{dataset:{}}},$:el,$$:s=>s==='[data-admin-only]'?[admin]:s==='[data-permission]'?[permissions]:[],
  account:{isOwner:true},currentMode:'tech',serverRole:'admin',can:()=>false,renderProfileIdentity(){},
  visibleExpenses:()=>ctx.expenses,expenses:[],updateOfflineSyncUi(){},});
vm.runInContext(page.slice(page.indexOf('  function applyAccess(){'),page.indexOf('  async function syncGoogleNow(')),ctx);
ctx.applyAccess();
assert.equal(admin.dataset.roleHidden,'false');
assert.equal(el('#profileControlHeading').textContent,'Administración');
assert(el('#profileExpenseState').hidden,'No decorative zero counters');
ctx.expenses=[{status:'Pendiente',amount:100},{status:'Aprobado',amount:300}];ctx.applyAccess();
assert.equal(el('#profileExpenseState').textContent,'1 por revisar');
assert(!el('#profileExpenseState').hidden);
assert(!el('#pendingExpenseNavBadge').hidden);
const paymentsLine=page.split(/\r?\n/).find(line=>line.includes('const payOriginalAccess='));
vm.runInContext(paymentsLine,ctx);ctx.applyAccess();
assert.equal(el('#profileExpenseTitle').textContent,'Pagos a técnicos');
assert.equal(el('#profileExpenseState').textContent,'1 por revisar','Owner payment shortcut retains pending indicator');
// Evaluate base access again as a technician (payment wrapper is owner-only).
vm.runInContext(page.slice(page.indexOf('  function applyAccess(){'),page.indexOf('  async function syncGoogleNow(')),ctx);
ctx.account.isOwner=false;ctx.serverRole='technician';ctx.applyAccess();
assert.equal(admin.dataset.roleHidden,'true');
assert.equal(el('#profileExpenseTitle').textContent,'Mis gastos');
assert.equal(el('#profileControlHeading').textContent,'Mi trabajo');
assert(el('#pendingExpenseNavBadge').hidden);
assert.equal(permissions.dataset.roleHidden,'true');
console.log('OK: Profile hierarchy, no redundant summary, pending visibility, owner/technician access, notifications, and guarded sign-out.');
