// 埋め込み UI をそのまま実行し、初期化・脱力・通信断・非表示時の停止を検証する。
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2] || 'firmware/src/web_ui.h', 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const elements = new Map();
const listeners = {};
const canvas = new Proxy({}, {get: () => () => {}});
function element(id) {
  if (!elements.has(id)) elements.set(id, {value: id === 'h' ? 115 : 0, style: {}, classList: {add(){},remove(){},toggle(){}}, clientWidth: 400, clientHeight: 400,
    parentElement: {clientWidth: 400, clientHeight: 400}, getContext: () => canvas,
    addEventListener(k, f){this[k] = f;}, setPointerCapture(){}, getBoundingClientRect: () => ({left: 0,top: 0}), appendChild(){}});
  return elements.get(id);
}
let socket;
class WebSocket {static OPEN=1;constructor(){socket=this;this.sent=[];this.readyState=1;} send(m){this.sent.push(JSON.parse(m));}}
const context = vm.createContext({console, WebSocket, location:{host:'robot'}, document:{hidden:false,getElementById:element,createElement:element,addEventListener(k,f){listeners[k]=f;}},window:{addEventListener(k,f){listeners[k]=f;}},ResizeObserver:class{observe(){}}, fetch:async()=>({json:async()=>[]}),setInterval(){},setTimeout(){},JSON,Math});
const config=fs.readFileSync('firmware/src/config.h','utf8');
const html=fs.readFileSync('firmware/src/web_ui.h','utf8');
assert.equal(+html.match(/id="h" min="([0-9]+)"/)[1],+config.match(/BODY_H_MIN = ([0-9.]+)f/)[1]);
vm.runInContext(source,context);
socket.onopen();
assert.equal(typeof element('rest').onclick,'function');
vm.runInContext('sendState()',context);
assert.equal(socket.sent.at(-1).stand,undefined,'Opening a page must not arm the robot');
assert.equal(socket.sent.at(-1).h,undefined,'Page reload must preserve the current body height');
socket.onmessage({data:JSON.stringify({h:125,stand:false,i2c:false,vbat:7.4})});
assert.equal(element('h').value,125);assert.match(element('stat').innerHTML,/サーボ通信異常/);
element('h').value=120;element('h').input();vm.runInContext('sendState()',context);
assert.equal(socket.sent.at(-1).h,120);
element('rest').onclick();
assert.equal(socket.sent.at(-1).stand,0);
vm.runInContext('sendState()',context);assert.equal(socket.sent.at(-1).stand,undefined);
element('stand').onclick();assert.equal(socket.sent.at(-1).stand,1);
vm.runInContext('sendState()',context);assert.equal(socket.sent.at(-1).stand,undefined);
vm.runInContext('vx=1;vy=-1;wz=.7;ptt=1',context);
socket.onclose(); socket.onopen();vm.runInContext('sendState()',context);
assert.deepEqual([socket.sent.at(-1).vx,socket.sent.at(-1).vy,socket.sent.at(-1).wz,socket.sent.at(-1).ptt],[0,0,0,0]);
vm.runInContext('vx=1;vy=1;wz=1;ptt=1;document.hidden=true',context);listeners.visibilitychange();
assert.deepEqual([socket.sent.at(-1).vx,socket.sent.at(-1).vy,socket.sent.at(-1).wz,socket.sent.at(-1).ptt],[0,0,0,0]);
console.log('PASS: UI initialization, rest, reconnect, background stop');
