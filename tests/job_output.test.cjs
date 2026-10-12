const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const source=readFileSync(require('node:path').join(__dirname,'../static/management.js'),'utf8');
test('output escapes HTML and animates only newly received text',()=>{
  const context={esc:value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')};
  runInNewContext(source.slice(source.indexOf('const jobOutputSeen='),source.indexOf('function jobSteps('))+';globalThis.render=jobCommandOutput;',context);
  const step={time:1,stdout:'<script>alert(1)</script>',stderr:'',state:'running'};
  const first=context.render({id:'job'},step,0);
  assert.ok(first.includes('&lt;script>'));
  assert.ok(!first.includes('<script>'));
  assert.ok(context.render({id:'job'},step,0).includes('<span class="job-output-new"></span>'));
  step.stdout+='\nDone';step.exit_code=0;
  const next=context.render({id:'job'},step,0);
  assert.ok(next.includes('<span class="job-output-new">\nDone</span>'));
  assert.ok(next.includes('Completed successfully'));
});
