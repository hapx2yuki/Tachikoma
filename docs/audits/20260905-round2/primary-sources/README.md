# メーカー資料の出典と再取得

確認日: 2026-09-05。メーカー原本PDF、ページ画像、STEP入りZIPとSTEPから変換した原形状STLは内部参照用としてローカルに保持する。これらの再配布条件は未確認のため、GitHubには原本を複製せず、出典・SHA-256・測定結果を保存する。利用可否についての法的判断を示すものではない。

| 資料 | 公開元・取得先 | 保存する根拠 |
|---|---|---|
| DS3218仕様 | [DSSERVO資料ページ](https://www.dsservo.com/down.asp?id=22) | 原本/描画のハッシュ、シミュレーションの仮定と数値 |
| OV3660モジュール仕様 | [Seeed仕様書](https://files.seeedstudio.com/wiki/SeeedStudio-XIAO-ESP32S3/new-res/OV3660_Camera_Module_Specification.pdf) | 原本/描画のハッシュ、カメラ候補の寸法・視野検査 |
| XIAO ESP32-S3 Sense 3Dモデル | [Seeed公式案内](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/)の3D Model、[ZIP](https://files.seeedstudio.com/wiki/SeeedStudio-XIAO-ESP32S3/res/seeed-studio-xiao-esp32s3-sense-3d_model.zip) | 組立変換付き測定JSONと原本ハッシュ |

全体のファイル照合は [source-register.json](source-register.json)。権利者は上表の公開元として記録し、外部の原本/原形状の再配布許諾は `UNVERIFIED` とする。ここに含める寸法測定JSONと `camera_removed_*_envelope.stl` 2個は、本監査で求めた予約領域の数値と単純な直方体であり、メーカーの詳細形状を複製したSTLではない。

XIAO測定を再現する場合は、公式ZIPをこのディレクトリの `xiao-esp32s3-sense-3d_model.zip` として保存し、台帳のハッシュと一致するか確認する。メーカー側で更新されていた場合、過去と同じ入力とは扱わない。

```sh
.venv/bin/pip install --target /tmp/tachikoma-step-runtime 'cadquery-ocp==7.9.3.1.1' 'vtk==9.7.0'
PYTHONPATH=/tmp/tachikoma-step-runtime .venv/bin/python tools/measure_xiao_step.py
```

このSTEPは2023年の旧カメラ世代の参考資料。購入済みの基板・OV3660・ケーブルを実測した値ではない。収納候補の判定には現在の実部品との照合が必要。
