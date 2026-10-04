const $ = id => document.getElementById(id);
let csrf = '', demo = false, bots = [], editing = null, busy = false, toastTimer;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path, method='GET', body) {
  const response = await fetch('/api/'+path, {method, headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== 'login') { $('shell').hidden=true; $('login').hidden=false; $('detail').close(); $('add-dialog').close(); $('management-dialog').close(); }
    throw new Error(data.error || 'Request failed. Please try again.');
  }
  return data;
}
function toast(message) { clearTimeout(toastTimer); $('toast').textContent=message; $('toast').hidden=false; toastTimer=setTimeout(()=>$('toast').hidden=true,7000); }
async function init() {
  try {
    const state = await api('session'); csrf=state.csrf; demo=state.demo;
    $('demo-login').hidden=!demo; $('credentials').hidden=demo;
    $('login-button').textContent=demo?'Enter demo workspace →':'Sign in →';
    $('demo-banner').hidden=!demo;
    if (state.authenticated) await showDashboard(); else $('login').hidden=false;
  } catch(e) {toast(e.message);}
}
$('login-form').addEventListener('submit',async e=>{
  e.preventDefault(); $('login-button').disabled=true; $('login-error').textContent='';
  try { const data=await api('login','POST',{username:$('username').value,password:$('password').value}); csrf=data.csrf; $('password').value=''; await showDashboard(); }
  catch(e){$('login-error').textContent=e.message;} finally{$('login-button').disabled=false;}
});
async function showDashboard() { $('login').hidden=true; $('shell').hidden=false; await refresh(); if(typeof refreshHealth==='function')refreshHealth(); }
$('logout').onclick=async()=>{try{await api('logout','POST',{}); location.reload();}catch(e){toast(e.message);}};
$('refresh').onclick=()=>refresh().catch(e=>toast(e.message));

