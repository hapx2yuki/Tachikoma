# 購入済み部品と設計値の照合

2026-09-05。GitHub P-01/P-03の注文メモと現存ファイルを照合した。現物を識別した記録ではない。

| 部品 | 設計・資料 | 注文メモ/現物の状態 | 次に確定する値 |
|---|---|---|---|
| 脚サーボ | DS3218、180°のパルス変換。メーカー資料は4.8–6.8V、180°/270°両品 | LD-20MG×14のメモとLD-220MGの図面が混在。型番未確認 | ケース/タブ/軸/ホーン寸法、角度とパルス、許容電圧、実保持トルク |
| サーボ電源 | HOBBYWING UBEC 10A V2、6V出力、連続10A/瞬間15A | HENGE 8Aの注文メモ。入力下限7Vの記載が2S末期条件と合わない可能性 | 現物型番、入力下限、負荷時出力/温度、5V側との合計入力 |
| 腕・目サーボ | MG90S×6/ES9251II×2 | クローンや別版の定格を一括適用できない | 6V適合、実ケース、ホーン、電流、可動角 |
| カメラ | CAM2既存寸法は旧センサー前提。現行Seeed資料のOV3660は対角102° | XIAO ESP32S3 Senseのセンサー版が未確認 | センサー刻印、子基板8×8×5.30mm、FPC長/幅、主基板とUSBの空間 |
| MAX98357A | Adafruit #3006はSDに基板上プルアップあり | 購入基板の実装が同一か未確認 | SD端子回路、ゲイン設定、8Ω/1Wスピーカーへ許容出力 |
| 電圧計/電流計 | SANWA PM7aは電流測定不可 | PM7aの購入候補を電流計として記載していた箇所を訂正 | 十分な定格のDCクランプ計またはシャント/記録器 |

一次資料: [DS3218](https://www.dsservo.com/down.asp?id=22)、
[LD-220MG](https://www.hiwonder.com/products/ld-220mg)、
[Hobbywing UBEC 10A Car](https://www.hobbywing.com/products/ubec-10a-car79)、
[Seeed XIAO ESP32S3](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/)、
[MAX98357A #3006](https://learn.adafruit.com/adafruit-max98357-i2s-class-d-mono-amp/pinouts)、
[PM7a](https://www.sanwa-meter.co.jp/japan/products/digital_multimeters/pm7a.html)。

## 電源測定

[測定表](power-measurement.csv)は未記入のひな形。測定結果を仮の値で埋めていない。
サーボ・5V系・電池入力を分けて記録する。計器の帯域/記録周期より短いピークは捕捉できない旨も残す。
入力電流の計算は `(Vservo×Iservo/ηUBEC + Vlogic×Ilogic/ηDC) / Vbat`。
`power-budget.json`は仮定の感度計算であり、実測電流・歩行可能な時間ではない。

無負荷1個、片脚の段階荷重、支持台上の全軸、接地静止、短時間歩行の順に記録する。
サーボ電源は独立スイッチで切れる状態にし、停止時にも機体を支持する。
ヒューズは単に15Aを採用せず、スイッチのDC定格・配線/端子・電池・UBECと溶断時間特性を照合する。
USBから給電している間は外部5Vを切り離す。

I2C故障ではPCA9685が最後のPWMを出し続けるため、通信経由の停止だけでは足りない。
既存スイッチによる独立遮断を実機で確認し、OE回路の追加は配線図と故障時の出力を検証してから採用する。
