#pragma once
// スマホ用操作画面 (PROGMEM 埋め込み)。
// 左: 移動ジョイスティック / 右: 旋回スライダ / 上: 状態表示
// 下: 体高・LED・サウンド・トリム

const char INDEX_HTML[] PROGMEM = R"HTML(<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>Tachikoma</title>
<style>
:root{--bg:#10141c;--panel:#1b2230;--acc:#4d7fff;--txt:#dce6f5;--red:#e2483d}
*{margin:0;padding:0;box-sizing:border-box;touch-action:none;user-select:none;-webkit-user-select:none}
body{background:var(--bg);color:var(--txt);font-family:system-ui,sans-serif;height:100dvh;
display:flex;flex-direction:column;overflow:hidden}
header{display:flex;justify-content:space-between;align-items:center;padding:8px 14px;
background:var(--panel)}
header b{color:var(--acc);letter-spacing:.12em}
#stat{font-size:.8rem;opacity:.85}
#stat .warn{color:var(--red);font-weight:700}
main{flex:1;display:flex;gap:10px;padding:10px}
#joyWrap,#turnWrap{flex:1;display:flex;align-items:center;justify-content:center}
canvas{background:var(--panel);border-radius:18px}
footer{padding:8px 12px;background:var(--panel);display:flex;flex-direction:column;gap:8px}
.row{display:flex;gap:8px;align-items:center}
.row label{font-size:.75rem;width:3.2em;opacity:.8}
input[type=range]{flex:1;accent-color:var(--acc)}
button{background:#2a3550;color:var(--txt);border:0;border-radius:10px;padding:9px 12px;
font-size:.85rem;flex:1}
button.on{background:var(--acc)}
button.rec.on{background:var(--red)}
#trimPanel{display:none;max-height:38dvh;overflow-y:auto;touch-action:pan-y}
#trimPanel .row label{width:5em}
#voicePanel .row label{width:5em}
input[type=text],input[type=password]{flex:1;background:#111726;color:var(--txt);
border:1px solid #2a3550;border-radius:8px;padding:7px 8px;font-size:.85rem}
</style></head><body>
<header><b>TACHIKOMA</b><span id="stat">connecting…</span></header>
<main>
 <div id="joyWrap"><canvas id="joy"></canvas></div>
 <div id="turnWrap"><canvas id="turn"></canvas></div>
</main>
<footer>
 <div class="row"><label>体高</label>
  <input type="range" id="h" min="105" max="130" value="115"></div>
 <div class="row">
  <button id="stand" class="on">起動</button>
  <button id="rest">脱力</button>
  <button id="led" class="on">LED</button>
  <button id="eyeBtn" class="on">目:キョロ</button>
  <button id="snd1">♪1</button><button id="snd2">♪2</button>
  <button id="armBtn">アーム</button>
  <button id="voiceBtn">ボイス</button>
  <button id="trimBtn">Trim</button>
 </div>
 <div id="armPanel" style="display:none;flex-direction:column;gap:6px">
  <div class="row"><label>肩ヨー</label>
   <input type="range" id="ay" min="-15" max="15" value="0"></div>
  <div class="row"><label>肩上下</label>
   <input type="range" id="ap" min="-45" max="85" value="55"></div>
  <div class="row"><label>肘</label>
   <input type="range" id="ae" min="0" max="95" value="95"></div>
  <div class="row">
   <button onclick="armPose('tuck')">収納</button>
   <button onclick="armPose('ready')">構え</button>
   <button onclick="armPose('reach')">前へ</button>
   <button onclick="armPose('wave')">バイバイ</button>
  </div>
 </div>
 <div id="voicePanel" style="display:none;flex-direction:column;gap:6px">
  <button id="pttBtn" class="rec">話す (押し続け)</button>
  <div class="row"><label>状態</label><span id="voiceStat">-</span></div>
  <div class="row"><label>SSID</label>
   <input type="text" id="staSsid" placeholder="iPhoneテザリングのSSID" autocomplete="off"></div>
  <div class="row"><label>パスワード</label>
   <input type="password" id="staPass" autocomplete="off"></div>
  <div class="row"><button id="staSave">WiFi保存/接続</button></div>
 </div>
 <div id="trimPanel"></div>
</footer>
<script>
"use strict";
let ws, wsOK=false, vx=0, vy=0, wz=0, standing=true, led=true, ptt=0;
const stat=document.getElementById('stat');
function connect(){
  ws=new WebSocket('ws://'+location.host+'/ws');
  ws.onopen=()=>{wsOK=true;stat.textContent='online'};
  ws.onclose=()=>{wsOK=false;stat.textContent='reconnecting…';setTimeout(connect,800)};
  ws.onmessage=e=>{
    const d=JSON.parse(e.data);
    stat.innerHTML=(d.vbat>0?d.vbat.toFixed(2)+'V ':'')+
      (d.cut?'<span class="warn">低電圧遮断</span>':
       d.low?'<span class="warn">LOW BATT</span>':'online');
  };
}
connect();
// 腕キーは「ユーザーがスライダを操作した時だけ」送る。毎ティック送ると
// /arm プリセットで書いた target を 100ms で上書きしてしまう
let armDirty=false;
for(const id of ['ay','ap','ae'])
  document.getElementById(id).addEventListener('input',()=>{armDirty=true;});
// 操作系メッセージの送出。100ms 周期の他、PTT ボタンの押下/解放時にも即時
// 呼ぶ (録音開始/停止のレイテンシを抑えるため周期を待たない)
function sendState(){ if(!wsOK) return;
  const msg={vx,vy,wz, h:+document.getElementById('h').value,
    stand:standing?1:0, led:led?1:0, amir:1, ptt};
  if(armDirty){
    msg.ay=+document.getElementById('ay').value;
    msg.ap=+document.getElementById('ap').value;
    msg.ae=+document.getElementById('ae').value;
    armDirty=false;
  }
  ws.send(JSON.stringify(msg));
}
setInterval(sendState,100);

function armPose(p){
  fetch('/arm?pose='+p);
  // スライダ表示をプリセット実値 (config.h の ARM_POSE_*) に同期する。
  // armDirty は立てない (target はファーム側が既に設定済み)
  const v={tuck:[0,55,95],ready:[10,30,40],reach:[0,10,10]}[p];
  if(v){b('ay').value=v[0];b('ap').value=v[1];b('ae').value=v[2];}
}
// 目モード (押すたびに キョロキョロ→正面→スキャン を巡回)
const eyeModes=['kyoro','front','scan'];
const eyeNames={kyoro:'目:キョロ',front:'目:正面',scan:'目:スキャン'};
let eyeIdx=0;
b('eyeBtn').onclick=()=>{
  eyeIdx=(eyeIdx+1)%eyeModes.length;
  const m=eyeModes[eyeIdx];
  fetch('/eye?mode='+m);
  b('eyeBtn').textContent=eyeNames[m];
  b('eyeBtn').classList.toggle('on',m!=='front');
};

function setupPad(id, cb, spring){
  const cv=document.getElementById(id), ctx=cv.getContext('2d');
  function size(){const w=cv.parentElement.clientWidth-8,
    h=cv.parentElement.clientHeight-8, s=Math.min(w,h);
    cv.width=cv.height=s; draw(0,0);}
  let px=0,py=0;
  function draw(x,y){const s=cv.width,c=s/2,r=s*.36;
    ctx.clearRect(0,0,s,s);
    ctx.strokeStyle='#33415e';ctx.lineWidth=2;
    ctx.beginPath();ctx.arc(c,c,r,0,7);ctx.stroke();
    ctx.fillStyle='#4d7fff';
    ctx.beginPath();ctx.arc(c+x*r,c+y*r,s*.13,0,7);ctx.fill();}
  function pt(e){const t=e.touches?e.touches[0]:e,b=cv.getBoundingClientRect(),
    c=cv.width/2,r=cv.width*.36;
    let x=(t.clientX-b.left-c)/r, y=(t.clientY-b.top-c)/r;
    const m=Math.hypot(x,y); if(m>1){x/=m;y/=m}
    px=x;py=y;draw(x,y);cb(x,y);}
  function end(){px=py=0;draw(0,0);if(spring)cb(0,0);}
  cv.addEventListener('pointerdown',e=>{cv.setPointerCapture(e.pointerId);pt(e)});
  cv.addEventListener('pointermove',e=>{if(e.buttons)pt(e)});
  cv.addEventListener('pointerup',end);cv.addEventListener('pointercancel',end);
  new ResizeObserver(size).observe(cv.parentElement); size();
}
setupPad('joy',(x,y)=>{vx=x; vy=-y;},true);
setupPad('turn',(x,y)=>{wz=x;},true);

const b=id=>document.getElementById(id);
b('stand').onclick=()=>{standing=true;b('stand').classList.add('on');
  b('rest').classList.remove('on');};
b('rest').onclick=()=>{standing=false;b('rest').classList.add('on');
  b('stand').classList.remove('on');};
b('led').onclick=()=>{led=!led;b('led').classList.toggle('on',led);};
b('snd1').onclick=()=>fetch('/play?n=1');
b('snd2').onclick=()=>fetch('/play?n=2');
b('armBtn').onclick=()=>{const p=b('armPanel');
  const show=p.style.display!=='flex';
  p.style.display=show?'flex':'none';
  b('armBtn').classList.toggle('on',show);};

// ---- ボイス (PTT + STA WiFi 設定)
async function refreshWifi(){
  try{
    const r=await fetch('/wifi'); const d=await r.json();
    b('voiceStat').textContent=d.connected?('接続中 '+d.ip):
      (d.ssid?('未接続: '+d.ssid):'STA未設定');
    if(d.ssid) b('staSsid').value=d.ssid;
  }catch(e){ b('voiceStat').textContent='?'; }
}
b('voiceBtn').onclick=()=>{const p=b('voicePanel');
  const show=p.style.display!=='flex';
  p.style.display=show?'flex':'none';
  b('voiceBtn').classList.toggle('on',show);
  if(show) refreshWifi();};
function pttDown(){ptt=1;b('pttBtn').classList.add('on');sendState();}
function pttUp(){ptt=0;b('pttBtn').classList.remove('on');sendState();}
// setPointerCapture でボタンに指を捕捉する。捕捉後は指がボタン外へ
// ずれても pointerup/pointercancel はこの要素へ届くため、pointerleave
// による誤った途中終了 (半二重のため録音の再開には押し直しが必要) を防ぐ
b('pttBtn').addEventListener('pointerdown',e=>{
  b('pttBtn').setPointerCapture(e.pointerId);pttDown();});
b('pttBtn').addEventListener('pointerup',pttUp);
b('pttBtn').addEventListener('pointercancel',pttUp);
b('staSave').onclick=async()=>{
  const ssid=b('staSsid').value, pass=b('staPass').value;
  await fetch('/wifi',{method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:'ssid='+encodeURIComponent(ssid)+'&pass='+encodeURIComponent(pass)});
  b('staPass').value='';
  b('voiceStat').textContent='接続試行中…';
  setTimeout(refreshWifi,2000);
};

// ---- Trim UI
const tp=b('trimPanel');
b('trimBtn').onclick=async()=>{
  if(tp.style.display==='block'){tp.style.display='none';return;}
  const r=await fetch('/trim'); const t=await r.json();
  tp.innerHTML='';
  const names=['FR yaw','FR pit','FR knee','FL yaw','FL pit','FL knee',
   'RL yaw','RL pit','RL knee','RR yaw','RR pit','RR knee','Head','','','',
   'R肩yaw','R肩pit','R肘','R指(未使用)','L肩yaw','L肩pit','L肘','L指(未使用)',
   '右目','中目(未使用)','左目'];
  // ch25 (中目) は 2026-07-28 以降固定カメラ目でサーボが無いためスキップ。
  // ch19/23 (旧グリップ) も 2026-07-29 固定爪化でサーボが無いためスキップ
  // (config.h ARM_CH は 16-18/20-22 のみ登録、19/23 は永久に enabled_=false)
  t.forEach((v,i)=>{ if(i>26||(i>12&&i<16)||i===19||i===23||i===25)return;
    const row=document.createElement('div');row.className='row';
    row.innerHTML=`<label>${names[i]}</label>
     <input type="range" min="-200" max="200" value="${v}"
      oninput="trimSend(${i},this.value)">`;
    tp.appendChild(row);});
  tp.style.display='block';
};
// トリム送信は 150ms スロットル (NVS 保存は firmware 側で静穏後にまとめて実施)
const trimPend={};let trimTimer=null;
function trimSend(ch,us){
  trimPend[ch]=us;
  if(trimTimer)return;
  trimTimer=setTimeout(()=>{trimTimer=null;
    for(const c in trimPend){fetch('/trim?ch='+c+'&us='+trimPend[c]);delete trimPend[c];}
  },150);
}
</script></body></html>)HTML";
