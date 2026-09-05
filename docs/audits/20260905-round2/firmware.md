# 第2次 firmware・音声監査 (2026-09-05)

前回の修正確認に加え、実 `main.cpp` をホスト上で実行し、起動・脱力・再通電・入力喪失・I2C故障・校正・電池検知断線を試験した。firmware/src 全12ファイル、platformio.ini、voice_bridge/persona、専用試験・疑似ドライバを通読した記録は `coverage-firmware.json`。GitHub の追跡先は RV-01 #81 (親 #80)。実機へは書き込んでいない。

## 新しく確認し修正した欠陥

| ID / 重要度 | 再現・根拠 | 修正 | 検証 |
|---|---|---|---|
| F2-01 / P1 | 実 Peripherals に7.4V相当→0Vを10秒入力してもcutout=false。平均が3V未満になると3秒判定を解除していた | 一度電池を検知した後の0Vも低電圧停止に含め、復帰しても停止を保持。USBだけの校正時は未検知扱いを維持 (`peripherals.h:56`) | fault試験、実校正main試験 |
| F2-02 / P1 | 実Servosはboard1不在でもsoftStart完了でready=true。PWM書込ACKも無視し、全軸指令を続けた | 設定読戻し、PWM書込ACK、100ms周期のMODE1/prescale監視。不一致で停止保持、健全側も全ch停止、故障側へfull-offを再試行。5ms I2C timeout (`servos.h:109,165,176`) | 起動時不在・瞬間書込失敗・切断/再接続・PCA設定初期化・再起動ボタン再押下を注入 |
| F2-03 / P1 | UI初期standing=trueかつ周期stand:1により脱力後の再読込だけで再通電。通常ファームも起動時自動通電 | 通常版は初期脱力、起動/脱力は明示操作時のみ送信。体高も操作時だけ更新し、再読込で既定値へ戻さない。校正版の自動1500usは維持 (`control.h:9`, `main.cpp:276`, `web_ui.h:111`) | 実setup/loop、実UIスクリプトの初回接続・再接続・体高変更・rest/start |
| F2-04 / P2 | CH_HEADの機構が未確定なのにch12を順次通電し、旋回で±25°出力 | ch12を未使用として全offを維持。可動軸は12脚+6腕+2目の20ch。既存チャンネル番号・機械寸法は変更なし (`servos.h:37`, `main.cpp:302`) | 使用20chとch12/ch25/予備chのoff検査 |
| F2-05 / P2 | 制御接続切断時は1.5秒待ち、期限切れ速度はメールボックスに残った | 最後に完全速度指令を送った接続の切断で速度/PTTを即時0。期限切れ値も0にしmillis周回時の復活を防止 (`main.cpp:50,232`) | 別クライアント切断では継続、所有接続切断では即0、入力喪失でgait停止 |
| F2-06 / P2 | ESP32の16bit mono FIFOには並べ替えの既知制約があるが、DMAをそのままPCMとして扱っていた | 32bit mono DMAへ変更し、PCM16との上下16bit変換を明示。ネットワークのPCM16/16kHzと64BCLKは維持 (`audio.h:33,131`) | 正負の32bitサンプルを元の時系列順のPCM16へ変換する試験。実波形は未検証 |
| F2-07 / P2 | 修正前の実Audio.runに640bytes投入し128bytesの部分書込を返すと512bytes消失。古い再生の終了と次の開始も排他されていなかった | 未送信分を保持。無進捗1秒で再生解除。世代番号と同じロックで旧再生の終了/失敗が新再生を消さないようにした。DMA末尾待機も維持 (`audio.h:131,217`) | 実stepの部分書込、無進捗timeout、旧世代リセット/読出し拒否、DMA待機 |
| F2-08 / P2 | camera関数はMJPEG対応を表明しながらrequests.contentで無限応答の完了待ち。TTSスレッドは無制限キュー・切断後も生成継続 | MJPEGの最初のJPEGだけ取得して閉じる。4MiB・総取得期限・画像形式検査。1byteずつ期限を確認して低速連続応答も検査。TTSキュー16個・取消通知・HTTP応答close (`voice_bridge.py:218,340`) | 初画像後の次フレーム読出し禁止、偽画像/過大画像/低速応答、18チャンク以下で上流停止し取消でclose |
| F2-09 / P3 | DFPlayer要求の読出→0クリア間の競合、負のhistory_turnsで空dequeからpop、personaが測定値を受け取らないことが不明確 | atomic exchange、負値を拒否、実測状態が渡らないことをpersonaに明記 | ビルド、負値試験、全文照合 |

