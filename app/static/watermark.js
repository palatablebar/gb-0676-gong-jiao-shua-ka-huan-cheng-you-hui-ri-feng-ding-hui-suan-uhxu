const feedback=document.querySelector('#feedback');
const select=document.querySelector('#deviceSelect');
function say(text,bad){feedback.textContent=text;feedback.style.color=bad?'var(--bad)':'var(--ok)';}
async function call(url,options){
  say('处理中…');
  const res=await fetch(url,options||{method:'POST'});
  const body=await res.json();
  if(!res.ok){say(body.message||'请求失败',true);throw Error(body.message);}
  return body;
}
select?.addEventListener('change',()=>{location.href='/watermark?device='+encodeURIComponent(select.value);});
function currentDevice(){return select.value;}
document.querySelector('#archiveLink').addEventListener('click',event=>{
  event.preventDefault();
  location.href='/api/ingest/devices/'+encodeURIComponent(currentDevice())+'/archive';
});
document.querySelector('#fillGap').addEventListener('click',async()=>{
  try{await call('/api/ingest/demo/fill-gap');location.href='/watermark?device=OB-101';}catch(e){}
});
document.querySelector('#resetDemo').addEventListener('click',async()=>{
  try{await call('/api/ingest/demo/reset');location.href='/watermark?device=OB-101';}catch(e){}
});
document.querySelectorAll('.seal-btn').forEach(btn=>btn.addEventListener('click',async()=>{
  const day=btn.dataset.day;
  try{
    const body=await call(`/api/ingest/devices/${encodeURIComponent(currentDevice())}/seal/${day}`);
    say((body.idempotent?'该日结此前已封存':'封存成功：')+day+' 快照 '+body.snapshot_sha256.slice(0,12)+'…');
    setTimeout(()=>location.reload(),700);
  }catch(e){}
}));
