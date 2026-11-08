# P15 電源・動作数値シミュレータ

P14の接続案（脚12軸・腕4軸、独立サーボ給電、PCA9685×2、ESP32）を基準にした、既存の操作可能な数値モデルです。P36 S1基板やP37ハーネスの実装、実機ファームの変更は含みません。実機へ通電・指令する機能もありません。現行構成との対応と制限は[simulator.md](../../docs/power-system/simulator.md)を参照してください。

## 起動

リポジトリのルートから：

```sh
node tools/power-system-simulator/server.cjs
```

`http://127.0.0.1:8796/`は`monitor.html`を返し、外部制御APIと波形モニターを使えます。`index.html`の操作画面をHTTPで開く場合は下のPythonサーバーを使います。終了は各サーバーのCtrl+Cです。Node.js 18以降、Python例と静的サーバーにはPython 3を使います。npmパッケージや外部サービスは使いません。

単体版は`tools/power-system-simulator/P15-standalone.html`をブラウザで開けます。外部CDN・npm・ネット接続は不要です。

`index.html`の操作画面を配信する場合：

```sh
python3 -m http.server 8795 --bind 127.0.0.1 --directory tools/power-system-simulator
```

http://127.0.0.1:8795/index.html を開きます。開始ボタンで時間が進み、CSVと設定JSONを保存できます。この画面はブラウザ内モデルを動かし、Node APIサーバーの状態とは共有しません。

## 試し方

1. 「歩行用の関節波形」で開始し、指令角と応答角（計算値）、電池電流を比較します。
2. 負荷倍率や共通配線抵抗を増やし、モデル上の電圧降下と遅れの変化を見ます。
3. 「全軸40°ステップ」で初期化し、軸ごとの遅延条件を比較します。
4. FR Kneeなどを拘束し、角度が止まってもモデル電流が流れる条件を見ます。
5. I²C断線のモデル状態と主電源OFFを別々に確認します。
6. 腕電流制限を変えると出力電圧がどう変わるかを比較できます。実UBECの制限特性は未同定です。

「歩行用の関節波形」は16個の位置指令です。接地、支持多角形、転倒、R69実姿勢を計算した歩行ではありません。爪はサーボ軸の指令と等価バネ負荷を示します。爪形状や実機荷重はモデルに含みません。

## 計算モデル

単位はrad、秒、Nm、A、V、Ω、kg·m²です。画面では角度を度に変換します。

- 物理更新2ms、指令更新20ms。PCAの25MHz typical / prescale121 / 12bitを使い、約50.0288Hzと4.88µs単位の量子化を反映します。
- 電池の開放電圧`Voc(SOC)`は仮の区分線形曲線です。`Vbus = Voc - Ibat*(Rbattery+Rcommon)`を電流との連立問題として各ステップで解きます。
- 直接給電の脚は`Vleg = Vbus - Ileg*Rleg`で表します。安定化分岐と理想的な静的電流制限もモデル化します。ヒカップ・熱・過渡応答は再現しません。
- サーボのトルク・速度・電流と等価負荷は仮定値を含みます。巻線PWM電流、回生、温度、実負荷は解きません。
- 電池Ah/Wh、MCU低電圧、PCA論理電源喪失、I²C断は計算状態です。電池監視センサーや実機の停止動作を実装・検証したものではありません。
- 仮想ヒューズと低電圧停止は初期状態で無効です。I²t、ソフト停止、理想スイッチは比較用のモデル機能で、現物回路・定格の検証ではありません。

部品定義は`device-catalog.json`と[SPEC_REVIEW.md](SPEC_REVIEW.md)、仮定台帳は`sources.json`です。`devices-default.json`の値と明示的な試験条件を区別してください。メーカー条件付き値、モデル仮定、未同定値は混ぜずに記録しています。

## 検証と再現

リポジトリのルートから既存の数値・APIテストを実行できます。

```sh
node tools/power-system-simulator/tests/numerical.cjs
node tools/power-system-simulator/tests/interface.cjs
```

結果JSONとCSVは`tools/power-system-simulator/results/`へ生成されます。この出力ディレクトリは配布せず、`.gitignore`で追跡対象外です。数値/APIテストは宣言した計算モデルとHTTPインターフェースを検査します。実機の電流、温度、保持力、連続運転、ヒューズ、電池、歩行成立性は検証しません。

元環境のChrome/Playwright専用テストは、その環境外の絶対パスを参照していたため配布していません。この移植ではUIのHTTP応答とAPIを検査しますが、ブラウザでの描画や波形の視覚確認は未実施です。

## 制御接続・部品差し替え

- HTTP JSONの指令入力、状態取得、明示時間送りに対応します。PCA互換アダプターとPython制御例を同梱しています。
- `device-catalog.json` / `devices-default.json` / `device-library.js`は部品定義と検証を持ち、画面から部品交換・JSON読み書きができます。
- Python制御例は外部プロセスから同じAPIを呼びます。値は計算モデルの指令と応答であり、実機センサーの計測ではありません。
- メーカー定格から保護応答や電流波形を作りません。接地、熱、回生、SPICE、実MCU命令実行は対象外です。
