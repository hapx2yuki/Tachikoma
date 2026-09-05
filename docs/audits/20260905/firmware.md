# ファームウェア・音声制御の監査結果 — 2026-09-05

追跡: [RV-01 #81](https://github.com/hapx2yuki/Tachikoma/issues/81)、親 [E9 #80](https://github.com/hapx2yuki/Tachikoma/issues/80)。

**修正が必要な欠陥を確認した。特に操作画面は JavaScript の初期化例外により、脱力・ジョイスティック・PTT などの登録前に停止していた。** 本監査ではファームウェアと音声ブリッジを修正し、ホスト回帰試験と通常版・校正版のビルドを完了した。実機への書込み、サーボ通電、録音、外部 API の有料呼出しは実施していない。

## 対象と前提

- `AGENTS.md`、`docs/HANDOFF.md` を通読し、`firmware/src` 全ファイル、`firmware/platformio.ini`、`tools/voice_bridge.py` を確認した。音声配線は `docs/wiring.md` と照合した。
- 起点は主査が保存した `Tachikoma-audit-backups/20260905-150317/baseline.tar.gz`。元コードを一時ディレクトリへ取り出し、実ヘッダと埋込み JavaScript を再実行した。
- 幾何定数、歩幅、体高、サーボ型番、端子割当、STL/3MF は変更していない。体高スライダの下限表示のみ、既存ファームの 110 mm に合わせた。
- `bugfix-root-cause-investigator` の再現・原因切り分け・最小修正・回帰試験の手順を使用した。

## 確認した欠陥と修正

| 番号 | 重要度 | 修正前の問題と根拠 | 修正・検証 |
|---|---|---|---|
| FW-01 | P1 | `web_ui.h` で `const b` の初期化より前に `b('eyeBtn')` を実行。Node 実行で `ReferenceError: Cannot access 'b' before initialization`。先に登録された定期送信だけが継続し、脱力等の操作登録には到達しなかった。 | `b` の宣言を先頭へ移動。埋込みスクリプトをそのまま実行し、脱力ボタンが `stand:0` を送ることを確認。通信断・画面非表示時は移動/PTT をゼロにし、再接続で旧指令を再開しない。現行箇所: `web_ui.h:82`、`:117`。 |
| FW-02 | P1 | `Servos::softStart()` は有効フラグを立てるだけ。`main.cpp` は全軸 `ready()` まで PWM を出さず、順次通電の説明と異なり最初の有効周期でまとめて出力した。元コードで最初の 100 ms 後の PWM 出力件数は 0。 | 100 ms ごとに実際の中立パルスを 1 軸へ出す。使用 21 軸の出力時刻差が 100 ms 以上であることをホストで確認。固定カメラの ch25 を対象から除外。起動時に全チャンネルを full-off にし、腕・目のソフト状態を中立パルスに合わせる。現行箇所: `servos.h:31`、`:36`、`arms.h:42`。 |
| FW-03 | P1 | 脚には出力スルーがあるが、腕ガードはスルー前の `legs[].ang` を参照。前脚目標が 35°→0°の退出時、出力は 30.2°でもガードが解除される。 | 脚の出力を `LegOutput` に分離し、目標・出力の危険側を腕へ渡す。FR/FL 両側について、退出フレームでも実際に生成した PCA 指令が退避角のままであることを確認。脚の単脚/ペアヨー制約もスルー後に適用。現行箇所: `leg_output.h:9`、`:22`、`main.cpp:289`。 |
| FW-04 | P1 | 校正版は低電圧を監視するものの、`cutout()` と Web の脱力指令をパルス停止に使わず、`allUs(cal_us)` を継続した。 | `Peripherals` の本物の低電圧ラッチを模擬 ADC 入力で成立させ、校正時も全 32 ch の full-off と再開しないことを確認。脱力ボタンも同じ停止経路に接続し、再起動時は順次通電をやり直す。現行箇所: `main.cpp:242`、`servos.h:80`。 |
| FW-05 | P1 | `/cal?us=NaN` 等は `String::toInt()` の変換失敗が 0 となり、500 µs 端へ丸められる。WS の部分 JSON は欠落した `stand` を 1 に戻す。最終 `writeDeg(NaN)` は `fmaxf` を通じ実際に 500 µs を出した。 | HTTP 整数の形式/範囲と JSON 型/有限値を検査し、不正なフレーム全体を拒否する。部分指令は他の状態を保持し、移動 3 成分が揃わないフレームでは通信期限を延長しない。最終サーボ出力も非有限値を拒否する。IK は非有限入力時に出力を書き換えない。現行箇所: `control.h:16`、`:45`、`servos.h:55`、`ik.h:17`。 |
| FW-06 | P2 | AsyncTCP と制御ループが `arms.target` 等へ同時アクセス。構造体代入と `volatile` は複数フィールドの一貫性を保証しない。NVS 保存の途中でトリムを変更すると、末尾の `dirty_=false` が新しい保存要求を消す順序があった。 | 制御用メールボックスを短いクリティカルセクションで更新/取得し、運動状態は制御ループだけが変更する。トリムは保存前にスナップショットを取り、保存中の変更は次回に残す。NVS スタブの書込中へ新しいトリム変更を挿入し、2 回目に保存されることを確認。現行箇所: `main.cpp:216`、`servos.h:112`。 |
| FW-07 | P2 | INMP441 は WS 周期あたり 64 SCK が必須。一方、16 bit/sample かつ既定の `bits_per_chan=0` は左右合計 32 SCK。I2S の DMA へ最後の PCM をコピーした直後にゼロ消去する経路もあり、語尾を切る。 | PCM16 の契約は維持し、物理スロットを 32 bit に指定。DMA の最長待機時間を経て再生状態を解除し、I2S のリセットを音声タスクに集約。設定値はホストで確認したが、実クロックと音量は UNVERIFIED。現行箇所: `audio.h:36`、`:123`、`:144`。 |
| FW-08 | P2 | `handle_connection()` は `busy=True` にした後、同じ受信ループで応答生成を `await` するため、生成中の音声を捨てられず後から処理する。TTS の生成待ち時間を送信開始時刻に含め、初回が 2 秒遅れると後続 5 チャンクの送信間隔がすべて 0 秒になった。 | 応答生成を別の asyncio タスクにし、受信を続けて生成中の入力を破棄する。送信間隔は直前の実送信時刻から計算。遅延後も各 100 ms 音声の間隔が 99 ms 以上であることを仮想時刻で検証。録音開始外の PCM を捨て、録音長/WS フレームに上限を設けた。現行箇所: `voice_bridge.py:377`、`:413`。 |
| FW-09 | P2 | 音声リングの空き確保が 1 byte なので、あふれると 16,383 byte を保持し、以後 PCM16 の境界がずれる。拒否した 2 本目の接続の切断イベントが、正規接続の再生状態までリセットする。 | 空き単位を 2 byte にしてサンプル境界を維持。リングのコピー/リセットをロックし、奇数長フレームは拒否。正規ブリッジ ID だけに操作/切断を許可。オーバーフロー、折返し、奇数長読出し、2 本目の接続拒否と切断を実ヘッダで検証。現行箇所: `audio.h:88`、`:103`、`:188`。 |

### 修正前の数値の読み方

[`firmware-before.log`](firmware-before.log) は元コードの実行結果である。`legIK(NaN)` はこの試験では **false** を返したが、出力角へ NaN を書き込んだ。これを「IK が NaN を成功扱いした」とは判定していない。独立に、`writeDeg(NaN)` が端の 500 µs を出すことを確認した。

## 実行済みの検証

再現コマンド:

```sh
.venv/bin/python tools/tests/firmware_run.py --build
```

- `firmware_contract_test.cpp`: 実際の `Servos` / `Arms` / `Gait` / `LegOutput` / `Peripherals` / 指令解析をホストでコンパイル。上表の指令/PWM/ラッチ/NVS と、変化する指令 10,000 フレームの有限角・ヨー制約を検証。
- `firmware_audio_test.cpp`: 実際の音声クラスのイベントとリングを検証。I2S 呼出しはスタブであり、波形確認の代わりにはならない。
- `firmware_ui_test.cjs`: 埋込み JavaScript を実行し、初期化、脱力送信、再接続時のゼロ化、画面非表示時のゼロ化、体高下限の一致を検証。実スマートフォン上の操作試験は別途必要。
- `firmware_voice_test.py`: 応答生成中に届く次の発話が処理されないこと、不正 JSON 配列で接続処理が落ちないこと、TTS 初回生成遅延後の送信間隔を検証。
- `voice_bridge.py --mock --self-test`: ダミー WS サーバとの一往復、PCM 38,400 byte、`tts_begin` → PCM → `tts_end` の順序が PASS。
- 通常版 `esp32dev`: SUCCESS。最終の通常版バイナリを `firmware/.pio/build/esp32dev/firmware.bin` に生成。
- 校正版 `esp32cal`: `CALIBRATION_MODE` を付けた別環境で SUCCESS。既存 `platformio.ini` は書き換えず、一時設定を生成して検証。
- 主査の物理シミュレーション担当から、実 C++ `Gait` / `LegOutput` と Python の 1,077 フレーム比較 PASS、最大差 0.000895° の報告を受領。独立証拠は物理シミュレーション側の監査記録を参照。

主証拠: [`firmware-host-tests.log`](firmware-host-tests.log)。全ホスト試験と通常版・校正版の最終ビルドを含む。元コードの失敗は [`firmware-before.log`](firmware-before.log)。`firmware-build.log` / `firmware-host-regression.log` は途中時点の補助記録である。

## 残る UNVERIFIED と実機試験への引継ぎ

以下は、本監査のホスト試験だけでは「動作確認済み」としない。

| 対象 | 確認すること | 引継ぎ先 |
|---|---|---|
| 校正・電源 | 実サーボの 180°/270°、1500 µs 中立、各軸符号、トリム値、実 PWM 幅。模擬 ADC のラッチ試験と、実電圧低下での停止は別物。VBAT 配線/分圧係数を実測し、校正版でも停止すること。 | [EL-03 #30](https://github.com/hapx2yuki/Tachikoma/issues/30)、[EL-07 #34](https://github.com/hapx2yuki/Tachikoma/issues/34) |
| 起動・再有効化 | 支持台で中立への順次通電と立位への移行を観察し、電流/電圧降下を測る。脱力中の実角は読み取れない。0°からの軸別スルーは定常 IK の到達可能集合外を通り得るため、外装付きの遷移干渉を別途確認する。 | [L-09 #44](https://github.com/hapx2yuki/Tachikoma/issues/44) |
| 腕脚連成 | 今回保証した `cur_` は PCA へ出す**指令角**であり、実サーボが到達済みの角ではない。腕の退避が脚の進入に間に合うか、荷重下の追従遅れ/ガタ/たわみを支持台で確認する。必要なら実測速度に基づく先行退避や動作の連携が必要。 | [L-09 #44](https://github.com/hapx2yuki/Tachikoma/issues/44)、歩行・外装検証 |
| 非常停止 | I2C 断線/バス固着/ESP32 リセット時、PCA9685 は独立に出力し得る。ソフトからの full-off と WDT は電源遮断や OE の独立停止と同等ではない。実配線で停止手段と保持時間を確認する。 | [EL-07 #34](https://github.com/hapx2yuki/Tachikoma/issues/34)、[L-09 #44](https://github.com/hapx2yuki/Tachikoma/issues/44) |
| 音声 | WS 16 kHz / BCLK 1.024 MHz、INMP441 L/R=GND、16 bit mono のサンプル並び、MAX98357A の SD 結線と音量、語尾の欠落/エコー/連続再生、再接続を実測する。API キー・実 STT/LLM/TTS・実カメラの結合は未実施。 | [H-04 #53](https://github.com/hapx2yuki/Tachikoma/issues/53) |

無指令時の自動脱力、実サーボトルク、電源容量、外装の取付可否は、それぞれ既存の機構/電装・実機課題へ残す。今回の修正で機械全体の成立を証明したとは扱わない。

## 音声仕様の一次資料 (確認日: 2026-09-05)

- [TDK / InvenSense INMP441 datasheet](https://invensense.tdk.com/wp-content/uploads/2015/02/INMP441.pdf): p11 のデジタルインターフェースが WS 周期あたり 64 SCK を要求する。
- [Espressif ESP-IDF v4.4.7 の I2S 実装](https://raw.githubusercontent.com/espressif/esp-idf/v4.4.7/components/driver/i2s.c): ビットクロックはサンプルレート × チャンネル数 × スロット幅で計算される。ローカル導入 SDK の `driver/i2s.h` でも `bits_per_chan` を確認した。
- [Analog Devices MAX98357A](https://www.analog.com/en/products/max98357a.html): I2S の 16/24/32 bit データを扱う。基板の SD 結線と実際の再生音量は未実測。
