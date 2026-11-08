// Local, numerical-only control bridge. Never opens a serial port or physical I2C.
'use strict';
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const E=require('./engine.js'),Devices=require('./device-library.js');
function createServer(){
 let model=new E.Model().externalMode(),running=false,timer=null;
 const maxBody=65536;
 function state(){return {version:'p15.telemetry.v1',clock:{mode:running?'realtime':'lockstep',running,step_s:model.c.dt},devices:{set:model.devices.set,parts:model.devices.parts,unknown:model.devices.unknown,pwm:model.devices.pwm},config:model.c,channel_map:E.CHANNEL_MAP,simulation:model.snapshot(),faults:{mainOn:model.mainOn,i2cLost:model.i2cLost,jammed:model.jammed},events:model.events};}
 function stop(){running=false;if(timer)clearInterval(timer);timer=null;}
 function advance(seconds){if(!Number.isFinite(seconds)||seconds<=0||seconds>1)throw Error('SCHEMA: step_s must be (0,1]');const n=Math.round(seconds/model.c.dt);if(Math.abs(n*model.c.dt-seconds)>1e-9)throw Error('SCHEMA: step_s must be an integer multiple of dt');for(let i=0;i<n;i++)model.step();}
 function config(input,deviceSet=Devices.DEFAULT){if(!input||typeof input!=='object'||Array.isArray(input))throw Error('SCHEMA: config object');for(const[k,v]of Object.entries(input)){if(!(k in E.DEFAULTS)||typeof v!==typeof E.DEFAULTS[k])throw Error('SCHEMA: config key/type '+k);if(typeof v==='number'&&!Number.isFinite(v))throw Error('SCHEMA: finite number '+k);}
  const c={...E.DEFAULTS,...input};
  // Fixed bus and integration clocks in v1; reproducible controller boundary.
  if(c.dt!==.002||c.controlPeriod!==.02)throw Error('SCHEMA: v1 dt=0.002/controlPeriod=0.02');
  if(c.soc<0||c.soc>1||c.frequency<=0||c.armV<=0||c.legSetV<=0||c.dropout<0||c.logicPower<0||c.logicThreshold<=0||c.lowVoltageDelay<0||c.lowVoltageThreshold<=0)throw Error('SCHEMA: config physical range');
  if(!['direct','regulated'].includes(c.legMode)||!['walk','hold','step','claw'].includes(c.scenario))throw Error('SCHEMA: config enum');
  return new E.Model(input,deviceSet).externalMode();
 }
 const server=http.createServer(async(req,res)=>{
  const reply=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
  try{
   const expected='127.0.0.1:'+server.address().port;
   if(![expected,'localhost:'+server.address().port].includes(req.headers.host))return reply(403,{error:'HOST'});
   const url=new URL(req.url,'http://'+expected),pathname=url.pathname;
   if(pathname.startsWith('/api/')){
    if(req.method==='GET'&&pathname==='/api/devices')return reply(200,{version:'p15.devices.v1',catalog:Devices.CATALOG,active:model.devices.set});
    if(req.method==='GET'&&pathname==='/api/state')return reply(200,state());
    if(req.method!=='POST')return reply(405,{error:'METHOD'});
    if(req.headers.origin&&!['http://'+expected,'http://localhost:'+server.address().port].includes(req.headers.origin))return reply(403,{error:'ORIGIN'});
    if(!(req.headers['content-type']||'').startsWith('application/json'))return reply(415,{error:'CONTENT_TYPE: application/json required'});
    let raw='',size=0;for await(const chunk of req){size+=chunk.length;if(size>maxBody)return reply(413,{error:'BODY_TOO_LARGE'});raw+=chunk;}
    const body=JSON.parse(raw||'{}');if(!body||typeof body!=='object'||Array.isArray(body))throw Error('SCHEMA: object required');
    if(pathname==='/api/command')return reply(200,model.acceptCommand(body));
    if(pathname==='/api/step'){if(running)throw Error('CLOCK_BUSY: pause realtime before stepping');advance(body.step_s);return reply(200,state());}
    if(pathname==='/api/reset'){const candidate=config(body.config||{},body.devices||model.devices.set);stop();model=candidate;return reply(200,state());}
    if(pathname==='/api/devices'){if(running)throw Error('CLOCK_BUSY: pause before device replacement');const candidate=config(body.config||{},body.devices);model=candidate;return reply(200,state());}
    if(pathname==='/api/run'){
     if(typeof body.running!=='boolean')throw Error('SCHEMA: running boolean');stop();
     if(body.running){running=true;timer=setInterval(()=>advance(.02),20);timer.unref();}
     return reply(200,state());
    }
    if(pathname==='/api/fault'){
     for(const[k,v]of Object.entries(body)){if(k==='jammed'){if(!Number.isInteger(v)||v< -1||v>15)throw Error('SCHEMA: jammed -1..15');}else if(!['mainOn','i2cLost'].includes(k)||typeof v!=='boolean')throw Error('SCHEMA: fault key/type');}
     for(const[k,v]of Object.entries(body))model[k]=v;
     model.event('外部から故障/電源条件を更新');return reply(200,state());
    }
    return reply(404,{error:'NOT_FOUND'});
   }
   if(req.method!=='GET')return reply(405,{error:'METHOD'});
   const name=pathname==='/'?'monitor.html':decodeURIComponent(pathname.slice(1));
   const file=path.resolve(__dirname,name);if(!file.startsWith(__dirname+path.sep))return reply(403,{error:'PATH'});
   if(!fs.existsSync(file)||!fs.statSync(file).isFile())return reply(404,{error:'NOT_FOUND'});
   const mime={'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.png':'image/png','.csv':'text/csv','.md':'text/plain'};
   res.writeHead(200,{'Content-Type':(mime[path.extname(file)]||'application/octet-stream')+'; charset=utf-8','Cache-Control':'no-store'});fs.createReadStream(file).pipe(res);
  }catch(err){reply(/BUS_DISCONNECTED|MCU_UNAVAILABLE|SEQUENCE|CLOCK_BUSY/.test(err.message)?409:400,{version:'p15.error.v1',error:err.message});}
 });
 server.on('close',stop);return server;
}
module.exports={createServer};
if(require.main===module){const port=Number(process.env.P15_PORT||8796);createServer().listen(port,'127.0.0.1',()=>console.log('P15 control monitor: http://127.0.0.1:'+port+'/'));}
