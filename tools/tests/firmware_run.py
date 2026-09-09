#!/usr/bin/env python3
"""firmware のホスト回帰を実行。--build で通常版/校正版もビルドする (書込なし)。"""
import argparse
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]

def run(command, timeout=120):
    print("\n$ " + shlex.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=ROOT, check=True, timeout=timeout)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='tachikoma-firmware-tests-') as directory:
        temporary = Path(directory)
        for name in ('contract', 'audio', 'fault', 'main', 'calibration_main'):
            binary = temporary / name
            run(['c++', '-std=c++17', '-include', 'tools/tests/firmware_stubs/Arduino.h',
                 '-I', 'tools/tests/firmware_stubs', '-I', 'firmware/src',
                 '-I', 'firmware/.pio/libdeps/esp32dev/ArduinoJson/src',
                 f'tools/tests/firmware_{name}_test.cpp', '-o', binary])
            run([binary])
        run(['node', 'tools/tests/firmware_ui_test.cjs'])
        run([sys.executable, 'tools/tests/firmware_voice_test.py'])
        run([sys.executable, 'tools/voice_bridge.py', '--mock', '--self-test'])
        if args.build:
            ini = temporary / 'platformio-audit.ini'
            original = (ROOT/'firmware/platformio.ini').read_text()
            ini.write_text(original + '''\n[env:esp32cal]
extends = env:esp32dev
build_flags =
    ${env:esp32dev.build_flags}
    -DCALIBRATION_MODE
''')
            run([ROOT/'.venv/bin/pio', 'run', '-d', 'firmware', '-c', ini, '-e', 'esp32cal'], 240)
            run([ROOT/'.venv/bin/pio', 'run', '-d', 'firmware'], 240)
    print('\nPASS: all firmware host checks' + (' and both builds' if args.build else ''), flush=True)

if __name__ == '__main__':
    main()
