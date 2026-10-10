const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const source=readFileSync(require('node:path').join(__dirname,'../static/management.js'),'utf8');
function harness(){
  const output={innerHTML:'',classList:{toggle(){}}};
  const context={$:()=>output,esc:String};
  runInNewContext(source.slice(source.indexOf('const healthHistory='),source.indexOf('async function refreshHealth()'))+';globalThis.render=renderHealth;globalThis.history=healthHistory;globalThis.graph=healthGraph;',context);
  return {...context,output};
}
test('history uses elapsed time and never fabricates earlier readings',()=>{
  const h=harness();
  h.render({cpu_percent:20,ram_percent:40,disk_percent:30},60000);
  assert.match(h.graph('cpu_percent',60000),/M300.00,80.00/);
  h.render({cpu_percent:40},65000);
  assert.match(h.graph('cpu_percent',65000),/M275.00,80.00 L300.00,60.00/);
  h.render({cpu_percent:60},130000);
  assert.equal(h.history.length,1);
});
test('missing samples and polling interruptions split the line',()=>{
  const h=harness();
  h.render({cpu_percent:20},60000);
  h.render({cpu_percent:null},65000);
  h.render({cpu_percent:40},70000);
  assert.equal((h.graph('cpu_percent',70000).match(/class="health-line"/g)||[]).length,2);
  h.render({cpu_percent:60},90000);
  assert.equal((h.graph('cpu_percent',90000).match(/class="health-line"/g)||[]).length,3);
});
test('invalid values, storage labels and disconnected readings remain truthful',()=>{
  const h=harness();
  h.render({cpu_percent:NaN,ram_percent:Infinity,disk_percent:130,load:['bad']},60000);
  assert.equal(h.history[0].cpu_percent,null);
  assert.equal(h.history[0].ram_percent,null);
  assert.equal(h.history[0].disk_percent,100);
  assert.match(h.output.innerHTML,/Storage used/);
  assert.doesNotMatch(h.output.innerHTML,/NaN|Infinity/);
  h.render({},65000,true);
  assert.equal(h.history.length,2);
  assert.equal(h.history[1].disk_percent,null);
  assert.match(h.output.innerHTML,/Waiting for connection/);
  assert.doesNotMatch(h.output.innerHTML,/<strong>100%/);
});
