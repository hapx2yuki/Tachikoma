# 電気部品の仕様再確認 — 2026-11-08

既存P14の接続と購入記録を起点に照合。メーカーの定格、製品版一致、未測定のモデル係数は別の情報として管理する。

| 対象 | 今回の根拠 | 反映と限界 |
|---|---|---|
| LD-220MG | [Hiwonder公式](https://www.hiwonder.com/products/ld-220mg) | 6–8.4V、20kgf·cm@7.4V、0.16s/60°、100mA no-load、500–2500µs/180°、20ms。拘束電流・PD・負荷・電圧換算は仮定 |
| MG90S | [TowerPro公式](https://towerpro.com.tw/product/mg90s-3/)、[別公式販売ページ](https://torqpro.com/product/mg90s/) | 4.8V基準の1.8kgf·cm、0.10s/60°を反映。別ページのdeadband1µsをモデル参考値に使用。購入個体・上限電圧・PWM角度・電流は未確認 |
| PCA9685×2 | [NXP Rev.4](https://www.nxp.com/docs/en/data-sheet/PCA9685.pdf) | 12bit、25MHz typical、prescaler式、OE、論理電源範囲、電源復帰時OFFを反映。現行prescale121で4.88µs/tick。完全なI²Cレジスタエミュレーションではない |
| ESP32-DevKitC | [Espressifデータシート](https://www.espressif.com/sites/default/files/documentation/esp32_datasheet_en.pdf)、[V4回路図](https://dl.espressif.com/dl/schematics/esp32_devkitc_v4_sch.pdf) | RF条件別100mA RX/240mA TXを負荷モデルの基準に使用。3.3V・25°C等の条件付き。基板補助電流30mAや無線duty等は仮定。CPU命令実行は行わない |
| HENGE 8A | [メーカー説明書・販売店配布](https://www.flyingtech.co.uk/wp-content/uploads/2021/02/201751721654.pdf) | 入力7–25.5V、出力5/6/7.4V、連続8A、短時間12Aを台帳化。購入版未確認。入力仕様外を警告。8A定格は電流制限の動作点ではない |
| Zeee電池 | [メーカー同寸法品](https://zeeebattery.com/products/zeee-2s-2200mah-shorty-lipo-battery-7-4v-50c-deans)、ユーザー写真 | 2S/2200mAh、73×34×18.5mm。参照品Deansと現物XT60の違いを明記。OCV・内部抵抗・劣化は未測定 |
| MINI560 | 製造元・実装IC不明 | 同名流通品だけから特性を確定しない。現在の5V/3A/効率90%/差電圧2Vは計算仮定。未知表示を残す |
| Kiligen B083NGXQ13 | 購入識別子あり、メーカー確定資料なし | 3線の役割・DC定格・抵抗を未確定。理想スイッチのみ |
| ac292 | ホルダーの識別子 | 素子の品番/定格/時間電流特性を特定できない。仮想I²tを現物ヒューズとして扱わない |
| G5LE | [OMRON公式](https://omronfs.omron.com/en_US/ecb/products/pdf/en-g5le.pdf) | 標準型DC抵抗負荷8A@30V、通電10Aを参照台帳化。コイルsuffix/ドライバー未選定で未接続。モーター負荷への適合は別検討 |
| 交換比較HOBBYWING86010030 | [HOBBYWING公式](https://www.hobbywing.com/en/index.php/products/ubec-8a-2-3s100) | 入力6–12.6V、5/6V、連続8A/peak15A、自己消費60mA。HENGEと同じ制御APIで差し替え比較。購入・採用を意味しない |

## 取得証拠

元のP15作業フォルダには取得記録と参照資料のコピーがありました。portable sourceには第三者資料の原本ファイルを含めず、この要約に出典URLと取得上の限界を残しています。ここに記載した仕様値は当時のP15レビュー記録であり、現行部品の購入版確認や再取得を示しません。

資料にない値を断定した「完全に正確な実機エミュレーター」ではない。カタログ式との照合・数値実装の試験は実施済み。サーボ電流、実負荷、UBEC保護、電池抵抗、電源過渡応答は測定して部品JSONへ差し替える必要がある。仕様範囲外も計算比較は継続するが、そこでの実機応答は保証しない。

## 実装に反映した変更

- 固定50Hz近似からPCAの発振周波数・prescalerによる量子化へ変更。
- 制御DC-DCと腕UBECの効率・差電圧・自己消費を個別の部品定義へ分離。
- バッテリー容量/SOC-OCV/抵抗、サーボ基準トルク・速度、PWM対角度を交換可能にした。
- MCU停止とPCA電源喪失を区別。後者では外部指令レジスターを解除する。
- プロファイル交換の際に未知の値を消さず、結果へ部品IDと未検証状態を付ける。
