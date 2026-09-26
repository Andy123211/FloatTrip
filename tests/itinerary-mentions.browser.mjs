// Run with the app on localhost:8765: ego-browser nodejs -e "$(cat tests/itinerary-mentions.browser.mjs)"
// API responses and authentication are isolated in this page; no real user data is changed.
const task = await taskSpace('途见行程卡片与引用验证');
console.log({spaceId:task.spaceId});
const page = task.page('p1');
const source = `(() => {
 const originalGet=Storage.prototype.getItem;
 Storage.prototype.getItem=function(key){return key==='auth'?JSON.stringify({token:'mention-fixture',username:'样例旅行者'}):originalGet.call(this,key)};
 const plans=[{id:'plan-a',itinerary_id:'plan-a',root_id:'plan-a',destination:'南京',duration_days:3,version:1,start_date:'2026-09-06',end_date:'2026-09-08',created_at:'2026-09-01T08:00:00',highlights:['老城烟火寻味','山林与人文','秦淮夜色']},{id:'plan-b',itinerary_id:'plan-b',root_id:'plan-a',destination:'南京',duration_days:3,version:2,is_modified:true,start_date:'2026-09-06',end_date:'2026-09-08',created_at:'2026-09-02T08:00:00',highlights:['钟山慢行','午后逛展']},{id:'plan-c',itinerary_id:'plan-c',root_id:'plan-c',destination:'苏州',version:1,start_date:'2026-08-01',end_date:'2026-08-02',created_at:'2026-07-20T08:00:00'}];
 const conversation={id:'mention-fixture',title:'聊聊南京的行程',status:'active'};
 window.__sent=[];window.__failNext=true;
 const originalFetch=window.fetch;
 window.fetch=async(input,options={})=>{
  const url=new URL(typeof input==='string'?input:input.url,location.href);
  if(!url.pathname.startsWith('/api/'))return originalFetch(input,options);
  let data={};
  if(url.pathname==='/api/conversations')data=[conversation];
  else if(url.pathname.endsWith('/view'))data=conversation;
  else if(url.pathname.endsWith('/messages')&&options.method==='POST'){
    const body=JSON.parse(options.body);window.__sent.push(body);
    if(window.__failNext){window.__failNext=false;return new Response(JSON.stringify({detail:'样例发送失败，请重试'}),{status:503,headers:{'Content-Type':'application/json'}})}
    data={message:{id:'sent'+window.__sent.length,role:'user',content:body.content,sequence:2+window.__sent.length},run:{id:'sent-run'+window.__sent.length,conversation_id:conversation.id,kind:'chat',status:'succeeded'}};
  }
  else if(url.pathname.endsWith('/messages'))data=[{id:'cards',role:'assistant',content:'这两份南京行程都在这里。选一份，我们接着聊。',sequence:1,artifacts:[{type:'itinerary_collection',title:'南京 · 保存的旅程',items:plans.slice(0,2)}]}];
  else if(url.pathname.endsWith('/planning-brief'))data=null;
  else if(url.pathname==='/api/runs'||url.pathname.endsWith('/events'))data=[];
  else if(url.pathname==='/api/history')data=url.searchParams.has('cursor')?{items:plans.slice(2),next_cursor:null}:{items:plans.slice(0,2),next_cursor:'older'};
  else if(url.pathname.startsWith('/api/history/'))data={id:url.pathname.split('/').pop(),plan:{destination:'南京',start_date:'2026-09-06',end_date:'2026-09-08',days:[{day:1,theme:'老城烟火寻味',timeline:[{type:'attraction',name:'中山陵景区',start_time:'09:00',end_time:'11:00',photo:'/assets/trip-redesign/nanjing-cover.png'}]}]}};
  else if(url.pathname.endsWith('/stream'))return new Response('',{headers:{'Content-Type':'text/event-stream'}});
  return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
 };
})();`;
await page.cdp('Page.addScriptToEvaluateOnNewDocument',{source});
await page.cdp('Emulation.setDeviceMetricsOverride',{width:1181,height:894,deviceScaleFactor:1,mobile:false});
await page.goto('http://localhost:8765/?view_plan_id=other-open-plan');
await page.waitForSelector('.saved-itinerary-reference');

