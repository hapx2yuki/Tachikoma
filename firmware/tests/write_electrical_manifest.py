#!/usr/bin/env python3
"""手持ち配線案をCSV/JSONへ出す。数値ピンは実config.hから読む。測定欄は空。"""
import csv,hashlib,json,re
from pathlib import Path
R=Path(__file__).resolve().parents[2];OUT=R/'outputs/print-first-20260905/electrical';OUT.mkdir(parents=True,exist_ok=True)
config=(R/'firmware/src/config.h').read_text()
def pin(name):return 'GPIO'+re.search(r'\b'+name+r'\s*=\s*(\d+)',config)[1]
C=[]
def add(src,dst,net,wire,condition='接続前に無電源で極性・導通・実端子を確認'):
 C.append(dict(id=f'WIRE-{len(C)+1:02}',source=src,destination=dst,net=net,wire=wire,condition=condition,status='計画・実物未確認'))
add('LiPo +','既存ヒューズ入力','VBAT','AWG16')
add('既存ヒューズ出力','手動スイッチ入力','VBAT_FUSED','AWG16','15AヒューズとDC10A表示スイッチの保護協調は未確認')
add('手動スイッチ出力','HENGE入力+ / mini560 #1入力+ / mini560 #2入力+','VBAT_SWITCHED','AWG16幹線+適合分岐','スイッチOFFで全変換器への電池入力が切れること')
add('LiPo −','全変換器GND / ESP32 GND / PCA GND / 各サーボGND / 音声GND / XIAO GND','GND','AWG16幹線、AWG20電源分岐、信号と共通','正出力3系統は互いにつながない')
add('HENGE出力+','脚12個のV+','SERVO_LEG_6V','AWG16幹線+AWG20各分岐','実出力6Vと全脚サーボの許容電圧を確認。PCA V+へ集約しない')
add('mini560 #2出力+','MG90S腕6個 / ES9251II目2個のV+','SERVO_SMALL_5V','AWG20分岐','全個体5V適合・保持力・電流は未確認。ロジック5Vと正極を並列にしない')
add('mini560 #1出力+','ESP32 5V/VIN / MAX98357A VIN / XIAO 5V','LOGIC_5V','AWG20分岐','ESP32/XIAOへUSBを挿す前に外部5Vを外す')
add('ESP32 3.3V','PCA×2 VCC / INMP441 VDD','LOGIC_3V3','AWG26','PCAのVCCとサーボV+を区別、待機SDA/SCLも3.3Vを確認')
add(pin('PIN_SDA'),'PCA×2 SDA','I2C_SDA','AWG26')
add(pin('PIN_SCL'),'PCA×2 SCL','I2C_SCL','AWG26')
add('PCA board0出力0..11','FR/FL/RL/RRのyaw/pitch/knee信号','PWM_LEGS','AWG26','PCA_CHの順を確認、正極は外部6Vバスから')
add('PCA board1出力0,1,2,4,5,6,8,10','右腕3/左腕3/右目/左目の信号','PWM_SMALL','AWG26','グローバルch16,17,18,20,21,22,24,26。正極は外部5Vバスから')
add('PCA board1 A0','同基板アドレス設定','I2C_ADDRESS','既存ジャンパ/はんだ','0x41に設定。board0は0x40')
add('VBAT_SWITCHED','100kΩ → 分圧点 → 33kΩ → GND','VBAT_SENSE','AWG26','既存セットから抵抗値を測って選ぶ')
add('分圧点',pin('PIN_VBAT'),'VBAT_ADC','AWG26','実電池電圧と画面値を照合。起動時0VをUSBのみと区別できない')
add(pin('PIN_I2S_BCLK'),'INMP441 SCK / MAX98357A BCLK','I2S_BCLK','短いAWG26')
add(pin('PIN_I2S_WS'),'INMP441 WS / MAX98357A LRC','I2S_WS','短いAWG26')
add(pin('PIN_I2S_DIN'),'INMP441 SD','I2S_MIC','短いAWG26','マイクのSDはデータ。アンプのSD/MODEと混同しない')
add(pin('PIN_I2S_DOUT'),'MAX98357A DIN','I2S_TX','短いAWG26')
add('INMP441 L/R','同マイクGND','MIC_LEFT','マイク側の短い既存線','長距離マイク配線はVDD/GND/SCK/WS/SDの5本')
add('MAX98357A SD/MODE','同基板VIN','AMP_LEFT','短い既存線','Adafruit購入実基板と一致すること。左ch選択')
add('MAX98357A GAIN','同基板VIN','AMP_GAIN_6DB','短い既存線','Adafruit仕様。電源再投入後有効')
add('MAX98357A SPK +/−','φ20mm 8Ω 1Wスピーカーの2端子','AUDIO_BTL','AWG26または適合する既存SH2pinリード','どちらのスピーカー端子もGNDへつながない')
for src,dst,net in [('1000µF/16V #1','6V脚バス/GND','LEG_DECOUPLE'),('1000µF/16V #2','5V腕目バス/GND','SMALL_DECOUPLE'),('470µF/10V','5Vロジック/GND','LOGIC_DECOUPLE')]:
 add(src,dst,net,'短いリード','極性・耐圧を現物確認。過渡応答は未測定')
