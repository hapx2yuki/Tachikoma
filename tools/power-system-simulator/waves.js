/* Scalar simulation telemetry plots. PWM is reconstructed, not measured GPIO. */
(function(root){
function lines(canvas,rows,series,{min=0,max=1,unit='',seconds=10,time=null}={}){
 canvas.width=Math.max(320,Math.round(canvas.clientWidth)||canvas.width);const c=canvas.getContext('2d'),W=canvas.width,H=canvas.height;c.clearRect(0,0,W,H);c.font='14px system-ui';
 const end=Math.max(seconds,time??(rows.length?rows.at(-1).t:0)),start=end-seconds,x=t=>63+(W-78)*(t-start)/seconds,y=v=>H-27-(H-51)*(v-min)/(max-min);
 c.fillStyle='#a9bdcd';c.strokeStyle='#2e414f';c.lineWidth=1;
 for(let i=0;i<=4;i++){const v=min+(max-min)*i/4;c.beginPath();c.moveTo(60,y(v));c.lineTo(W-15,y(v));c.stroke();c.fillText(v.toFixed(max-min>20?0:1)+unit,0,y(v)+4);c.fillText((start+i*seconds/4).toFixed(1)+'s',63+(W-94)*i/4,H-6);}
 for(const {get,color}of series){c.beginPath();c.strokeStyle=color;c.lineWidth=2;let begun=false;for(const r of rows){if(r.t<start||r.t>end)continue;const value=get(r);if(!Number.isFinite(value))continue;begun?c.lineTo(x(r.t),y(value)):c.moveTo(x(r.t),y(value));begun=true;}c.stroke();}
}
function pulse(canvas,servo){canvas.width=Math.max(320,Math.round(canvas.clientWidth)||canvas.width);const c=canvas.getContext('2d'),W=canvas.width,H=canvas.height;c.clearRect(0,0,W,H);c.font='14px system-ui';c.fillStyle='#a9bdcd';c.fillText('HIGH',0,22);c.fillText('LOW',0,65);const period=(servo.period_us||20000)/1000,total=2*period;const x=ms=>63+(W-78)*ms/total;c.strokeStyle='#2e414f';c.lineWidth=1;for(let i=0;i<=4;i++){c.beginPath();c.moveTo(x(i*total/4),10);c.lineTo(x(i*total/4),67);c.stroke();c.fillText((i*total/4).toFixed(1)+'ms',x(i*total/4)-8,88);}
 c.strokeStyle='#f1bc64';c.lineWidth=2;c.beginPath();c.moveTo(x(0),62);for(let frame=0;frame<2;frame++){if(!servo.outputOff){c.lineTo(x(frame*period),18);c.lineTo(x(frame*period+servo.pulse/1000),18);c.lineTo(x(frame*period+servo.pulse/1000),62);}c.lineTo(x((frame+1)*period),62);}c.stroke();}
root.P15Waves={lines,pulse};
})(globalThis);