const assert=(await import('node:assert/strict')).default;
console.log(await page.screenshot({path:'/tmp/itinerary-cards-desktop.png'}));
await page.click('loc=css:button[aria-label="引用南京 · 3日 · 2026-09-06 — 2026-09-08 · V2"]');
await page.waitForSelector('.itinerary-reference-chip');
await page.fill('textarea[aria-label="给途见发送消息"]','第二天轻松一点');
await page.click('button[aria-label="发送消息"]');
await page.waitForSelector('.chat-error');
assert.equal(await page.evaluate(()=>document.querySelector('.chat-composer textarea').value),'第二天轻松一点');
assert.match(await page.evaluate(()=>document.querySelector('.itinerary-reference-chip').textContent),/V2/);
await page.click('button[aria-label="发送消息"]');
await page.waitForFunction(()=>document.querySelector('.chat-composer textarea').value==='');
let sent=await page.evaluate(()=>window.__sent);
assert.equal(sent.length,2);assert.equal(sent[1].related_itinerary_id,'plan-b');assert.match(sent[1].content,/^@南京.*V2\n第二天轻松一点$/);
assert.ok(await page.evaluate(()=>!!document.querySelector('.itinerary-reference-chip')));
console.log({failedSendPreservedDraftAndReference:true,exactVersionOverridesOpenPlan:true,referencePersists:true});
await page.fill('textarea[aria-label="给途见发送消息"]','@南京');
await page.click('button[aria-label="发送消息"]');
assert.equal((await page.evaluate(()=>window.__sent)).length,2);
assert.match(await page.evaluate(()=>document.querySelector('.chat-error').textContent),/请先从 @ 列表选择/);
await page.fill('textarea[aria-label="给途见发送消息"]','');
console.log({unselectedMentionCannotSend:true});
await page.click('button[aria-label="移除行程引用"]');
await page.fill('textarea[aria-label="给途见发送消息"]','@南京');
await page.waitForSelector('[role="option"]');
await page.press('textarea[aria-label="给途见发送消息"]','ArrowDown');
await page.press('textarea[aria-label="给途见发送消息"]','Enter');
assert.match(await page.evaluate(()=>document.querySelector('.itinerary-reference-chip').textContent),/V2/);
assert.equal((await page.evaluate(()=>window.__sent)).length,2);
await page.fill('textarea[aria-label="给途见发送消息"]','@苏州');
await page.click('text="查找更早的行程"');
await page.waitForSelector('[role="option"]');
await page.press('textarea[aria-label="给途见发送消息"]','Enter');
assert.match(await page.evaluate(()=>document.querySelector('.itinerary-reference-chip').textContent),/苏州/);
console.log({keyboardSelectionNoAccidentalSend:true,paginationAndReferenceReplacement:true});
await page.fill('textarea[aria-label="给途见发送消息"]','@不存在');
await page.waitForSelector('.itinerary-mention-popover');
await page.press('textarea[aria-label="给途见发送消息"]','Escape');
assert.equal(await page.evaluate(()=>!!document.querySelector('.itinerary-mention-popover')),false);
for(const width of [1440,1024,768,390]){
 await page.cdp('Emulation.setDeviceMetricsOverride',{width,height:894,deviceScaleFactor:1,mobile:width===390});
 if(width===768)await page.click('loc=role:button[name="对话"]');
 await page.fill('textarea[aria-label="给途见发送消息"]','');
 await page.fill('textarea[aria-label="给途见发送消息"]','@南京');
 await page.waitForSelector('[role="option"]');
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 const bounds=await page.evaluate(()=>{const r=document.querySelector('.itinerary-mention-popover').getBoundingClientRect();return{x:r.x,y:r.y,right:r.right,bottom:r.bottom}});
 assert.ok(bounds.x>=0&&bounds.right<=width+1&&bounds.y>=0,JSON.stringify(bounds));
 console.log({width,noOverflow:true,pickerVisible:true});
 if(width===390){await page.waitForFunction(()=>document.getAnimations().every(a=>a.playState!=='running'));console.log(await page.screenshot({path:'/tmp/itinerary-mention-mobile.png'}));}
 await page.press('textarea[aria-label="给途见发送消息"]','Escape');
}

await page.cdp('Emulation.clearDeviceMetricsOverride',{});
await task.finish({keep:[]});
console.log('PASS: itinerary mentions and responsive saved cards');
