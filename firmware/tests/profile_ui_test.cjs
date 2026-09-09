const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync('firmware/src/web_ui.h','utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const elements=new Map(), canvas=new Proxy({}, {get:()=>()=>{}});
function el(id){if(!elements.has(id))elements.set(id,{value:115,style:{},classList:{add(){},remove(){},toggle(){}},parentElement:{clientWidth:400,clientHeight:400},getContext:()=>canvas,addEventListener(){},setPointerCapture(){},getBoundingClientRect:()=>({left:0,top:0}),appendChild(){}});return elements.get(id);}
let socket;class WS {static OPEN=1;constructor(){socket=this;this.readyState=1;this.sent=[];}send(m){this.sent.push(JSON.parse(m));}}
const context=vm.createContext({console,WebSocket:WS,location:{host:'robot'},document:{hidden:false,getElementById:el,createElement:el,addEventListener(){}},window:{addEventListener(){}},ResizeObserver:class{observe(){}},fetch:async()=>({ok:false,json:async()=>[]}),setInterval(){},setTimeout(){},JSON,Math});
(async()=>{
 vm.runInContext(source,context);socket.onopen();
 socket.onmessage({data:JSON.stringify({features:{status_leds:false,dfplayer:false,i2s_audio:true,print_first_profile:true,print_first_profile_status:'CANDIDATE_NOT_ADOPTED',print_first_profile_adopted:false},stand:false,vbat:7.4})});
 assert.equal(el('led').disabled,true);assert.equal(el('snd1').disabled,true);assert.equal(el('snd2').disabled,true);
 assert.match(el('hardwareStat').textContent,/LED未搭載.*SD効果音未搭載.*I2S会話対応/);
 assert.match(el('hardwareStat').textContent,/試作設定.*未採用.*CANDIDATE_NOT_ADOPTED/);
 vm.runInContext('pttDown()',context);assert.equal(socket.sent.at(-1).ptt,1);assert.equal(socket.sent.at(-1).stand,undefined);
 socket.onmessage({data:JSON.stringify({features:{status_leds:true,dfplayer:true,i2s_audio:true},stand:false,vbat:7.4})});
 assert.equal(el('led').disabled,false);assert.equal(el('snd1').disabled,false);
 await el('snd1').onclick();assert.match(el('hardwareStat').textContent,/再生できません/);
 console.log('PASS: UI explicitly identifies omitted hardware, restores standard controls, preserves PTT, reports failed track API');
})().catch(e=>{console.error(e);process.exitCode=1;});
