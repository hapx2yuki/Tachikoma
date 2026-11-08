# P15 制御・部品交換インターフェース v1

## 起動

```sh
node tools/power-system-simulator/server.cjs
```

http://127.0.0.1:8796/ で波形画面。端末上の別プロセスから次を実行すると、同じ画面で外部制御の応答が見える。

```sh
python3 tools/power-system-simulator/controller-example.py --seconds 8
```

外部プログラムと画面サンプルの同時実行はしない。サーバーは127.0.0.1専用で実機には接続しない。終了はサーバーのCtrl+C。保存済みソースから再起動可能。

## 境界

`制御ロジック → PCA互換アダプター → HTTP JSON → 共通数値モデル → テレメトリー → 波形`

実機実装時はアダプターの送信先を実機ドライバーへ差し替える。現在はシミュレーター用アダプター、Python制御例、通信仕様を実装。ESP32の命令セットや既存ファーム全体を実行するCPUエミュレーターではない。`setPWM`は幅とFULL_OFFの互換サブセットであり、開始位相や全レジスターは非対応。

## エンドポイント

すべてPOSTは`Content-Type: application/json`。副作用のないGETを除きクロスオリジン要求を拒否する。

| API | 入力・出力 |
|---|---|
| GET `/api/state` | `p15.telemetry.v1`。計算状態・部品・設定・イベント・故障を返す。時間は進まない |
| POST `/api/command` | `p15.control.v1`。下記のPWMバッチ |
| POST `/api/step` | `{"step_s":0.02}`。dt=0.002秒の整数倍、最大1秒 |
| POST `/api/run` | `{"running":true}`で20msごとに時間を進める。遅延分の無制限追い付きはしない |
| POST `/api/reset` | `{"config":{"loadScale":0.3}}`。現在の部品セットを保ち0秒・PWM OFFへ戻す |
| POST `/api/fault` | `{"jammed":2}` / `{"i2cLost":true}` / `{"mainOn":false}`。jammed=-1で解除 |
| GET `/api/devices` | カタログと適用中の部品セット |
| POST `/api/devices` | `{"devices":部品セット,"config":試験条件}`。部品交換、0秒・PWM OFFへ初期化 |

実時間モード中の明示ステップ・部品交換は409で拒否。明示ステップを使う場合、`run(false)`の後に指令→step→状態取得を順番に呼ぶ。1つの制御クライアントが時間を所有する。

```json
{
  "version":"p15.control.v1",
  "sequence":0,
  "commands":[{"board":0,"channel":2,"pulse_us":1800}],
  "oe":[false,false]
}
```

- sequenceは非負・単調増加整数。重複を409で拒否。reset/部品交換後は0から。
- board=0/1、channel=0..15。1バッチ最大32チャネル、同一宛先の重複は禁止。
- pulse_us=500..2500。`{"board":0,"channel":2,"full_off":true}`で停止。これは一般的なLED PWMではなくサーボ用の範囲制限付きAPI。
- `oe[board]=true`は出力禁止。無指令時は最後のPWMを保持。自動ウォッチドッグは実装していない。
- 新しい幅は次の20ms制御境界でモデルへ適用。I²C断は新規書き込みを拒否し最終値を保持。論理電源喪失はレジスターをOFFへ戻す。電源復帰後は新たな書き込みが必要。
- 現行チャンネル：脚0..11（FR, FL, RL, RRのYaw/Hip/Knee）。腕は基板1の0=右肩、3=右爪、4=左肩、7=左爪。腕はP14仮案で、既存ファームへの採用は別作業。
- 未割当チャネルはレジスターとして受理するが架空のサーボを生成しない。
- バッチ全体を検証してから適用。不正値400、通信断・停止MCU・旧sequence・時計競合409。部分適用しない。

## 波形とテレメトリー

`simulation.t`は秒、`bus/leg/arm/logic`はV、`ib/ileg/iarm`はA。
`servos[i]`: `target/q/command`は入力指令/応答/量子化後モデル制限指令の度、`tau`はNm、`current`はA、`pulse`はµs、`period_us`はPWM周期。`outputOff`は実効的な出力禁止。`commandLimited`は大きな指令制限の表示。

角度原点は試験用。入力PWMと角度の対応はLD-220MGでメーカー値、MG90Sで仮定値。モデル指令±70°、仮想物理ストッパー±80°は機構の実可動域ではない。`q/current/tau`は計算真値であり、実サーボから読めるセンサー出力ではない。制御器は、実機で取得できない値を通常フィードバックへ混入させないこと。

PCAは25MHz typical、prescale=121を基準にtick=4.88µs、周期19.98848ms（約50.0288Hz）。実発振器偏差は未校正。PWM図は現在レジスターからの再構成で、オシロスコープ実測ではない。

## 部品交換

`devices-default.json`を保存元とする。選択肢の交換、または`custom`に部品定義を追加して対応slotのIDを変更する。部品定義は`kind/name/source/evidence/spec/model/unknown`を持つ。

- `spec`：出典の条件付き仕様。取得できない値はnull。
- `model`：計算上の仮定や選択。メーカー定格を保護動作の閾値と混同しない。
- `unknown`：未同定の項目。取り込みで消さない。
- 対応slot：脚/腕サーボ、電池、腕/制御DC-DC、マイコン負荷、PCA、スイッチ、ヒューズ、リレー。
- 対応モデル族：PWM位置サーボ、SOC-OCV電池、静的buck変換器、等価電力マイコン、PCA9685互換PWM。UARTサーボやboost等の違うトポロジーは同じ式へ無理に入れず別モデルの実装が必要。
- スイッチは理想接点、ヒューズは任意有効化の仮想I²t、リレーは未接続参照。これらの現物特性は未特定。カタログにあるだけで精密モデル化済みとはしない。
- 現在の`custom`にカタログの同種エントリーを複製し、出典・数値・未確定項目を更新する。出典URLは実行されない。
- HOBBYWING86010030は部品交換の比較候補で、購入・採用やBOM変更ではない。
- 交換時のconfigは試験条件のみ（例loadScale）。電流・電池容量等をconfigで明示上書きすると部品値より優先する。その場合は校正値または実験条件として記録する。

本APIは実機妥当性、過渡回路、熱、回生、EMI、SPICE、CPU命令実行、実I²Cの代替ではない。現時点で正確に照合できるのは取得した定格値・端子機能・PWM式と、宣言した近似モデルの数値計算。
