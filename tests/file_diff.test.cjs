const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const source=readFileSync(require('node:path').join(__dirname,'../static/management.js'),'utf8');
const context={esc:value=>String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')};
runInNewContext(source.slice(source.indexOf('function diffMarkup('),source.indexOf('async function openGitReview(')),context);
test('file preview shows escaped additions, removals and both line numbers',()=>{
  const html=context.diffMarkup('--- Current file\n+++ Proposed file\n@@ -1,2 +1,2 @@\n [Service]\n-ExecStart=old\n+ExecStart=<script>\n');
  assert.match(html,/class="diff-remove"><td class="diff-number">2<\/td><td class="diff-number"><\/td>/);
  assert.match(html,/class="diff-add"><td class="diff-number"><\/td><td class="diff-number">2<\/td>/);
  assert.match(html,/\+ExecStart=&lt;script&gt;/);
  assert.doesNotMatch(html,/<script>/);
  assert.match(context.diffMarkup(''),/No changes/);
});
