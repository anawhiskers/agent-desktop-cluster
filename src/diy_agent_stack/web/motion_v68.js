// Native input never passes through this presentation module.
export const presenceColors=Object.freeze({lilac:'#b49cff',rose:'#ff83ba',mint:'#62e5c5',sky:'#72c8ff',amber:'#ffd478',coral:'#ff9e8d'});
const boundedText=(value,max,fallback='')=>typeof value==='string'?value.replace(/[\u0000-\u001f\u007f]/g,' ').replace(/\s+/g,' ').trim().slice(0,max)||fallback:fallback;
export function presencePresentation(item={}){
 item=item&&typeof item==='object'?item:{};
 const name=boundedText(item.label,60,item.id==='human'?'You':'Agent');
 const color=(Object.hasOwn(presenceColors,item.preset)?presenceColors[item.preset]:null)||(Object.values(presenceColors).includes(item.color)?item.color:presenceColors.lilac);
 const phases={working:'Working',moving:'Moving',idle:'Idle',finished:'Finished',stopped:'Stopped'};
 const phase=Object.hasOwn(phases,item.phase)?phases[item.phase]:'Present';
 const state=item.typing?(item.protected?'Protected input':'Typing'):phase;
 return {name,color,state,working:['working','moving'].includes(item.phase)||Boolean(item.typing)};
}
export function renderPresenceChip(chip,item){
 const p=presencePresentation(item),doc=chip.ownerDocument;
 const name=doc.createElement('strong'),state=doc.createElement('span');
 name.className='presence-name';state.className='presence-state';name.textContent=p.name;state.textContent=p.state;
 chip.replaceChildren(name,state);chip.style.setProperty('--cursor-color',p.color);
 chip.classList.toggle('is-working',p.working);chip.setAttribute('aria-label',p.name+' · '+p.state);chip.title=p.name+' · '+p.state;
 return p;
}
export function renderCursorLabel(node,label,item){
 const p=presencePresentation(item);label.textContent=p.name;
 node.dataset.presenceState=p.state;node.style.setProperty('--cursor-color',p.color);
 node.setAttribute('aria-label',p.name+' · '+p.state);label.title=p.name+' · '+p.state;
 return p;
}
// Time-based damping avoids frame-rate-dependent easing.
export function cursorStep(from,target,elapsed,{snap=false}={}){
 if(snap||!from||!Number.isFinite(from.x)||!Number.isFinite(from.y))return {...target,settled:true};
 const distance=Math.hypot(target.x-from.x,target.y-from.y);
 if(distance<=.25)return {...target,settled:true};
 const fraction=1-Math.exp(-Math.max(0,Math.min(64,elapsed))/26);
 return {x:from.x+(target.x-from.x)*fraction,y:from.y+(target.y-from.y)*fraction,settled:false};
}
// Only consecutive slow paints in an active burst count; idle gaps are ignored.
export function createMotionBudget(){
 let slow=0,last=0,holdUntil=0;
 return {sample(now,{active=false,reduced=false}={}){
  const gap=last?now-last:0;last=now;
  if(active&&gap>34&&gap<250)slow++;else slow=0;
  if(slow>=3){holdUntil=now+5000;slow=0;}
  return {low:reduced||now<holdUntil,elapsed:gap>0&&gap<250?gap:16.7};
 },reset(){slow=0;last=0;holdUntil=0;}};
}
// Coordinator-only metadata. Presence phases do not imply a reservation.
export function workStatusEntries(status,now=Date.now()/1000){
 if(!status||status.version!==68)return [];
 const reservations=(Array.isArray(status.reservations)?status.reservations:[]).flatMap(item=>{
  if(!['shared','exclusive'].includes(item?.mode)||!['desktop','machine'].includes(item.scope)||!Number.isFinite(item.expires)||Math.abs(item.expires)>8640000000000||item.expires<=now)return [];
  return [{kind:item.mode,label:boundedText(item.label,32,'Agent'),reason:boundedText(item.reason,160),expires:item.expires,state:(item.mode==='exclusive'?'Reserved':'Shared')+' · '+item.scope}];
 });
 const operations=(Array.isArray(status.active_operations)?status.active_operations:[]).flatMap(item=>{
  if(item?.mode!=='background'||!Number.isFinite(item.expires)||Math.abs(item.expires)>8640000000000||!['dispatching','running','unknown'].includes(item.state))return [];
  const expired=Boolean(item.lease_expired)||item.expires<=now,unresolved=expired||item.state==='unknown';
  return [{kind:unresolved?'unresolved':'background',label:boundedText(item.label,32,'Agent'),reason:boundedText(item.reason,160),expires:item.expires,expired,state:unresolved?'Background · Needs review':item.state==='dispatching'?'Background · Starting':'Background'}];
 });
 return [...reservations,...operations].slice(0,12);
}
export function renderWorkStatus(container,status,now=Date.now()/1000){
 const entries=workStatusEntries(status,now),doc=container.ownerDocument;
 container.replaceChildren();
 for(const entry of entries){
  const chip=doc.createElement('span'),name=doc.createElement('strong'),state=doc.createElement('span'),time=doc.createElement('time');
  chip.className='work-status-chip work-status-'+entry.kind;name.textContent=entry.label;state.textContent=entry.state;
  time.dateTime=new Date(entry.expires*1000).toISOString();time.textContent=entry.expired?'lease expired':'until '+new Date(entry.expires*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
  chip.append(name,state,time);chip.title=[entry.label,entry.state,entry.reason,entry.expired?'Lease expired':'Expires '+time.textContent.slice(6)].filter(Boolean).join(' · ');
  chip.setAttribute('aria-label',chip.title);container.append(chip);
 }
 return entries;
}
