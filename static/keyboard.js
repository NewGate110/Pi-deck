/* Native Tab, Enter, Space and Escape remain available throughout the UI. */
function keyboardBookmark(root){
  const active=document.activeElement;
  if(!active||!root.contains(active))return ()=>{};
  const card=active.closest('[data-card-bot]');
  const bot=card?.dataset.cardBot;
  const output=active.dataset.outputKey;
  const id=active.id;
  const controls=card?[...card.querySelectorAll('button,input')]:[];
  const index=controls.indexOf(active);
  return ()=>{
    let target=id?document.getElementById(id):null;
    if(output)target=[...root.querySelectorAll('[data-output-key]')].find(el=>el.dataset.outputKey===output);
    if(bot){
      const replacement=[...root.querySelectorAll('[data-card-bot]')].find(el=>el.dataset.cardBot===bot);
      target=replacement?.querySelectorAll('button,input')[index];
    }
    if(target?.isConnected&&!target.disabled)target.focus({preventScroll:true});
    else if(!document.querySelector('dialog[open]'))document.getElementById('bots-heading')?.focus({preventScroll:true});
  };
}
document.addEventListener('DOMContentLoaded',()=>{
  const help=document.getElementById('keyboard-dialog');
  document.getElementById('keyboard-help').onclick=()=>help.showModal();
  document.getElementById('close-keyboard').onclick=()=>help.close();
  const botList=document.getElementById('bots');
  botList.tabIndex=-1;
  document.querySelector('.skip-link').onclick=()=>botList.focus();
  let returnFocus=()=>{};
  document.addEventListener('focusin',event=>{
    if(event.target.closest('#shell'))returnFocus=keyboardBookmark(document.getElementById('shell'));
  });
  document.querySelectorAll('dialog').forEach(dialog=>dialog.addEventListener('close',()=>{
    if(!document.querySelector('dialog[open]')&&document.activeElement===document.body)returnFocus();
  }));
  document.addEventListener('keydown',event=>{
    if(event.defaultPrevented||event.isComposing||event.repeat)return;
    const target=event.target;
    const typing=target.closest('input,textarea,select,[contenteditable="true"]');
    if(event.altKey&&event.shiftKey&&!event.ctrlKey&&!event.metaKey&&!typing&&!document.querySelector('dialog[open]')&&!document.getElementById('shell').hidden){
      const actions={j:()=>document.querySelector('[data-panel="jobs"]').click(),n:()=>document.getElementById('add-bot').click(),r:()=>document.getElementById('refresh').click(),b:()=>document.querySelector('#bots input,#bots button')?.focus(),k:()=>help.showModal()};
      const action=actions[event.key.toLowerCase()];
      if(action){event.preventDefault();action();}
      return;
    }
    if(typing||event.altKey||event.ctrlKey||event.metaKey||event.shiftKey)return;
    const group=target.closest('.management-toolbar,.bot-card');
    if(!group||!target.matches('button'))return;
    let controls=[...group.querySelectorAll('button:not(:disabled)')].filter(el=>el.getClientRects().length);
    let index=controls.indexOf(target),next;
    if(event.key==='ArrowRight')next=controls[(index+1)%controls.length];
    if(event.key==='ArrowLeft')next=controls[(index+controls.length-1)%controls.length];
    if(event.key==='Home')next=controls[0];
    if(event.key==='End')next=controls.at(-1);
    if(group.matches('.bot-card')&&['ArrowUp','ArrowDown'].includes(event.key)){
      const cards=[...document.querySelectorAll('.bot-card')];
      const card=cards[cards.indexOf(group)+(event.key==='ArrowDown'?1:-1)];
      const buttons=card?[...card.querySelectorAll('button:not(:disabled)')]:[];
      next=buttons[Math.min(index,buttons.length-1)];
    }
    if(next){event.preventDefault();next.focus();}
  });
});
