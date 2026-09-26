// ego-browser nodejs -e "$(cat tests/clarification.browser.mjs)"
// All APIs and authentication are page-isolated fixtures.
const task=await taskSpace('途见补充信息卡片验证');console.log({spaceId:task.spaceId});
const page=task.page('p1');
const fillDate=async(index,value)=>page.evaluate(({index,value})=>{const node=document.querySelectorAll('input[type=date]')[index];Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(node,value);node.dispatchEvent(new Event('input',{bubbles:true}));node.dispatchEvent(new Event('change',{bubbles:true}));},{index,value});
const assert=(await import('node:assert/strict')).default;
const source=`(()=>{
 const get=Storage.prototype.getItem;Storage.prototype.getItem=function(key){return key==='auth'?JSON.stringify({token:'fixture',username:'样例旅行者'}):get.call(this,key)};
 const originalFetch=window.fetch;const scenario=new URL(location.href).searchParams.get('fixture')||'text';
 const question=scenario==='legacy'?'候选池未收录用户点名外滩，需要扩充候选池':scenario==='dates'?'这次旅行从哪天开始、哪天结束？':'如果都安排在同一天，时间会比较赶。你更想保留哪些安排？';
 const schema=scenario==='dates'?{type:'string',format:'date-range'}:scenario==='choice'?{type:'string',enum:['保留江边散步','保留博物馆','分到两天安排']}:scenario==='multi'?{type:'array',items:{enum:['江边散步','博物馆','老街']}}:{type:'string'};
 const interaction={kind:'run.waiting_user',interaction_id:'answer-1',question,input_schema:schema};
 const conversation={id:'clarify',title:'调整上海行程',status:'active'};
 let run={id:'clarify-run',conversation_id:'clarify',kind:'chat',status:'waiting_user',journey_step_index:2,request_snapshot:{destination:'上海'},pending_interaction:interaction};
 window.__sent=[];window.__fail=true;
 window.fetch=async(input,options={})=>{
  const url=new URL(typeof input==='string'?input:input.url,location.href);if(!url.pathname.startsWith('/api/'))return originalFetch(input,options);
  let data={};
  if(url.pathname==='/api/conversations')data=[conversation];
  else if(url.pathname.endsWith('/view'))data=conversation;
  else if(url.pathname.endsWith('/messages'))data=[{id:'m1',role:'user',content:'@上海三日游 帮我调整一下这份行程',sequence:1}];
  else if(url.pathname.endsWith('/planning-brief'))data=null;
  else if(url.pathname==='/api/runs')data=[run];
  else if(url.pathname.endsWith('/events'))data=[{kind:'custom',sequence:1,payload:interaction}];
  else if(url.pathname.endsWith('/resume')){
   window.__sent.push(JSON.parse(options.body));
   if(window.__fail){window.__fail=false;return new Response(JSON.stringify({detail:'样例网络中断，请重试'}),{status:503,headers:{'Content-Type':'application/json'}})}
   run={...run,status:'running',pending_interaction:null};data={...run,accepted_message:{id:'answer',role:'user',content:window.__sent.at(-1).value,sequence:2}};
  }
  else if(url.pathname.endsWith('/cancel')){run={...run,status:'cancelled'};data=run;}
  else if(url.pathname.startsWith('/api/history/'))data={plan:{destination:'上海',days_count:3,start_date:'2026-09-20',end_date:'2026-09-22',days:[{day:1,theme:'滨江漫游',timeline:[{type:'attraction',name:'外滩',start_time:'09:00',end_time:'11:00'}]}]}};
  else if(url.pathname==='/api/history')data={items:[],next_cursor:null};
  else if(url.pathname.endsWith('/stream'))return new Response('',{headers:{'Content-Type':'text/event-stream'}});
  return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
 };
})();`;
await page.cdp('Page.addScriptToEvaluateOnNewDocument',{source});
await page.cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:960,deviceScaleFactor:1,mobile:false});
await page.goto('http://localhost:8765/?view_plan_id=fixture&fixture=text');
await page.waitForSelector('.clarification-card');
assert.equal(await page.evaluate(()=>document.querySelector('.chat-composer textarea').disabled),true);
assert.equal(await page.evaluate(()=>!!document.querySelector('.planning-thought-steps')),false);
await page.fill('textarea[aria-label="填写补充信息"]','保留江边散步，下午再逛博物馆');
await page.press('textarea[aria-label="填写补充信息"]','Escape');
await page.click('button[aria-label="展开补充信息"]');
assert.equal(await page.evaluate(()=>document.querySelector('.clarification-card textarea').value),'保留江边散步，下午再逛博物馆');
console.log(await page.screenshot({path:'/tmp/clarification-desktop.png'}));
await page.click('text="提交并继续"');
await page.waitForSelector('.clarification-error');
assert.equal(await page.evaluate(()=>document.querySelector('.clarification-card textarea').value),'保留江边散步，下午再逛博物馆');
await page.click('text="提交并继续"');
await page.waitForFunction(()=>!document.querySelector('.clarification-card'));
assert.equal(await page.evaluate(()=>window.__sent[1].interaction_id),'answer-1');
console.log({textRetryAndCollapse:true,sameInteraction:true});
for(const scenario of ['dates','choice','multi','legacy']){
 await page.goto('http://localhost:8765/?view_plan_id=fixture&fixture='+scenario);await page.waitForSelector('.clarification-card');
 if(scenario==='dates'){
  await fillDate(0,'2026-10-02');await fillDate(1,'2026-10-01');
  await page.click('text="提交并继续"');await page.waitForSelector('.clarification-error');assert.equal(await page.evaluate(()=>window.__sent.length),0);
  await fillDate(1,'2026-10-03');
 }else if(scenario==='choice')await page.click('text="分到两天安排"');
 else if(scenario==='multi'){await page.click('text="江边散步"');await page.click('text="博物馆"');}
 else{assert.doesNotMatch(await page.evaluate(()=>document.querySelector('.clarification-card').textContent),/候选池|用户点名/);await page.fill('textarea[aria-label="填写补充信息"]','按原要求继续');}
 await page.evaluate(()=>{window.__fail=false});await page.click('text="提交并继续"');await page.waitForFunction(()=>!document.querySelector('.clarification-card'));
 const body=await page.evaluate(()=>window.__sent.at(-1));assert.equal(body.interaction_id,'answer-1');assert.equal(typeof body.value,'string');
 console.log({scenario,serializedAnswer:body.value});
}
await page.goto('http://localhost:8765/?view_plan_id=fixture&fixture=text');await page.waitForSelector('.clarification-card');
for(const width of [1440,1024,768,390]){
 await page.cdp('Emulation.setDeviceMetricsOverride',{width,height:894,deviceScaleFactor:1,mobile:width===390});
 if(width===768)await page.click('loc=role:button[name="对话"]');
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 const bounds=await page.evaluate(()=>{const r=document.querySelector('.clarification-card').getBoundingClientRect();return{x:r.x,y:r.y,right:r.right,bottom:r.bottom}});
 assert.ok(bounds.y>=0&&bounds.right<=width+1&&bounds.bottom<=894,JSON.stringify(bounds));
 if(width===390){await page.waitForFunction(()=>document.getAnimations().every(a=>a.effect?.getTiming().iterations===Infinity||a.playState!=='running'));console.log(await page.screenshot({path:'/tmp/clarification-mobile.png'}));}
 console.log({width,noOverflow:true,cardVisible:true});
}
await page.cdp('Emulation.setDeviceMetricsOverride',{width:390,height:450,deviceScaleFactor:1,mobile:true});
const button=await page.evaluate(()=>{const r=[...document.querySelectorAll('.clarification-card button')].find(b=>b.textContent.includes('提交并继续')).getBoundingClientRect();return{top:r.top,bottom:r.bottom}});
assert.ok(button.top>=0&&button.bottom<=450,JSON.stringify(button));console.log({smallKeyboardViewport:button});
await page.click('text="停止本次任务"');await page.waitForFunction(()=>!document.querySelector('.clarification-card'));
await page.cdp('Emulation.clearDeviceMetricsOverride',{});
await task.finish({keep:[]});console.log('PASS: clarification forms, retry, cancel, responsive and keyboard viewport');
