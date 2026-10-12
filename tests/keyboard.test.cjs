const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const source=readFileSync(require('node:path').join(__dirname,'../static/keyboard.js'),'utf8');
function setup(){
  const handlers={},calls=[];
  const elements=new Map();
  const element=id=>{if(!elements.has(id))elements.set(id,{hidden:false,focus:()=>calls.push('focus:'+id),click:()=>calls.push('click:'+id),showModal:()=>calls.push('open:'+id)});return elements.get(id);};
  const document={addEventListener:(name,fn)=>handlers[name]=fn,getElementById:element,querySelectorAll:()=>[],querySelector:selector=>selector==='dialog[open]'?null:element(selector)};
  const context={document};runInNewContext(source,context);handlers.DOMContentLoaded();
  const press=(extra={})=>{const event={key:'J',altKey:true,shiftKey:true,target:{closest:()=>null},preventDefault:()=>calls.push('prevent'),...extra};handlers.keydown(event);};
  return {calls,document,press,context,element};
}
test('shortcuts open navigation and do not fire in text fields or dialogs',()=>{
  const h=setup();h.press();assert.deepEqual(h.calls,['prevent','click:[data-panel="jobs"]']);
  h.calls.length=0;h.press({target:{closest:()=>({})}});assert.deepEqual(h.calls,[]);
  h.document.querySelector=()=>({});h.press();assert.deepEqual(h.calls,[]);
});
test('arrow navigation moves focus without activating bot actions',()=>{
  const h=setup();let clicked=false,focused=false;
  const next={getClientRects:()=>[1],focus:()=>focused=true,click:()=>clicked=true};
  const target={getClientRects:()=>[1],matches:()=>true,closest:selector=>selector.includes('input')?null:group};
  const group={querySelectorAll:()=>[target,next],matches:()=>false};
  h.press({key:'ArrowRight',altKey:false,shiftKey:false,target});
  assert.equal(focused,true);assert.equal(clicked,false);
});
test('refresh restores keyboard focus to the same job output',()=>{
  const h=setup();let focused=false;
  h.document.activeElement={id:'',dataset:{outputKey:'job:stdout'},closest:()=>null};
  const root={contains:()=>true,querySelectorAll:()=>[{dataset:{outputKey:'job:stdout'},isConnected:true,focus:()=>focused=true}]};
  h.context.keyboardBookmark(root)();assert.equal(focused,true);
});