修正前の直接観測は `firmware-before.log`。原稿は `/tmp/tachikoma-firmware-round2-before` に保存して対照した。PWM変換はインストール済み Adafruit PWM Servo Driver 3.0.3 の同じ式と切捨てを使用し、正常時の幅を変更していない。従来の writeMicroseconds は内部prescale読出と書込の失敗を返さないため、検査済みprescaleとACKを返すsetPWMに置き換えた。

ESP32 mono FIFO制約の一次情報は [Espressif I2S仕様](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/peripherals/i2s.html)。デバイスは古いAPIを使うArduino 2.0.17/ESP-IDF 4.4系なので、公開仕様に加え、インストール済みdriver/i2s.hのbits_per_chanとDMA定義、Adafruitの実ソースを確認した。SDKを更新する変更はしていない。

## 実行結果

- `firmware-tests.log`: 通常/校正のPlatformIOビルドを含む全実行PASS。通常版RAM 64,400 bytes、Flash 945,181 bytes。
- `firmware-host-final.log`: 最終の5つのC++実行、実UI JavaScript、Python 7試験、実ローカルWebSocket一往復38,400 bytesがPASS。
- C++の正常運動検査は10,000フレーム。gait.h/leg_output.h/config.hの数値・数式は第1次監査から変更せず、シミュレーション担当へ固定を通知した。
- 通常版と校正版それぞれの実setup/loopが、ボード疑似故障・電池検知喪失・停止/再開に応答することを確認。ヘッダーだけの検査には留めていない。
- 全10本の割当ピンは重複なし。ADC34、I2C21/22、I2S26/25/27/33、DFPlayer16/17、LED4。カメラは別基板で、本体のGPIO割当を増やしていない。
- `git diff --check`、Python構文検査を実施。すべてホスト試験とビルドであり、電流や実パルス幅の計測結果ではない。

## 実機確認を残す条件

1. **独立した停止回路**: I2Cが断線/固定された基板のPWMをソフトだけでは消せない。復旧後には停止命令を再送するが、断線中・CPU停止中・再起動完了までのパルス停止は保証しない。OE/電源遮断と保持/転倒を含む既存実機ゲートは未完了。
2. **電圧検知**: 起動時から検知線が未接続の場合とUSBのみはソフトで区別できない。USB校正を維持するため未検知は自動停止対象にしない。実機では電池接続時の表示電圧・分圧配線・停止時間を確認する。
3. **サーボ実測**: 1500usと実角度、ホーン位相、個体の180/270°、JOINT_SIGN、トルク/保持電流、再通電時の実姿勢は測定できていない。softStartは中立角を仮定した順次通電であり、外力で動いた関節を検出する機能ではない。
4. **I2Sと部品実装**: 64BCLK/WS、マイクの左チャンネル選択、MAX98357AのSD結線と左右選択、実音量・歪み・DMA末尾の実時間は未確認。32bit変換のホスト試験は実波形測定の代わりにならない。
5. **実ネットワーク/API**: 本番キーを使うSTT/LLM/TTS・カメラ実機は呼んでいない。HTTP応答待ちの同期スレッドは切断時も直ちに強制終了できず、無応答のread timeoutを待つ場合がある。APIモデル・声ID・契約は別途確認する。
6. **使用基板**: 設定はesp32dev、GPIO16/17を使用。実モジュールがPSRAM搭載WROVER等でこのピンを内部使用する場合の互換性は未確認。実装品の型番を確定する。

併行して購入表のSANWA PM7aに「DC400µ–10A」とある誤記を確認した。[メーカー仕様](https://www.sanwa-meter.co.jp/japan/products/digital_multimeters/pm7a.html)は電圧・抵抗・導通・ダイオードのみで電流機能がない。文書担当へ修正を引き継いだ。
