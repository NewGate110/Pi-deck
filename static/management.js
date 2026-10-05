/* Operational UI. All remote values are escaped before rendering. */
const selectedBots=new Set();
let gitReview=null;
let managementPanel=null, managementBot=null, managementRevision=null, panelLoading=false, telemetryBusy=false, logsBusy=false;
const dateText=t=>t?new Date(t*1000).toLocaleString():'—';
const input=(name,label,value='',type='text')=>`<label>${esc(label)}<input name="${esc(name)}" type="${type}" value="${esc(value)}" ${type==='password'?'autocomplete="new-password"':''}></label>`;
function syncSelection(){
  for(const id of selectedBots)if(!bots.some(b=>b.id===id))selectedBots.delete(id);
  $('selected-count').textContent=selectedBots.size+' selected';$('run-bulk').disabled=!selectedBots.size;
  $('select-all').checked=bots.length>0&&selectedBots.size===bots.length;
  $('select-all').indeterminate=selectedBots.size>0&&selectedBots.size<bots.length;
}
$('bots').addEventListener('change',e=>{const id=e.target.dataset.select;if(!id)return;e.target.checked?selectedBots.add(id):selectedBots.delete(id);syncSelection();});
$('select-all').onchange=()=>{selectedBots.clear();if($('select-all').checked)bots.forEach(b=>selectedBots.add(b.id));render();syncSelection();};
$('run-bulk').onclick=async()=>{
  const ids=[...selectedBots],action=$('bulk-action').value;
  if(!await confirmAction('Run on '+ids.length+' bots?',action==='update'?'Pull latest code and restart each selected bot. Dependency changes must be installed separately.':'Apply '+action+' to the selected bots?'))return;
  try{await api('bulk','POST',{ids,action});toast('Bulk action queued. Open Jobs for per-bot results.');}catch(e){toast(e.message);}
};
async function refreshHealth(){
  if(telemetryBusy||$('shell').hidden||document.hidden)return;
  telemetryBusy=true;
  try{
    const h=await api('health');
    const percentage=v=>v==null?'—':Math.round(v)+'%';
    const readings=[['CPU',percentage(h.cpu_percent)],['Memory',percentage(h.ram_percent)],['Disk',percentage(h.disk_percent)],['Temperature',h.temperature==null?'—':h.temperature+'°C'],['Uptime',h.uptime==null?'—':Math.floor(h.uptime/86400)+'d '+Math.floor(h.uptime%86400/3600)+'h'],['Load · 1 min',h.load?h.load[0].toFixed(2):'—']];
    $('health-values').innerHTML=readings.map(([label,value])=>`<div><span>${label}</span><strong>${esc(value)}</strong></div>`).join('');
    $('connection-status').textContent=(h.demo?'Demo readings · ':'Connected · ')+new Date().toLocaleTimeString();
  }catch(e){$('connection-status').textContent='Disconnected · retrying automatically';}
  finally{telemetryBusy=false;}
}
function showManagement(title,panel,bot=null){
  gitReview=null;managementPanel=panel;managementBot=bot;managementRevision=null;
  $('management-title').textContent=title;$('management-content').innerHTML='<p class="muted">Loading…</p>';$('management-error').textContent='';$('management-status').textContent='';
  if(!$('management-dialog').open)$('management-dialog').showModal();
}
$('close-management').onclick=()=>{$('management-dialog').close();managementPanel=null;managementBot=null;};
$('management-dialog').addEventListener('close',()=>{managementPanel=null;managementBot=null;});
document.querySelectorAll('[data-panel]').forEach(button=>button.onclick=()=>openPanel(button.dataset.panel));
async function openPanel(panel){
  showManagement({jobs:'Background jobs',activity:'Activity history',discovery:'Discover existing services',schedules:'Scheduled actions',alerts:'Telegram alerts',account:'Account settings',power:'Pi power',system:'Pi Deck updates'}[panel],panel);
  try{
    if(panel==='jobs'||panel==='activity'){await refreshPanel();return;}
    if(panel==='system'){
      const info=await api('system/update'),s=info.state;
      if(managementPanel!==panel)return;
      $('management-content').innerHTML=`<p class="notice">${info.demo?'Demo only: application updates and restarts are simulated.':'Updates run as the dashboard service account (root in the supplied installation). Only update from a trusted upstream.'}</p><p class="muted">Checkout: ${esc(info.repo)}<br>Service: ${esc(info.service)}<br>Commit: ${esc(s.current||'Not checked')} · ${esc(s.branch||'Unknown branch')} · ${s.behind??'Unknown'} behind</p>
      <p class="notice">Update code, install changed requirements, then restart Pi Deck. The dashboard will disconnect briefly; your bots keep running. Restart waits for the background queue to be empty.</p>
      ${s.restart_required?'<p class="notice">Application files changed. Restart Pi Deck when ready.</p>':''}
      <button class="secondary" data-system-review="1">Review changes / force update</button>
      <div class="maintenance-actions">${[['check','Check updates'],['pull','Pull latest'],['dependencies','Install requirements'],['rollback','Roll back code'],['resume','Return to branch']].map(([action,label])=>`<button class="secondary" data-system-action="${action}">${label}</button>`).join('')}</div>
      <p class="muted">Code rollback does not undo dependency or state changes. Private configuration and state are backed up outside the checkout before modifying code.</p>
      ${s.private_backup?`<p class="muted">Private backup: ${esc(s.private_backup)}</p>`:''}${s.recovery?`<p class="muted">Recovery ref: <code>${esc(s.recovery.ref)}</code><br>Original commit: <code>${esc(s.recovery.head)}</code></p>`:''}
      <form id="system-restart-form" class="form-grid">${input('confirm','Type pi-deck to restart the dashboard')}<button class="danger" ${info.restart_pending?'disabled':''}>Restart Pi Deck</button></form>`;
    }else if(panel==='discovery'){
      const data=await api('discovery');
      $('management-content').innerHTML='<p class="notice">Local services are candidates, not automatically identified bots. Verify paths and ownership before importing.</p>'+data.services.map(s=>`<div class="list-row"><div><strong>${esc(s.service)}</strong><p class="muted small">${esc(s.description)}</p></div><button class="secondary" data-import="${esc(s.service)}">Import manually</button></div>`).join('')+(data.services.length?'':'<p class="muted">No unregistered local services found.</p>');
    }else if(panel==='schedules'){await renderSchedules();}
    else if(panel==='alerts'){
      const a=await api('alerts');
      $('management-content').innerHTML=`<p class="notice">When enabled, sends crash/resource alerts and recovery notifications to your Telegram chat. Notifications have a 15-minute cooldown. Demo mode never sends messages.</p><form id="alerts-form" class="form-grid">${input('token','Bot token (leave blank to keep saved token)','','password')}${input('chat_id','Telegram chat ID',a.chat_id)}${input('temperature','Temperature threshold °C',a.temperature,'number')}${input('disk','Disk threshold %',a.disk,'number')}<label class="checkbox-field wide"><input name="enabled" type="checkbox" ${a.enabled?'checked':''}> Enable notifications</label><p class="muted small wide">${a.configured?'Token configured.':'No token configured.'} ${esc(a.last_error||'')}</p><button class="primary">Save alert settings</button></form>`;
    }else if(panel==='account'){
      const a=await api('account');
      $('management-content').innerHTML=`<p class="notice">Saving changes revokes all sessions, including this one. Leave the new password blank to keep it.</p><form id="account-form" class="form-grid">${input('username','Username',a.username)}${input('current_password','Current password','','password')}${input('password','New password (12–256 characters)','','password')}${input('repeat','Repeat new password','','password')}${input('session_minutes','Session timeout in minutes',a.session_minutes,'number')}<button class="primary">Save and revoke sessions</button></form>`;
      $('account-form').elements.username.disabled=true;
    }else if(panel==='power'){
      $('management-content').innerHTML=`<p class="notice">Reboot disconnects this dashboard until the Pi is ready. Shutdown requires turning the Pi back on manually. Demo mode simulates both actions.</p><form id="power-form" class="form-grid"><label>Action<select name="action"><option value="reboot">Reboot</option><option value="poweroff">Shut down</option></select></label>${input('confirm','Type REBOOT or POWEROFF')}<button class="danger">Request power action</button></form>`;
    }
  }catch(e){$('management-error').textContent=e.message;}
}
function jobSteps(job){
  if(!job.steps?.length)return '<p class="muted small">'+(job.state==='queued'?'Steps will appear when this job starts.':'Step history is unavailable for this older job.')+'</p>';
  return '<ol class="job-steps" aria-label="Job step history">'+job.steps.map(step=>`<li data-state="${esc(step.state)}"><div><strong>${esc(step.message)}</strong><span class="muted small">${esc(step.state==='info'?'Recorded':step.state)} · ${esc(dateText(step.time))}${step.duration!=null?' · '+esc(step.duration)+'s':''}</span></div></li>`).join('')+'</ol>';
}
async function refreshPanel(){
  if(panelLoading||!$('management-dialog').open)return;panelLoading=true;
  const panel=managementPanel;
  try{
    if(panel==='jobs'){
      const data=await api('jobs');if(managementPanel!==panel)return;
      $('management-content').innerHTML=data.jobs.map(j=>`<div class="list-row"><div><strong>${esc(j.label)} ${esc(j.bot)}</strong><p class="muted small">${dateText(j.time)} · ${esc(j.state)}</p><p>${esc(j.message)}</p>${j.result?`<p class="muted">${esc(j.result.message||'')}</p>${j.result.current?`<p class="muted small">Commit ${esc(j.result.current)} · ${esc(j.result.branch||'detached')} · ${j.result.behind??'Unknown'} behind</p>`:''}${(j.result.results||[]).map(r=>`<p class="muted small">${esc(r.bot)}: ${esc(r.outcome)}</p>`).join('')}`:''}${jobSteps(j)}</div></div>`).join('')||'<p class="muted">No jobs yet.</p>';
    }else if(panel==='activity'){
      const data=await api('activity');if(managementPanel!==panel)return;
      $('management-content').innerHTML=data.events.map(e=>`<div class="list-row"><span>${esc(e.action)} · ${esc(e.bot)} · ${esc(e.outcome)}</span><time class="muted small">${dateText(e.time)}</time></div>`).join('')||'<p class="muted">No activity yet.</p>';
    }
  }catch(e){$('management-error').textContent=e.message;}finally{panelLoading=false;}
}
async function renderSchedules(){
  const [data,jobData]=await Promise.all([api('schedules'),api('jobs')]);
  $('management-content').innerHTML=`<p class="notice">Intervals run while Pi Deck is running. Missed intervals run once after restart; overlapping runs are skipped. Times below use this device’s timezone.</p><form id="schedule-form" class="form-grid"><label>Bot<select name="bot">${bots.map(b=>`<option value="${esc(b.id)}">${esc(b.name)}</option>`).join('')}</select></label><label>Action<select name="action"><option value="backup">Configuration backup</option><option value="check">Check updates</option><option value="restart">Restart bot</option></select></label>${input('minutes','Every N minutes (5–10080)',60,'number')}<button class="primary" ${bots.length?'':'disabled'}>Add schedule</button></form><div>${data.schedules.map(s=>`<div class="list-row"><div><strong>${esc(s.bot)} · ${esc(s.action)}</strong><p class="muted small">Every ${s.minutes} min · ${s.enabled?'Enabled':'Disabled'}<br>Next: ${s.enabled?dateText(s.next):'—'}<br>Last queued: ${dateText(s.last)} · ${esc(jobData.jobs.find(j=>j.id===s.job)?.state||'Not run yet')}</p></div><div class="row-actions"><button class="secondary" data-schedule="${s.id}" data-enabled="${!s.enabled}">${s.enabled?'Disable':'Enable'}</button><button class="secondary" data-delete-schedule="${s.id}">Delete</button></div></div>`).join('')}</div>`;
}
async function openBotManagement(bot){
  showManagement(bot.name,'bot',bot);
  try{
    const [settings,backups,versions]=await Promise.all([api(`bots/${bot.id}/settings`),api(`bots/${bot.id}/backups`),api('versions')]);
    if(managementBot?.id!==bot.id)return;
    managementRevision=settings.revision;
    const b=settings.bot,v=versions.versions[bot.id];
    $('management-content').innerHTML=`<details open><summary>Registration settings</summary><p class="muted small">These paths tell Pi Deck which files to manage. Change the runtime and launch command in Service file.</p><form id="bot-settings-form" class="form-grid">${input('name','Name',b.name)}${input('description','Description',b.description)}${iconPicker(b.icon)}${input('user','Linux owner',b.user)}${input('service','Systemd service',b.service)}${input('repo','Repository path',b.repo)}${input('env','Environment file (optional; leave blank if unused)',b.env)}<button class="primary">Save settings</button><button type="button" class="secondary" data-edit-service="${esc(bot.id)}">Edit service / launch command</button></form></details>
    <details open><summary>Updates and dependencies</summary><p class="muted small">${v?`Commit ${esc(v.current||'unknown')} · ${esc(v.branch||'unknown')} · ${v.behind??'Unknown'} behind · checked ${dateText(v.checked)}`:'No update check yet.'}</p><p class="notice">Rollback changes code only and leaves a detached checkout. It does not undo dependency or database changes. Return to branch before pulling again. Restart separately after rollback.</p><button type="button" class="secondary" data-review-git="1">Review changes / force update</button><div class="maintenance-actions">${[['check','Check updates'],['pull','Pull latest'],['update','Update and restart'],['dependencies','Install requirements'],['rollback','Roll back code'],['resume','Return to branch']].map(([action,label])=>`<button class="secondary" data-maintenance="${action}">${label}</button>`).join('')}</div></details>
    <details><summary>Configuration backups (${backups.backups.length})</summary><p class="muted small">Latest 20 copies per file. A backup is created before each save. Preview an older file, then save it to restore.</p><button class="secondary" data-maintenance="backup">Back up now</button>${backups.backups.map(b=>`<div class="list-row"><span>${esc(b.kind)} · ${dateText(b.time)}</span><button class="secondary" data-backup="${b.id}">Preview / restore</button></div>`).join('')||'<p class="muted">No file backups yet.</p>'}</details>
    <details><summary>Remove bot</summary><p class="notice">Unregister keeps the service running and all files intact. Uninstall stops and disables the service, backs it up, and removes only its service file. Repository and environment files are always retained.</p><form id="remove-form" class="form-grid">${input('confirm','Type bot ID: '+bot.id)}<label class="checkbox-field"><input type="checkbox" name="uninstall"> Also uninstall the service</label><button class="danger">Remove bot</button></form></details>`;
  }catch(e){$('management-error').textContent=e.message;}
}
function diffMarkup(patch){
  if(!patch)return '<p class="muted">No changes.</p>';
  let oldLine=0,newLine=0,rows=[],files=[],name='Changes';
  const flush=()=>{if(rows.length)files.push(`<details class="diff-file" open><summary>${esc(name)}</summary><div class="diff-scroll"><table class="diff-table" aria-label="Code changes"><tbody>${rows.join('')}</tbody></table></div></details>`);rows=[];};
  for(const line of patch.split('\n').slice(0,4000)){
    if(line.startsWith('diff --git ')){flush();name=line.slice(11);oldLine=0;newLine=0;continue;}
    const hunk=line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if(hunk){oldLine=Number(hunk[1]);newLine=Number(hunk[2]);}
    const meta=hunk||(!oldLine&&!newLine);
    const added=!meta&&line.startsWith('+'),removed=!meta&&line.startsWith('-'),context=!meta&&line.startsWith(' ');
    const left=removed||context?oldLine++:'',right=added||context?newLine++:'';
    rows.push(`<tr class="${meta?'diff-meta':added?'diff-add':removed?'diff-remove':''}"><td class="diff-number">${left}</td><td class="diff-number">${right}</td><td><code>${esc(line)}</code></td></tr>`);
  }
  flush();return files.join('');
}
async function openGitReview(bot){
  showManagement(bot.name+' · Review code changes','git-review',bot);
  try{
    const data=await api(bot.system?'system/git-preview':`bots/${bot.id}/git-preview`,'POST',{});
    if(managementPanel!=='git-review'||managementBot?.id!==bot.id)return;
    gitReview=data;
    const recovery=data.recovery;
    $('management-content').innerHTML=`<p class="notice">${data.demo?'Demo diff — sample code only. ':''}Review the changes before replacing local code. Force update resets this branch to the reviewed upstream commit, including replacing local commits and staged or unstaged edits. ${bot.system?'Pi Deck':'The bot'} is not restarted.</p>
      <p class="muted">${esc(data.branch)} → ${esc(data.upstream)} · ${data.ahead} local commits · ${data.behind} incoming commits<br>Reviewed target: ${esc(data.target)}</p>
      <p class="muted">A local Git recovery ref is saved before replacement. Untracked and ignored files are retained; conflicting paths block the update. Avoid editing the checkout while the job runs.</p>
      ${recovery?`<p class="notice">Last recovery ref: <code>${esc(recovery.ref)}</code><br>Original commit: <code>${esc(recovery.head)}</code>${recovery.stash?`<br>Recover saved edits on the Pi with <code>git stash apply --index ${esc(recovery.ref)}</code> after returning to the original commit.`:''}</p>`:''}
      ${data.sections.map(section=>`<section class="diff-section"><h3>${esc(section.title)}</h3>${diffMarkup(section.patch)}</section>`).join('')}
      <p class="muted">${data.untracked_count} untracked/ignored paths retained${data.untracked.length?': '+data.untracked.map(esc).join(', '):'.'}</p>
      ${data.blockers.map(reason=>`<p class="error">${esc(reason)}</p>`).join('')}
      <form id="force-update-form" class="form-grid"><p class="notice wide">Only continue if you are satisfied the local changes can be replaced. This does not install dependencies or restart ${bot.system?'Pi Deck':'the bot'}.</p>${input('confirm','Type '+bot.id+' to confirm')}<button class="danger" ${data.can_force?'':'disabled'}>Back up and force update</button></form>`;
  }catch(e){if(managementPanel==='git-review'&&managementBot?.id===bot.id)$('management-error').textContent=e.message;}
}
$('management-dialog').addEventListener('click',async e=>{
  const button=e.target.closest('button');if(!button)return;
  const d=button.dataset;
  if(!Object.keys(d).length)return;
  button.disabled=true;
  try{
    if(d.reloadDashboard){location.reload();
    }else if(d.systemReview){await openGitReview({id:'pi-deck',name:'Pi Deck',system:true});
    }else if(d.systemAction){
      if(d.systemAction!=='check'&&!window.confirm('Run '+d.systemAction+' for Pi Deck itself? Application code and dependencies run with dashboard administrator privileges. Restart separately when ready.'))return;
      await api('system/update','POST',{action:d.systemAction});await openPanel('jobs');
    }else if(d.reviewGit){await openGitReview(managementBot);
    }else if(d.import){
      $('management-dialog').close();$('add-bot').click();field('mode').value='manual';setAddMode();field('service').value=d.import;await detectService();
    }else if(d.maintenance){
      if(['update','rollback','resume','dependencies'].includes(d.maintenance)&&!window.confirm('Run '+d.maintenance+' for '+managementBot.name+'? Dependency installation executes package code as the bot owner.'))return;
      await api(`bots/${managementBot.id}/maintenance`,'POST',{action:d.maintenance});$('management-status').textContent='Queued. See Jobs for progress and results.';
    }else if(d.editService){
      const bot=managementBot;$('management-dialog').close();await openDetail(bot,'service');
    }else if(d.backup){
      const bot=managementBot;const old=await api(`bots/${bot.id}/backups/${d.backup}`);
      if(old.kind==='settings'){
        const previous=JSON.parse(old.content),form=$('bot-settings-form');
        for(const [key,value] of Object.entries(previous))if(form.elements.namedItem(key))form.elements.namedItem(key).value=value;
        $('management-status').textContent='Previous settings loaded for review. Save settings to restore them.';
        form.closest('details').open=true;return;
      }
      $('management-dialog').close();await openDetail(bot,old.kind);
      if(!editing)return;
      editing.content=old.content;$('reveal').checked=old.kind==='env';$('editor').disabled=false;$('editor').value=old.content;$('save-file').disabled=false;
      $('file-warning').textContent='Previewing backup from '+dateText(old.time)+'. Compare with the current file before saving. Saving restores this content and backs up the current version.';
    }else if(d.schedule){await api('schedules/'+d.schedule,'PUT',{enabled:d.enabled==='true'});await renderSchedules();}
    else if(d.deleteSchedule){await api('schedules/'+d.deleteSchedule,'DELETE',{});await renderSchedules();}
  }catch(error){$('management-error').textContent=error.message;}finally{button.disabled=false;}
});
$('management-content').addEventListener('submit',async e=>{
  e.preventDefault();const form=e.target;const data=Object.fromEntries(new FormData(form));const submit=form.querySelector('button[type=submit],button:not([type])');if(submit)submit.disabled=true;
  $('management-error').textContent='';$('management-status').textContent='';
  try{
    if(form.id==='force-update-form'){
      if(!gitReview||!gitReview.can_force)throw new Error('Load a complete preview first.');
      const bot=managementBot;
      await api(bot.system?'system/update':`bots/${bot.id}/force-update`,'POST',{action:'force',confirm:data.confirm,revision:gitReview.revision});
      gitReview=null;
      await openPanel('jobs');
      toast('Force update queued. See the job steps and recovery reference.');
    }else if(form.id==='system-restart-form'){
      const result=await api('system/restart','POST',data);$('management-status').textContent=result.message;
      if(!demo){$('management-content').innerHTML='<p class="notice">Pi Deck is restarting. The dashboard may disconnect briefly.</p><button class="primary" data-reload-dashboard="1">Reload dashboard</button>';}
    }else if(form.id==='bot-settings-form'){
      await api(`bots/${managementBot.id}/settings`,'PUT',{...data,revision:managementRevision});await refresh();await openBotManagement(bots.find(b=>b.id===managementBot.id));$('management-status').textContent='Settings saved.';
    }else if(form.id==='remove-form'){
      await api(`bots/${managementBot.id}/remove`,'POST',{...data,uninstall:form.elements.uninstall.checked});$('management-status').textContent='Removal queued. See Jobs for the result.';
    }else if(form.id==='schedule-form'){
      await api('schedules','POST',{...data,minutes:Number(data.minutes)});await renderSchedules();
    }else if(form.id==='alerts-form'){
      const result=await api('alerts','PUT',{...data,enabled:form.elements.enabled.checked,temperature:Number(data.temperature),disk:Number(data.disk)});form.elements.token.value='';$('management-status').textContent=result.message;
    }else if(form.id==='account-form'){
      if(data.password!==data.repeat)throw new Error('New passwords must match.');
      await api('account','POST',{...data,session_minutes:Number(data.session_minutes)});location.reload();
    }else if(form.id==='power-form'){
      await api('power','POST',data);$('management-status').textContent=demo?'Demo power action queued.':'Power action queued. Connection will drop; this page will retry automatically.';
    }
  }catch(error){$('management-error').textContent=error.message;}finally{if(submit)submit.disabled=false;}
});
function logsPath(){return `bots/${editing.bot.id}/log-stream?priority=${encodeURIComponent($('logs-priority').value)}&q=${encodeURIComponent($('logs-search').value)}`;}
async function updateLiveLogs(){
  if(logsBusy||!editing||editing.kind!=='logs'||!$('detail').open)return;
  logsBusy=true;const id=editing.bot.id;
  try{
    const data=await api(logsPath());if(editing?.bot.id!==id||editing.kind!=='logs')return;
    const el=$('log-content'),atBottom=el.scrollTop+el.clientHeight>=el.scrollHeight-30;
    el.textContent=data.content||'No matching entries.';if(atBottom)el.scrollTop=el.scrollHeight;
    $('save-note').textContent='Updated '+new Date().toLocaleTimeString()+' · UTC log timestamps';$('dialog-error').textContent='';
  }catch(e){$('dialog-error').textContent='Log refresh failed: '+e.message;}finally{logsBusy=false;}
}
$('logs-priority').onchange=updateLiveLogs;let searchTimer;
$('logs-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(updateLiveLogs,250);};
$('logs-live').onchange=()=>{if($('logs-live').checked)updateLiveLogs();};
$('download-logs').onclick=()=>{if(editing)window.location.href='/api/'+logsPath()+'&download=1';};
setInterval(()=>{if(!document.hidden&&$('logs-live').checked)updateLiveLogs();},4000);
setInterval(()=>{refreshHealth();if(!document.hidden)refreshPanel();},5000);
let dashboardPolling=false;
setInterval(async()=>{if(dashboardPolling||document.hidden||$('shell').hidden||busy)return;dashboardPolling=true;try{await refresh();}catch(e){}finally{dashboardPolling=false;}},15000);
refreshHealth();
