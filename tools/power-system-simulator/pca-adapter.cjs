// Transport-neutral subset of a PCA9685 command API. No physical bus I/O.
'use strict';
class PCAAdapter{
 constructor(send,{oscillator_Hz=25000000,prescale=121}={}){this.tick_us=(prescale+1)/oscillator_Hz*1e6;this.send=send;this.sequence=0;this.pending=new Map();this.oe=[false,false];}
 setPulse(board,channel,pulse_us){this.pending.set(board+':'+channel,{board,channel,pulse_us});return this;}
 setPWM(board,channel,on,off){
  if(!Number.isInteger(on)||!Number.isInteger(off)||on<0||on>4095||off<0||off>4096)throw Error('on:0..4095 / off:0..4096; full-on unsupported');
  if(off===4096)this.pending.set(board+':'+channel,{board,channel,full_off:true});
  else{const width=((off-on+4096)%4096)*this.tick_us;if(width<500||width>2500)throw Error('servo pulse outside500..2500us');this.setPulse(board,channel,width);}return this;
 }
 outputEnable(board,enabled){if(!Number.isInteger(board)||board<0||board>1||typeof enabled!=='boolean')throw Error('board0..1 / enabled boolean');this.oe[board]=!enabled;return this;}
 async flush(){const frame={version:'p15.control.v1',sequence:this.sequence++,commands:[...this.pending.values()],oe:[...this.oe]};const ack=await this.send(frame);if(!ack.accepted)throw Error('command rejected');this.pending.clear();return ack;}
}
module.exports={PCAAdapter};
