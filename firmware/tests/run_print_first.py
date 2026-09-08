#!/usr/bin/env python3
"""手持ち構成と通常構成のホスト試験。実機へ書き込まない。"""
import subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def run(cmd):
 print('$',' '.join(map(str,cmd)),flush=True)
 subprocess.run(list(map(str,cmd)),cwd=ROOT,check=True)
with tempfile.TemporaryDirectory(prefix='tachikoma-print-first-') as td:
 base=['c++','-std=c++17','-include','tools/tests/firmware_stubs/Arduino.h','-I','firmware/tests/stubs','-I','tools/tests/firmware_stubs','-I','firmware/src','-I','firmware/.pio/libdeps/esp32dev/ArduinoJson/src']
 for leds,df in ((1,1),(0,0),(1,0),(0,1)):
  flags=[f'-DTACHIKOMA_STATUS_LEDS={leds}',f'-DTACHIKOMA_DFPLAYER={df}',
         f'-DTACHIKOMA_AUDIO_VOLUME_PERCENT={25 if not leds and not df else 100}']
  for name in ('peripheral_profile','profile_main'):
    out=Path(td)/f'{name}-{leds}{df}'
    run(base+flags+[f'firmware/tests/{name}_test.cpp','-o',out]);run([out])
    if name=='profile_main' and not leds and not df:run([out,'--missing-pca'])
 # 歩容profileも実ヘッダーを通してコンパイルし、固定体高の上限が
 # telemetry と control の両方に反映されることを確認する。
 for leds,df in ((0,0),(1,1)):
  out=Path(td)/f'profile-main-print-first-{leds}{df}'
  volume=25 if not leds and not df else 100
  flags=[f'-DTACHIKOMA_STATUS_LEDS={leds}',f'-DTACHIKOMA_DFPLAYER={df}',
         f'-DTACHIKOMA_AUDIO_VOLUME_PERCENT={volume}',
         '-DTACHIKOMA_PRINT_FIRST_PROFILE=1']
  run(base+flags+['firmware/tests/profile_main_test.cpp','-o',out])
  run([out]+(['--missing-pca'] if not leds and not df else []))
 # 実I2Sの回帰を同じ省部品フラグで実行。microSD/DFPlayerに依存しない。
 out=Path(td)/'i2s-print-first'
 run(base+['-DTACHIKOMA_STATUS_LEDS=0','-DTACHIKOMA_DFPLAYER=0','tools/tests/firmware_audio_test.cpp','-o',out]);run([out])
 for volume in (0,25,100):
  out=Path(td)/f'audio-volume-{volume}'
  run(base+[f'-DTACHIKOMA_AUDIO_VOLUME_PERCENT={volume}','firmware/tests/audio_volume_test.cpp','-o',out]);run([out])
 out=Path(td)/'calibration-print-first'
 run(base+['-DTACHIKOMA_STATUS_LEDS=0','-DTACHIKOMA_DFPLAYER=0','-DTACHIKOMA_AUDIO_VOLUME_PERCENT=25',
           'tools/tests/firmware_calibration_main_test.cpp','-o',out]);run([out])
 # 不正値を静かに真扱いしない。意図したコンパイルエラーだけを確認。
 probe=Path(td)/'invalid.cpp';probe.write_text('#include "profile_config.h"\nint main(){}\n')
 for flag in ('TACHIKOMA_STATUS_LEDS=2','TACHIKOMA_DFPLAYER=-1',
              'TACHIKOMA_AUDIO_VOLUME_PERCENT=101','TACHIKOMA_AUDIO_VOLUME_PERCENT=-1'):
  result=subprocess.run(['c++','-std=c++17','-I','firmware/src','-D'+flag,str(probe),'-o',str(Path(td)/'invalid')],cwd=ROOT,capture_output=True,text=True)
  assert result.returncode and ('Peripheral feature flags must be' in result.stderr or 'Audio volume percent must be' in result.stderr)
 print('PASS: invalid feature and volume values rejected at compile time',flush=True)
run(['node','firmware/tests/profile_ui_test.cjs'])
print('PASS: print-first and standard/custom/reduced-peripherals profiles')