with (OUT/'connections.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(C[0]));w.writeheader();w.writerows(C)
trials=[('power_off','抵抗値/極性/導通','全電源を外す'),('logic_no_load','mini560 #1出力','5V狙い'),('small_no_load','mini560 #2出力','5V狙い'),('leg_no_load','HENGE出力','6V狙い'),('adc','電池/分圧/Web VBAT','実値を突合'),('voice_bench','ESP32+I2S、サーボなし','25%再生振幅'),('one_leg_servo','対象脚サーボ1個、ホーン/荷重なし','定格電圧の確認後'),('one_small_servo','MG90Sまたは目サーボ1個','5V定格確認後'),('manual_off','USBなしで主SWをOFF','3系統が落ちること')]
with (OUT/'measurement-template.csv').open('w',newline='') as f:
 w=csv.writer(f);w.writerow(['trial','load','condition','battery_V_measured','leg_V_measured','small_V_measured','logic_V_measured','temperature_C_measured','current_A_measured','instrument','duration_s','observations','status'])
 for t,l,c in trials:w.writerow([t,l,c,'','','','','','','','','','UNVERIFIED'])
paths=['firmware/platformio.ini','firmware/src/profile_config.h','firmware/src/config.h','firmware/src/peripherals.h','firmware/src/audio.h','firmware/src/main.cpp','firmware/src/web_ui.h',
       'firmware/src/control.h','firmware/src/gait.h','firmware/src/arms.h','firmware/src/print_first_gait.h',
       'hardware/src/config.py','tools/generate_print_first_profile.py','firmware/tests/run_print_first.py',
       'firmware/tests/profile_main_test.cpp','firmware/tests/profile_ui_test.cjs']
data={'date':'2026-09-05','status':'配線候補・ホスト試験対象。実機未通電/未書込','new_purchases_required_for_initial_profile':False,
 'profile':'esp32dev_print_first','firmware_flags':{'TACHIKOMA_STATUS_LEDS':0,'TACHIKOMA_DFPLAYER':0,'TACHIKOMA_AUDIO_VOLUME_PERCENT':25},
 'motion_constants_modified':False,'rail_plan':{'leg_servo':{'voltage_target_V':6,'supply':'既存HENGE','axes':12,'current_A':None},'small_servo':{'voltage_target_V':5,'supply':'既存mini560 #2','axes':8,'current_A':None},'logic':{'voltage_target_V':5,'supply':'既存mini560 #1','loads':['ESP32','MAX98357A','XIAO'],'current_A':None}},
 'inventory_allocation':{'脚サーボ':{'required':12,'assigned':12,'reserve':None},'MG90S':{'required':6,'assigned':6,'reserve':None},'ES9251II':{'required':2,'assigned':2,'reserve':None},'mini560':{'required':2,'assigned':2,'reserve':None},'20mm_speaker':{'required':1,'assigned':1,'reserve':None},'PCA9685':{'required':2,'assigned':2,'reserve':None}},
 'omitted_initially':['WS2812','74AHCT125とLED専用抵抗/100nF','DFPlayerとmicroSD','追加の自動OE/電源遮断回路'],
 'retained':['脚12軸','腕6軸','左右目2軸','I2Sマイク/再生','XIAO独立カメラ(実ソフト/画像は別受入)','ソフト異常停止','既存スイッチの手動停止'],
 'unused_channels':[12,19,23,25],'connections':C,
 'measurement_limits':['DT830Bは電圧/無電源抵抗で使用。10A端子の定格/ヒューズが未確認なのでサーボ電流の直列測定を指示しない','現物5V/6V適合・単体保持・全機電流/温度は未測定','HENGE最低入力電圧は未確認、ソフト6.4V停止を6V維持保証としない','物理仕事率から電流を作らない','手動停止はCPU独立だが、無人故障時の自動遮断ではない'],
 'sources':[{'url':'https://www.hiwonder.com/products/ld-220mg','checked':'2026-09-05','used_for':'6〜8.4V/20kgcm@7.4V/500〜2500us180度。6V連続トルクは未記載'},{'url':'https://github.com/hapx2yuki/Tachikoma/issues/99','used_for':'現物LD-220MGと旧保持部不適合の報告'},{'url':'https://learn.adafruit.com/adafruit-max98357-i2s-class-d-mono-amp/pinouts','checked':'2026-09-05','used_for':'GAIN/SD/差動出力・5V8Ωの出力上限'},{'url':'https://documentation.espressif.com/esp-dev-kits/en/latest/esp32/esp32-devkitc/user_guide.html','checked':'2026-09-05','used_for':'ESP32の外部5V/USB給電の択一'},{'url':'https://learn.adafruit.com/16-channel-pwm-servo-driver/pinouts','checked':'2026-09-05','used_for':'PCA VCCとサーボV+の分離'},{'path':'docs/additional-purchases.json','used_for':'必要数/仕様/未確認境界'}],
 'source_sha256':{p:hashlib.sha256((R/p).read_bytes()).hexdigest() for p in paths}}
(OUT/'configuration.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
print('Saved',len(C),'connections and',len(trials),'empty measurement rows; no estimated current values')
