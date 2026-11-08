/* P15 numerical model only. No CAD, firmware, hardware I/O, or measured validation. */
(function(root){
'use strict';
const Devices=typeof module!=='undefined'&&module.exports?require('./device-library.js'):root.P15Devices;
const D=Math.PI/180, clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const DEFAULTS={dt:.002,controlPeriod:.02,soc:.8,capacityAh:2.2,batteryR:.06,commonR:.04,legR:.025,armR:.06,logicR:.08,armV:5,armLimit:8,legMode:'direct',legSetV:6,legLimit:10,efficiency:.9,dropout:.3,logicLimit:3,logicPower:.9,logicThreshold:4.5,legStallA:3,armStallA:.85,loadScale:1,frequency:.6,fuseA:15,fuseI2t:120,fuseEnabled:false,lowVoltageStop:false,lowVoltageThreshold:6.4,lowVoltageDelay:3,scenario:'walk',stagger:0};
const SPECS={leg:{refV:7.4,stallNm:20*.0980665,speed:60*D/.16,idleA:.1,J:.004,kp:4,kd:.16,drag:.015},arm:{refV:4.8,stallNm:1.8*.0980665,speed:60*D/.10,idleA:.04,J:.00012,kp:.45,kd:.009,drag:.001}};
const CHANNEL_MAP=Array.from({length:12},(_,i)=>({index:i,board:0,channel:i})).concat([0,3,4,7].map((channel,i)=>({index:12+i,board:1,channel})));
const LABELS=['FR Yaw','FR Hip','FR Knee','FL Yaw','FL Hip','FL Knee','RL Yaw','RL Hip','RL Knee','RR Yaw','RR Hip','RR Knee','右肩','右爪','左肩','左爪'];
function ocv(soc){const points=[[0,6],[.1,6.8],[.5,7.6],[1,8.4]];soc=clamp(soc,0,1);for(let i=1;i<points.length;i++){const[a,x]=points[i-1],[b,y]=points[i];if(soc<=b)return x+(y-x)*(soc-a)/(b-a);}return 8.4;}
class Model{
 constructor(config={},deviceSet=Devices.DEFAULT){this.devices=Devices.resolve(deviceSet);this.specs={leg:{...SPECS.leg,...this.devices.specs.leg},arm:{...SPECS.arm,...this.devices.specs.arm}};this.c={...DEFAULTS,...this.devices.config,...config};this.validate();this.reset();}
 ocv(soc=this.soc){return Devices.ocv(soc,this.devices.parts.battery);}
 pulseToAngle(width,kind){const sp=this.specs[kind];return ((width-sp.pulseMin)/(sp.pulseMax-sp.pulseMin)-.5)*sp.span;}
 angleToPulse(angle,kind){const sp=this.specs[kind];return sp.pulseMin+(angle/sp.span+.5)*(sp.pulseMax-sp.pulseMin);}
 validate(){for(const[k,v]of Object.entries(this.c)){if(typeof v==='number'&&!Number.isFinite(v))throw Error('Invalid '+k);}if(!(this.c.dt>0&&this.c.dt<=.005))throw Error('dt must be (0,0.005]');for(const k of ['batteryR','commonR','legR','armR','logicR','loadScale','stagger'])if(this.c[k]<0)throw Error(k+' must be nonnegative');for(const k of ['capacityAh','legStallA','armStallA','armLimit','legLimit','logicLimit','fuseA','fuseI2t'])if(this.c[k]<=0)throw Error(k+' must be positive');if(this.c.efficiency<=0||this.c.efficiency>1)throw Error('efficiency');}
 reset(){this.t=0;this.controlMode='internal';this.sequence=-1;this.lastCommandAt=null;this.oe=[false,false];this.registers=Array.from({length:32},()=>({pulse_us:1500,full_off:true}));this.soc=this.c.soc;this.wh=0;this.ah=0;this.fuseHeat=0;this.fuseOpen=false;this.lowTimer=0;this.lowLatched=false;this.mainOn=true;this.jammed=-1;this.i2cLost=false;this.controlNext=0;this.mcu=true;this.pcaPowered=true;this.events=[];this.servos=LABELS.map((label,i)=>({label,index:i,kind:i<12?'leg':'arm',q:0,w:0,target:0,command:0,current:0,tau:0,load:0,limit:0,pulse:1500,saturated:false,blocked:false,off:false}));this.power={bus:this.ocv(this.soc),leg:0,arm:0,logic:0,ib:0,ileg:0,iarm:0,ilogic:0,residual:0,loss:0};this.lastFlags='';this.power=this.solve(true);for(const servo of this.servos)Object.assign(servo,this.actuator(servo,servo.kind==='leg'?this.power.leg:this.power.arm,true));return this;}
 setConfig(patch){this.c={...this.c,...patch};this.validate();}
 event(text){this.events.push({t:this.t,text});if(this.events.length>80)this.events.shift();}
 toggleMain(){this.mainOn=!this.mainOn;this.event(this.mainOn?'仮想主電源 ON':'仮想主電源 OFF');}
 resetProtection(){this.fuseOpen=false;this.fuseHeat=0;this.lowLatched=false;this.lowTimer=0;this.event('仮想保護ラッチ解除');}
 externalMode(){this.controlMode='external';this.registers.forEach(r=>r.full_off=true);this.servos.forEach(s=>s.off=true);this.event('外部制御モード：全PWM OFFから開始');this.power=this.solve(true);for(const s of this.servos)Object.assign(s,this.actuator(s,s.kind==='leg'?this.power.leg:this.power.arm,true));return this;}
 acceptCommand(frame){
  if(!frame||frame.version!=='p15.control.v1')throw Error('SCHEMA: version must be p15.control.v1');
  if(!Number.isSafeInteger(frame.sequence)||frame.sequence<0||frame.sequence<=this.sequence)throw Error('SEQUENCE: strictly increasing nonnegative integer required');
  if(!Array.isArray(frame.commands)||frame.commands.length>32)throw Error('SCHEMA: commands array max32');
  const staged=[],used=new Set();
  for(const c of frame.commands){
   if(!c||typeof c!=='object')throw Error('SCHEMA: command object required');
   if(!Number.isInteger(c.board)||c.board<0||c.board>1||!Number.isInteger(c.channel)||c.channel<0||c.channel>15)throw Error('ADDRESS: board0..1/channel0..15');
   const id=c.board*16+c.channel;if(used.has(id))throw Error('DUPLICATE: channel repeated');used.add(id);
   if(c.full_off!==undefined&&typeof c.full_off!=='boolean')throw Error('SCHEMA: full_off boolean');
   if(c.full_off!==true&&(!Number.isFinite(c.pulse_us)||c.pulse_us<500||c.pulse_us>2500))throw Error('RANGE: pulse_us500..2500');
   staged.push([id,{pulse_us:c.full_off?1500:c.pulse_us,full_off:c.full_off===true}]);
  }
  if(frame.oe!==undefined&&(!Array.isArray(frame.oe)||frame.oe.length!==2||frame.oe.some(v=>typeof v!=='boolean')))throw Error('SCHEMA: oe must be2booleans;true=output disabled');
  if(this.i2cLost)throw Error('BUS_DISCONNECTED: command not applied');
  if(!this.mcu||!this.pcaPowered||!this.mainOn||this.fuseOpen)throw Error('MCU_UNAVAILABLE: command not applied');
  if(this.controlMode!=='external')this.externalMode();
  for(const[id,r]of staged)this.registers[id]=r;
  if(frame.oe!==undefined)this.oe=[...frame.oe];
  this.sequence=frame.sequence;this.lastCommandAt=this.t;
  return {version:'p15.ack.v1',accepted:true,sequence:this.sequence,sim_time_s:this.t,applied_registers:staged.length,output_update:'next20ms control tick'};
 }
 command(i,t){const c=this.c,phase=[0,Math.PI,0,Math.PI][Math.floor(i/3)]||0;let a=0;
  if(c.scenario==='step')a=t>=1+(c.stagger*i)?40*D:0;
  else if(c.scenario==='hold')a=(i<12?20:(i%2?20:0))*D;
  else if(c.scenario==='claw')a=i>=12&&i%2?25*D*(1-Math.cos(2*Math.PI*c.frequency*t)):0;
  else {if(i<12){const j=i%3;a=[8,18,25][j]*D*Math.sin(2*Math.PI*c.frequency*t+phase+(j===2?Math.PI/2:0));}else a=i%2?25*D*(1-Math.cos(2*Math.PI*c.frequency*t)):10*D*Math.sin(2*Math.PI*c.frequency*.5*t);}
  return clamp(a,-70*D,70*D);
 }
 load(s){const k=s.index;if(k<12){const j=k%3;return this.c.loadScale*([.035,.35,.45][j]*Math.cos(s.q));}if(k%2===0)return this.c.loadScale*.012*Math.cos(s.q);return this.c.loadScale*(.012+.045*clamp(s.q/(50*D),0,1));}
 actuator(s,V,enabled){const sp=this.specs[s.kind],ratio=Math.max(0,V)/sp.refV,stall=sp.stallNm*ratio,omega0=sp.speed*ratio,load=this.load(s);const idle=sp.idleA*Math.min(1,ratio);
  const address=CHANNEL_MAP[s.index];if(!enabled||s.off||this.oe[address.board]||V<.001)return {tau:0,current:idle,limit:stall,load,saturated:false};
  const err=s.command-s.q,deadband=sp.deadband_us*sp.span/(sp.pulseMax-sp.pulseMin)*D;const demand=sp.kp*(Math.abs(err)<=deadband?0:err)-sp.kd*s.w;
  const limit=stall*Math.max(0,1-Math.max(0,Math.sign(demand)*s.w)/Math.max(.001,omega0));
  const tau=clamp(demand,-limit,limit);
  const istall=(s.kind==='leg'?this.c.legStallA:this.c.armStallA)*ratio;
  const current=idle+(Math.max(idle,istall)-idle)*Math.abs(tau)/Math.max(stall,1e-9);
  return {tau,current,limit,load,saturated:Math.abs(demand)>limit+1e-6};
 }
 branch(indices,Vin,target,R,limit,enabled,dropout=this.c.dropout){const c=this.c;
  const ceiling=target===null?Math.max(0,Vin):Math.max(0,Math.min(target,Vin-dropout));
  const sum=v=>indices.reduce((a,i)=>a+this.actuator(this.servos[i],v,enabled).current,0);
  let lo=0,hi=ceiling;
  for(let k=0;k<22;k++){const v=(lo+hi)/2,I=sum(v);if(v+R*I<=ceiling&&I<=limit)lo=v;else hi=v;}
  const v=(lo+hi)/2,I=sum(v),sourceV=v+R*I;
  return {v,I,sourceV,limited:I>=limit*.999&&ceiling>0,power:sourceV*I};
 }
 evalPower(bus,enabled){const c=this.c;const leg=this.branch([0,1,2,3,4,5,6,7,8,9,10,11],bus,c.legMode==='direct'?null:c.legSetV,c.legR,c.legMode==='direct'?Infinity:c.legLimit,enabled);
  const ar=this.devices.parts.armRegulator.model,lr=this.devices.parts.logicRegulator.model;const arm=this.branch([12,13,14,15],bus,c.armV,c.armR,c.armLimit,enabled,ar.dropout_V);
  // Logic load is a resistor-equivalent around nominal 5V, with a declared Wi-Fi load pulse.
  const cm=this.devices.parts.controller.model,cs=this.devices.parts.controller.spec;const wifi=(this.t%cm.radioPeriod_s)<cm.radioDuty*cm.radioPeriod_s?(cs.rfTx80211b_A+cm.boardAux_A)/(cs.rfRx_A+cm.boardAux_A):1,conductance=c.logicPower*wifi/(lr.output_V**2);
  let logic=Math.max(0,Math.min(lr.output_V,bus-lr.dropout_V))/(1+c.logicR*conductance),ilogic=Math.min(c.logicLimit,logic*conductance);logic=Math.min(logic,c.logicLimit/Math.max(conductance,1e-9));
  const logicP=(logic+c.logicR*ilogic)*ilogic;
  const legInput=c.legMode==='direct'?leg.I:(bus>.0001?leg.power/(bus*c.efficiency):0);
  const iq=bus>.01?ar.quiescent_A+lr.quiescent_A:0,armInput=bus>.0001?arm.power/(bus*ar.efficiency):0,logicInput=bus>.0001?logicP/(bus*lr.efficiency):0;
  const ib=legInput+armInput+logicInput+iq;
  return {bus,leg:leg.v,arm:arm.v,logic,ib,ileg:leg.I,iarm:arm.I,ilogic,armLimited:arm.limited,legLimited:leg.limited,legPower:leg.v*leg.I,armPower:arm.v*arm.I,logicPower:logic*ilogic,loss:Math.max(0,bus*ib-(leg.v*leg.I+arm.v*arm.I+logic*ilogic))};
 }
 solve(enabled){const c=this.c,V=this.ocv(this.soc),R=c.batteryR+c.commonR;if(!this.mainOn||this.fuseOpen||this.soc<=0)return {bus:0,leg:0,arm:0,logic:0,ib:0,ileg:0,iarm:0,ilogic:0,loss:0,residual:0,armLimited:false,legLimited:false};
  if(R===0){const p=this.evalPower(V,enabled);p.residual=0;return p;}
  const f=v=>v+R*this.evalPower(v,enabled).ib-V;
  // Search from high voltage for the highest root, then bisect. No ideal constant-voltage source under overload.
  let hi=V,lo=0;for(let k=1;k<=24;k++){const v=V*(1-k/24);if(f(v)<=0){lo=v;break;}hi=v;}
  for(let k=0;k<28;k++){const mid=(lo+hi)/2;if(f(mid)>0)hi=mid;else lo=mid;}
  const p=this.evalPower((lo+hi)/2,enabled);p.residual=p.bus+R*p.ib-V;return p;
 }
 step(dt=this.c.dt){const c=this.c;if(!(dt>0&&dt<=.005))throw Error('step limit');
  // PCA register holds its previous target on I2C loss or MCU reset; MCU failure is not an independent power cut.
  if(this.t+1e-9>=this.controlNext){this.controlNext+=c.controlPeriod;for(const s of this.servos){
   let width;
   if(this.controlMode==='external'){
    const a=CHANNEL_MAP[s.index],r=this.registers[a.board*16+a.channel];s.off=r.full_off;
    width=r.pulse_us;s.target=this.pulseToAngle(width,s.kind)*D;
    // Register values remain on I2C loss; only new writes are rejected.
    if(this.lowLatched)continue;
   }else{
    s.off=false;s.target=this.command(s.index,this.t);width=this.angleToPulse(s.target/D,s.kind);
    if(!this.mcu||this.i2cLost||this.lowLatched)continue;
   }
   const ticks=Math.round(width/this.devices.pwm.tick_us);s.pulse=ticks*this.devices.pwm.tick_us;s.command=clamp(this.pulseToAngle(s.pulse,s.kind)*D,-70*D,70*D);
  }}
  let enabled=this.mainOn&&!this.fuseOpen&&!this.lowLatched&&this.pcaPowered;
  const p=this.solve(enabled);this.power=p;
  const oldMcu=this.mcu;this.mcu=p.logic>=(this.mcu?c.logicThreshold:c.logicThreshold+this.devices.parts.controller.model.recoverMargin_V);
  if(oldMcu!==this.mcu)this.event(this.mcu?'MCU電源回復（仮閾値）':'MCU低電圧：PCA指令保持を仮定');
  const wasPcaPowered=this.pcaPowered;const pcaLogic=Math.min(3.3,Math.max(0,p.logic-this.devices.parts.controller.model.ldoDropout_V));this.pcaPowered=pcaLogic>=this.devices.parts.pwm.spec.logicMin_V&&pcaLogic<=this.devices.parts.pwm.spec.logicMax_V;
  if(wasPcaPowered&&!this.pcaPowered&&this.controlMode==='external'){this.registers.forEach(r=>r.full_off=true);this.servos.forEach(s=>s.off=true);this.event('PCA論理電源喪失：PORに備えPWMレジスター解除');}
  if(c.fuseEnabled&&!this.fuseOpen){this.fuseHeat=Math.max(0,this.fuseHeat+Math.max(0,p.ib*p.ib-c.fuseA*c.fuseA)*dt-this.fuseHeat*dt/20);if(this.fuseHeat>=c.fuseI2t){this.fuseOpen=true;this.event('仮想I²t遮断：実ヒューズ特性ではない');}}
  if(c.lowVoltageStop&&!this.lowLatched){this.lowTimer=p.bus<c.lowVoltageThreshold&&this.mainOn?this.lowTimer+dt:0;if(this.lowTimer>=c.lowVoltageDelay&&this.mcu&&!this.i2cLost){this.lowLatched=true;this.event('ソフト低電圧停止ラッチ（模擬）');}}
  for(const s of this.servos){const V=s.kind==='leg'?p.leg:p.arm;const a=this.actuator(s,V,enabled);Object.assign(s,a);s.blocked=s.index===this.jammed;
   if(s.blocked){s.w=0;continue;}
   const sp=this.specs[s.kind],acc=(a.tau-a.load-sp.drag*s.w)/sp.J;s.w+=acc*dt;s.q+=s.w*dt;
   const bound=80*D;if(s.q>bound){s.q=bound;s.w=Math.min(0,s.w);}if(s.q< -bound){s.q=-bound;s.w=Math.max(0,s.w);}
  }
  this.ah+=p.ib*dt/3600;this.soc=clamp(this.soc-p.ib*dt/(3600*c.capacityAh),0,1);this.wh+=this.ocv(this.soc)*p.ib*dt/3600;this.t+=dt;
  if(![p.bus,p.ib,...this.servos.map(s=>s.q)].every(Number.isFinite))throw Error('Numerical divergence');return this.snapshot();
 }
 snapshot(){const p=this.power;const flags=[];if(!this.mainOn)flags.push('主電源OFF');if(this.fuseOpen)flags.push('仮想ヒューズ遮断');if(this.lowLatched)flags.push('低電圧停止');if(this.i2cLost)flags.push('I²C断・指令保持');if(Math.abs(p.residual)>.01)flags.push('電源解の残差超過');if(!this.mcu)flags.push('MCU低電圧');if(p.armLimited)flags.push('腕電源の電流制限');if(p.legLimited)flags.push('脚電源の電流制限');if(this.mainOn&&!this.fuseOpen&&(p.leg<this.devices.parts.legServo.spec.voltageMin_V||p.leg>this.devices.parts.legServo.spec.voltageMax_V))flags.push('脚サーボ仕様電圧外：外挿');if(this.mainOn&&!this.fuseOpen&&p.arm<4.8)flags.push('MG90S基準電圧未満：外挿');
  for(const[slot,voltage]of [['armRegulator',p.bus],['logicRegulator',p.bus]]){const d=this.devices.parts[slot];if(d.spec.inputMin_V==null||d.spec.inputMax_V==null)flags.push(d.name+'：入力保証範囲不明');else if(this.mainOn&&(voltage<d.spec.inputMin_V||voltage>d.spec.inputMax_V))flags.push(d.name+'：入力仕様外・応答未確定');}
  if(this.devices.parts.armServo.spec.voltageMax_V==null)flags.push('腕サーボ上限電圧・購入個体未確定');
  return {pwm:this.devices.pwm,deviceSelection:this.devices.set.selection,physicalValidation:'NOT_RUN',controlMode:this.controlMode,lastSequence:this.sequence,commandAge_s:this.lastCommandAt===null?null:this.t-this.lastCommandAt,oe:[...this.oe],t:this.t,soc:this.soc,ocv:this.ocv(this.soc),...p,batteryLoss:this.power.ib**2*this.c.batteryR,wireLoss:this.power.ib**2*this.c.commonR,wh:this.wh,ah:this.ah,fuseHeat:this.fuseHeat,fuseFraction:this.fuseHeat/this.c.fuseI2t,mcu:this.mcu,flags,servos:this.servos.map(s=>({label:s.label,q:s.q/D,w:s.w/D,target:s.target/D,command:s.command/D,tau:s.tau,load:s.load,current:s.current,saturated:s.saturated,pulse:s.pulse,period_us:this.devices.pwm.period_us,blocked:s.blocked,outputOff:s.off||this.oe[CHANNEL_MAP[s.index].board]||!this.mainOn||this.fuseOpen||!this.pcaPowered||this.lowLatched,commandLimited:Math.abs(s.target-s.command)>1*D,board:CHANNEL_MAP[s.index].board,channel:CHANNEL_MAP[s.index].channel}))};
 }
}
function run(config={},seconds=8,hook=null){const m=new Model(config),trace=[];let next=0;for(let i=0;i<Math.round(seconds/m.c.dt);i++){if(hook)hook(m);const v=m.step();if(m.t>=next){trace.push(v);next+=.02;}}return {model:m,trace};}
const api={Model,run,DEFAULTS,SPECS,LABELS,CHANNEL_MAP,ocv};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.PowerMotion=api;
})(typeof globalThis!=='undefined'?globalThis:this);