async function refresh() {
  $('refresh').disabled=true;
  try {
    const data=await api('bots'); bots=data.bots; render();
    if(typeof syncSelection==='function')syncSelection();
    $('updated').textContent='Updated '+new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
  } catch(e){$('updated').textContent='Refresh failed'; throw e;} finally{$('refresh').disabled=false;}
}
function render() {
  $('total').textContent=bots.length; $('bot-count').textContent=bots.length;
  $('running').textContent=bots.filter(b=>b.state==='active').length;
  $('stopped').textContent=bots.filter(b=>b.state==='inactive').length;
  $('attention').textContent=bots.filter(b=>!['active','inactive'].includes(b.state)).length;
  $('bots').innerHTML=bots.length?bots.map((b,i)=>`<article class="bot-card">
    <div class="bot-top"><input type="checkbox" class="bot-select" data-select="${esc(b.id)}" aria-label="Select ${esc(b.name)}" ${typeof selectedBots!=='undefined'&&selectedBots.has(b.id)?'checked':''}><div class="bot-icon">${['↗','✳','↓'][i%3]}</div><div><h3 class="bot-name">${esc(b.name)}</h3><div class="bot-description">${esc(b.description)}</div></div><span class="bot-status ${esc(b.state)}"><i class="dot ${b.state==='active'?'green':b.state==='inactive'?'gray':'orange'}"></i>${esc(b.state==='active'?'Running':b.state==='inactive'?'Stopped':b.state)}</span></div>
    <div class="bot-middle"><span class="service-name">▤ &nbsp; ${esc(b.service)}</span><span class="startup">Start on boot <button class="toggle ${b.enabled?'on':''}" role="switch" aria-checked="${b.enabled}" aria-label="Start ${esc(b.name)} on boot" data-bot="${esc(b.id)}" data-action="${b.enabled?'disable':'enable'}"></button></span></div>
    <div class="bot-bottom"><div class="tools"><button class="tool-button" data-bot="${esc(b.id)}" data-manage="1">Settings & maintenance</button><button class="tool-button" data-bot="${esc(b.id)}" data-file="env">≡ &nbsp; Environment</button><button class="tool-button" data-bot="${esc(b.id)}" data-file="service">▤ &nbsp; Service file</button><button class="tool-button" data-bot="${esc(b.id)}" data-file="logs">⌁ &nbsp; Logs</button><button class="tool-button" data-bot="${esc(b.id)}" data-action="pull">↓ &nbsp; Pull latest</button></div><div class="run-controls"><button class="secondary" data-bot="${esc(b.id)}" data-action="restart">↻ &nbsp; Restart</button><button class="${b.state==='active'?'secondary':'primary'}" data-bot="${esc(b.id)}" data-action="${b.state==='active'?'stop':'start'}">${b.state==='active'?'◼ &nbsp; Stop':'▷ &nbsp; Start'}</button></div></div>
  </article>`).join(''):'<div class="empty">No bots configured yet. Choose Add bot to create one from GitHub or connect an existing service.</div>';
}
function confirmAction(title,message) {
  $('confirm-title').textContent=title; $('confirm-text').textContent=message; $('confirm-dialog').showModal();
  return new Promise(resolve=>{
    const finish=value=>{ $('confirm-dialog').close(); resolve(value); };
    $('cancel-action').onclick=()=>finish(false); $('confirm-action').onclick=()=>finish(true);
    $('confirm-dialog').oncancel=e=>{e.preventDefault();finish(false);};
  });
}
$('bots').onclick=async event=>{
  const button=event.target.closest('button[data-bot]'); if(!button||busy)return;
  const bot=bots.find(b=>b.id===button.dataset.bot);
  if(button.dataset.file){await openDetail(bot,button.dataset.file);return;}
  if(button.dataset.manage){await openBotManagement(bot);return;}
  const action=button.dataset.action;
  if(['stop','restart','pull'].includes(action)) {
    const text=action==='pull'?'Pull the current branch from its configured upstream. Local changes block the update. The bot will keep running until you restart it.':`${action==='stop'?'Stop':'Restart'} this Telegram bot now? ${action==='stop'?'It will remain stopped until you start it or it starts at boot.':'It will be briefly unavailable.'}`;
    if(!await confirmAction(`${action==='pull'?'Update':action==='stop'?'Stop':'Restart'} ${bot.name}?`,text))return;
  }
  busy=true; button.disabled=true;
  try {const data=await api(`bots/${bot.id}/action`,'POST',{action,background:true});toast(data.message||'Action queued. Check Jobs for results.');await refresh();}
  catch(e){toast(e.message);}finally{busy=false;button.disabled=false;}
};
async function openDetail(bot,kind) {
  editing=null; $('dialog-error').textContent=''; $('detail-title').textContent=bot.name;
  $('detail-type').textContent=kind==='env'?'ENVIRONMENT VARIABLES':kind==='service'?'SYSTEMD SERVICE':'SERVICE LOGS';
  $('detail-path').textContent='Loading…'; $('editor').value=''; $('log-content').textContent='Loading…';
  $('preview-changes').hidden=kind==='logs';$('change-preview').hidden=true;$('change-preview').textContent='';
  $('live-log-tools').hidden=kind!=='logs'; $('logs-search').value=''; $('logs-priority').value='all'; $('logs-live').checked=true;
  $('editor').hidden=kind==='logs'; $('log-content').hidden=kind!=='logs'; $('save-file').hidden=kind==='logs'; $('refresh-logs').hidden=kind!=='logs';
  $('reveal-label').hidden=kind!=='env'; $('reveal').checked=false; $('editor').disabled=true; $('save-file').disabled=true;
  $('editor-label').textContent=kind==='logs'?'Last 200 matching entries · UTC':'File editor';
  $('save-note').textContent=kind==='logs'?'Logs may contain sensitive bot output.':'Saving does not restart the bot.';
  $('file-warning').textContent=kind==='env'?'Values are hidden until revealed. Edit the original file syntax; keep any quotes and comments your bot needs.':kind==='service'?'Service files control how the bot runs. Saving validates the unit and reloads systemd. Restart the bot separately to apply changes.':'Recent output from this bot’s systemd journal. Refresh to see new entries.';
  $('detail').showModal();
  try {
    const data=await api(`bots/${bot.id}/${kind==='logs'?'log-stream':'files/'+kind}`);
    if(!$('detail').open)return;
    editing={bot,kind,...data,saved:data.content};
    $('detail-path').textContent=kind==='logs'?bot.service:data.path;
    if(kind==='logs')$('log-content').textContent=data.content;
    else { $('editor').value=kind==='env'?mask(data.content):data.content; $('editor').disabled=kind==='env'; $('save-file').disabled=kind==='env'; }
  } catch(e){$('dialog-error').textContent=e.message;}
}
function mask(text){return text.split('\n').map(line=>line.trim()&&!line.trim().startsWith('#')?(line.includes('=')?line.slice(0,line.indexOf('=')+1)+'••••••••':'••••••••'):line).join('\n');}
$('reveal').onchange=()=>{
  if(!editing)return;
  if(!$('reveal').checked){editing.content=$('editor').value;$('change-preview').textContent='';$('change-preview').hidden=true;}
  $('editor').value=$('reveal').checked?editing.content:mask(editing.content);
  $('editor').disabled=!$('reveal').checked; $('save-file').disabled=!$('reveal').checked;
};
function closeDetail() {
  if(busy)return;
  if(editing&&editing.kind!=='logs'&&($('editor').disabled?editing.content:$('editor').value)!==editing.saved&&!window.confirm('Discard your unsaved changes?'))return;
  $('detail').close();editing=null;$('editor').value='';$('log-content').textContent='';$('change-preview').textContent='';
}
$('close-detail').onclick=closeDetail;
$('detail').oncancel=e=>{e.preventDefault();closeDetail();};
$('save-file').onclick=async()=>{
  if(!editing||busy)return; busy=true; $('save-file').disabled=true; $('dialog-error').textContent='';
  try {
    const data=await api(`bots/${editing.bot.id}/files/${editing.kind}`,'PUT',{content:$('editor').value,revision:editing.revision});
    editing.revision=data.revision; editing.content=$('editor').value; editing.saved=editing.content; $('save-note').textContent=data.message; toast(data.message);
  }catch(e){$('dialog-error').textContent=e.message;}finally{busy=false;$('save-file').disabled=false;}
};
$('preview-changes').onclick=()=>{
  if(!editing||editing.kind==='logs')return;
  if($('editor').disabled){$('dialog-error').textContent='Reveal environment values before previewing changes.';return;}
  $('change-preview').textContent='CURRENT FILE\n'+editing.saved+'\n\nPROPOSED FILE\n'+$('editor').value;
  $('change-preview').hidden=false;
};
$('refresh-logs').onclick=async()=>{
  if(!editing)return; $('refresh-logs').disabled=true;
  try{await updateLiveLogs();}catch(e){$('dialog-error').textContent=e.message;}finally{$('refresh-logs').disabled=false;}
};
let adding=false;
const addForm=$('add-form');
const field=name=>addForm.elements.namedItem(name);
function setAddMode(){
  const github=field('mode').value==='github';
  document.querySelectorAll('.github-field').forEach(el=>{el.hidden=!github;el.querySelectorAll('input,textarea').forEach(input=>input.disabled=!github);});
  field('env').readOnly=github;
  if(github&&field('repo').value)field('env').value=field('repo').value.replace(/\/$/,'')+'/.env';
  $('submit-add').textContent=github?'Create bot':'Add existing bot';
  $('repo-hint').textContent=github?"New directory in the user's home; its parent must already exist.":'Existing Git checkout on the Pi.';
  $('add-info').textContent=demo?'Demo only: no repositories, files or services will be created on your Pi.':github?'Create a new bot. It will stay stopped until you start it from the dashboard.':'Register the existing service and files. Their contents and running state will be preserved.';
}
$('add-bot').onclick=()=>{addForm.reset();$('add-error').textContent='';$('add-progress').textContent='';setAddMode();$('add-dialog').showModal();};
addForm.querySelectorAll('[name=mode]').forEach(el=>el.onchange=setAddMode);
field('id').oninput=()=>{
  const id=field('id').value;
  field('service').value=id+'.service';
  if(field('mode').value==='github'){
    field('repo').value='/home/'+field('user').value+'/'+id;
    field('env').value=field('repo').value+'/.env';
    field('command').value=field('repo').value+'/.venv/bin/python main.py';
  }
};
field('repo').oninput=()=>{if(field('mode').value==='github')field('env').value=field('repo').value.replace(/\/$/,'')+'/.env';};
function closeAdd(){if(!adding){$('add-dialog').close();addForm.reset();}}
$('close-add').onclick=closeAdd;
$('add-dialog').oncancel=e=>{e.preventDefault();closeAdd();};
addForm.onsubmit=async event=>{
  event.preventDefault();if(adding)return;
  const data=Object.fromEntries(new FormData(addForm));data.python_setup=field('python_setup').checked;
  adding=true; $('add-error').textContent='';
  $('add-progress').textContent=data.mode==='github'?'Setting up your bot… Cloning and installing dependencies may take several minutes. Keep this window open.':'Checking the service and registering your bot…';
  const controls=[...addForm.querySelectorAll('input,textarea,button')];const disabled=controls.map(el=>el.disabled);controls.forEach(el=>el.disabled=true);$('close-add').disabled=true;
  try{
    data.background=true;const result=await api('bots','POST',data);
    adding=false;closeAdd();toast(result.message||'Bot setup queued. Check Jobs for progress.');await refresh();
  }catch(e){$('add-error').textContent=e.message;}
  finally{adding=false;controls.forEach((el,i)=>el.disabled=disabled[i]);$('close-add').disabled=false;$('add-progress').textContent='';}
};
init();
