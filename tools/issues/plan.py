"""物理製作フェーズの作業分解 (issues-as-code)。

`tools/issues/sync_github_issues.py` がこのファイルを読み、GitHub の
ラベル / マイルストーン / イシュー / サブイシュー (親子) / 依存 (blocked by) を
冪等に同期する。**作業計画の正はこのファイル** — GitHub 上で直接編集した本文は
`--update-bodies` で上書きされるので、恒久的な変更はここへ書く。

構造:
  MILESTONES : Go/No-Go ゲートに対応する時間軸 (M0..M5)
  LABELS     : スコープ付きラベル (type/ area/ res/ prio/ skill/ + 並行作業OK)
  ISSUES     : key を持つイシュー。parent (エピック) と blocked_by (依存) で
               直列/並列関係を表す。本文中の {{KEY}} は同期時に #番号へ置換される。

キー体系: E=エピック / P=準備 / PR=印刷 / EL=電装 / L=脚・歩行 / A=腕 /
H=頭部 (目・カメラ・音声) / S=意匠シェル / I=統合。

前提となる現況 (2026-09-03 時点, docs/HANDOFF.md・print_manifest.md・
メモリの印刷状況スナップショット 2026-08-31 に基づく):
  印刷済み: PETG_Walk_1 (chassis+battery_cradle+audio_cradle_mic) /
            PETG_Walk_2 (coxa×4+femur×4, 8/27) / PLA_Matte_Blue_1〜4 /
            PLA_Matte_White / White_2 / elbow_shells / claw_mount_L 先行分 /
            foot_pad TPU×4 / leg_foot_bored×2 (旧 rev の可能性)
  未印刷:   PETG_Walk_3_Tibia / PLA_Matte_Gray / PETG_Walk_4_Rest /
            PLA_Matte_Gray_2 / PLA_Black_1 / PLA_Red_1 / Head_Top_Eyecut v2 (要確認)
  部品:     調達済み (ユーザー申告 2026-09-03)。サーボは未実測 (config.py [要実測])
"""

REPO = "hapx2yuki/Tachikoma"
MARKER = "tachikoma-key"   # 本文先頭の <!-- tachikoma-key: X --> で冪等化

# ---------------------------------------------------------------------------
# マイルストーン (Go/No-Go ゲートに対応)
# ---------------------------------------------------------------------------
MILESTONES = [
    ("M0 準備完了",
     "棚卸し・サーボ実測・config.py 確定 (全チェッカー緑)・電源ベンチ・サーボ中立化まで。"
     "ここが終わるまでサーボ寸法に依存する骨格の追加印刷はしない。"),
    ("M1 片脚 Go/No-Go",
     "脚 1 本をベンチで組み、1.2kg リフト試験 (6V) に合格する。"
     "不合格なら設計見直し→再生成→再印刷のループ (docs/assembly.md §1)。"),
    ("M2 頭無し歩行",
     "4 脚+シャーシ+電装で立位→前進→旋回→停止が転倒なしで成立する (意匠シェル無し)。"),
    ("M3 サブアセンブリ",
     "腕 (左右)・目ポッド・カメラ目・音声ユニットがそれぞれ単体で動作する。"),
    ("M4 フルドレス",
     "頭部・Mouth・Cabin・脚装飾・腕シェル・LED を全装着した状態になる。"),
    ("M5 統合・完成",
     "フルドレスでの歩行調整・音声会話・カメラ連携・演出確認・お披露目。"),
]

# ---------------------------------------------------------------------------
# ラベル (name, color, description)
# ---------------------------------------------------------------------------
LABELS = [
    # 種別
    ("type/エピック",  "5319e7", "作業ストリームの親イシュー。サブイシューで子を束ねる"),
    ("type/ゲート",    "b60205", "Go/No-Go 判定。合格するまで下流へ進まない"),
    ("type/タスク",    "0e8a16", "1 人が 1 回の作業 (半日〜1日) で完了できる単位"),
    ("type/要判断",    "fbca04", "オーナー (@hapx2yuki) の決定が必要。決定内容をコメントに残す"),
    ("type/不具合",    "d73a4a", "印刷不良・はめあい NG・設計と現物の齟齬・故障"),
    # 領域
    ("area/印刷",       "1d76db", "Bambu X2D での 3D プリント (プレート単位)"),
    ("area/組立",       "0052cc", "骨格・サブアセンブリの組立"),
    ("area/配線",       "006b75", "ハーネス製作・本体配線"),
    ("area/電装",       "0e8a16", "電源・基板・LED・サウンドの電気系"),
    ("area/ファーム",   "5319e7", "ESP32 / XIAO のビルド・書き込み・Web UI"),
    ("area/試験",       "e99695", "ベンチ試験・歩行試験・計測"),
    ("area/仕上げ",     "c2e0c6", "意匠シェルの接着・塗装・装飾"),
    ("area/CAD",        "bfd4f2", "config.py 更新・STL 再生成・検証スクリプト"),
    ("area/測定",       "fef2c0", "ノギス実測・電流・重量などの実測"),
    ("area/ドキュメント", "0075ca", "docs/ の更新"),
    ("area/運営",       "ededed", "プロジェクト運営・棚卸し・環境整備"),
    # 占有リソース (同時に 1 件しか進められないもの)
    ("res/プリンタ",    "f9d0c4", "X2D を占有する。印刷キュー (E2) の順番で進める"),
    ("res/本体",        "f9d0c4", "本体 (シャーシ組立品) を占有する。同時作業は調整して"),
    # 並列性
    ("並行作業OK",      "0e8a16", "手元のパーツ・工具だけで独立して進められる (複数人同時可)"),
    # 優先度
    ("prio/P0", "b60205", "クリティカルパス。これが止まると全体が止まる"),
    ("prio/P1", "d93f0b", "次のゲートに必要"),
    ("prio/P2", "fbca04", "後回し可 (完成度・演出)"),
    # 必要スキル (自己選択の手がかり)
    ("skill/はんだ",       "c5def5", "はんだ付け・圧着・配線"),
    ("skill/CAD-Python",   "c5def5", "hardware/src の Python CAD と検証スクリプト"),
    ("skill/ファーム",     "c5def5", "PlatformIO / Arduino / Web UI"),
    ("skill/模型仕上げ",   "c5def5", "接着・ヤスリ・塗装・現物合わせ"),
    ("skill/プリンタ操作", "c5def5", "Bambu Studio / AMS / フィラメント運用"),
]

# 既定ラベル (good first issue / help wanted) はそのまま活用する。

# ---------------------------------------------------------------------------
# イシュー定義
# ---------------------------------------------------------------------------
# 共通のフッタ (全イシュー本文の末尾に付く)
FOOTER = (
    "\n---\n"
    "運用ルール: [CONTRIBUTING.md](https://github.com/hapx2yuki/Tachikoma/blob/main/CONTRIBUTING.md) "
    "/ 全体図: [docs/build_plan.md](https://github.com/hapx2yuki/Tachikoma/blob/main/docs/build_plan.md)。"
    "着手時はコメントで宣言して自分を Assignee に。完了時は **証拠 (写真・実測値)** をコメントしてから Close。"
)

D = "https://github.com/hapx2yuki/Tachikoma/blob/main/docs"   # docs へのリンク短縮

ISSUES = []


def issue(key, title, *, parent=None, milestone=None, labels=(), blocked_by=(), body=""):
    ISSUES.append(dict(key=key, title=title, parent=parent, milestone=milestone,
                       labels=list(labels), blocked_by=list(blocked_by), body=body.strip()))


# ===========================================================================
# エピック (8 ストリーム)
# ===========================================================================
issue("E1", "E1 [エピック] 準備 — 棚卸し・サーボ実測・config 確定",
      milestone="M0 準備完了", labels=["type/エピック", "area/運営", "prio/P0"],
      body=f"""
## このストリームの目的
物理製作の**前提を固める**。特にサーボ実寸 → `hardware/src/config.py` 更新 → STL 再生成 →
全チェッカー緑、という設計側の確定が済むまで、サーボ寸法に依存する骨格 (tibia など) の
追加印刷はしない ([HANDOFF §5]({D}/HANDOFF.md) の順序)。

## 並列性
- {{{{P-01}}}} {{{{P-02}}}} {{{{P-03}}}} {{{{P-06}}}} {{{{P-07}}}} は互いに独立 → **最大 5 人で同時に着手可**
- {{{{P-04}}}} だけが直列 (実測待ち)。{{{{P-05}}}} はオーナー判断

## 完了条件
サブイシューが全て Close。`config.py` の `[要実測]` が全て実測値で埋まり、
[AGENTS.md の検証コマンド](https://github.com/hapx2yuki/Tachikoma/blob/main/AGENTS.md) が全緑。
""")

issue("E2", "E2 [エピック] 印刷キュー — X2D は 1 台なので順番待ち",
      labels=["type/エピック", "area/印刷", "res/プリンタ"],
      body=f"""
## このストリームの目的
プリンタ (Bambu X2D) は**単一リソース**なので、印刷ジョブは 1 本のキューとして扱う。
プレート構成は [docs/print_manifest.md 「単色プレート 3mf 一覧」]({D}/print_manifest.md) が正、
3mf は `hardware/stl/*.3mf` (生成は `tools/make_plates.py`)。

## 推奨順 (依存が解けた順に詰める)
1. サーボ寸法に**依存しない**キット意匠プレート — 今すぐ流せる:
   {{{{PR-01}}}} → {{{{PR-02}}}} → {{{{PR-03}}}}
2. サーボ実測・config 確定 ({{{{P-04}}}}) 後の骨格:
   {{{{PR-04}}}} (tibia = 歩行の最後のブロッカー) → {{{{PR-05}}}} → {{{{PR-06}}}}
3. 個別実測待ち: {{{{PR-07}}}} (音声実測後) / {{{{PR-08}}}} (Head_Top v2 要確認) / {{{{PR-09}}}} (再印刷が必要と判断された場合のみ)

## 運用
- 印刷担当は **1 件ずつ** 着手 (`res/プリンタ` ラベル)。開始時に「送信した (何 g / 何 h)」、
  完了時に**ベッド上の写真**をコメント
- AMS 実装 (2026-08-15 実読): A1=Polymaker Panchroma Matte Sapphire Blue (Generic PLA 登録) /
  A2=Bambu PLA Matte Ivory White / A3=Bambu PLA Matte Ash Gray / A4=Bambu PETG Translucent Gray /
  右 Ext=PLA Basic 黒 / 左 Ext=TPU 95A 黒 (bypass)。**黒・赤プレートは 3mf がスロット 1 出力なので
  Studio で割当を差し替える** ([print_manifest 注記]({D}/print_manifest.md))
- Bambu Studio の落とし穴 (AMS 同期 3 段階、数値欄の入力癖、複数選択時の回転表示) は
  [docs/printing.md]({D}/printing.md) とメモリの知見を参照
- `model/*.stl` (キット) は **150% スケール必須**、`hardware/stl/*.stl` は 150% 済み
""")

issue("E3", "E3 [エピック] 電装・電源・ファームウェア (ベンチ)",
      milestone="M0 準備完了", labels=["type/エピック", "area/電装", "prio/P0"],
      body=f"""
## このストリームの目的
本体に載せる前に、電源系・PCA9685×2・ESP32・ハーネスを**ベンチで単体動作**させる。
印刷や組立と完全に独立なので、はんだ付けができる人が最初から並行して進められる。
配線の正は [docs/wiring.md]({D}/wiring.md)、部品は [docs/BOM.md]({D}/BOM.md)。

## 鉄則 (wiring.md より)
- **サーボ電源は PCA9685 の V+ を経由させない** — AWG16 バスに直結、PCA へは信号+GND のみ
- 2S LiPo → 15A ヒューズ → ロッカー SW → UBEC 6V/10A (サーボ) / DC-DC 5V/3A (ロジック)
- WS2812 は 74AHCT125 でレベル変換 (直結不可)、DFPlayer TX には 1kΩ 直列

## 並列性
{{{{EL-01}}}} {{{{EL-02}}}} {{{{EL-03}}}} {{{{EL-05}}}} {{{{EL-06}}}} {{{{EL-08}}}} は独立。{{{{EL-04}}}} は 01-03 の後。
""")

issue("E4", "E4 [エピック] 脚・歩行 — 片脚ゲート → 4 脚 → 頭無し歩行",
      milestone="M2 頭無し歩行", labels=["type/エピック", "area/組立", "prio/P0"],
      body=f"""
## このストリームの目的
プロジェクトの**クリティカルパス**。手順の正は [docs/assembly.md §1-§2]({D}/assembly.md)。

```
{{{{L-01}}}} 脚 FL 組立 ─▶ {{{{L-02}}}} ゲート: 1.2kg リフト ─▶ {{{{L-03}}}}/{{{{L-04}}}}/{{{{L-05}}}} 残り 3 脚 (3 人並行可)
                                                    │
{{{{L-06}}}} シャーシ電装 (並行) ───────────────────────┴─▶ {{{{L-07}}}} 脚取付 ─▶ {{{{L-08}}}} 配線 ─▶ {{{{L-09}}}} 通電 ─▶ {{{{L-10}}}} ゲート: 歩行
```

## 重要な前提知識
- 脚は **標準版 (FL, RR) とミラー版 `_m` (FR, RL)** の 2 種。取り違えると 45° ペアの股ピッチサーボ同士が干渉して組めない
- 脚方位 (正面=90°): FR 15° / FL 165° / RL 210° / RR 330°
- ヨーサーボのケースは 4 個とも **X 軸平行・ボディ中央向き** (脚方位と独立)
- 関節は全て**ホーン片持ち結合** (アイドラー無し)。ホーンはリンク側ポケットへ M2.6 タッピングで共締め
- 歩容は firmware 側でワークスペース射影されるので、手ポーズで深タック (pitch45/knee30) を作らないこと
""")

issue("E5", "E5 [エピック] 腕 (左右) — 骨格チェーン → 吊り下げ → シェル",
      milestone="M3 サブアセンブリ", labels=["type/エピック", "area/組立", "prio/P1"],
      body=f"""
## このストリームの目的
腕は歩行に影響しないので、脚と**完全に並行**して進められる ([assembly.md §2.5]({D}/assembly.md))。
肩ブラケットから先 (肩ピッチ・上腕・肘・前腕・固定爪) はベンチで組めて、
最後に肩ヨーサーボ (シャーシ内蔵) のホーンへ吊り下げる。

## 前提知識
- 手は**キット準拠の固定爪** (可動グリッパは廃止済み)。爪ハブは両腕とも `Arm_Left_Claw_Grey` を使う
- 中立 (ヨー 0) は正面向きではなく**放射外向き 40°** (Head_Bottom ソケットの実測方位)
- ヨー ±15° / ピッチ -45〜+85° / 肘 0〜95°。firmware に脚×腕連成クランプあり (歩行中 42-44% の時間で腕が内寄せ退避するのは正常)
""")

issue("E6", "E6 [エピック] 頭部 — 目ポッド・カメラ目・音声ユニット",
      milestone="M3 サブアセンブリ", labels=["type/エピック", "area/組立", "prio/P1"],
      body=f"""
## このストリームの目的
頭部に載る 3 つの独立サブシステムをそれぞれ**単体で動作**させる
([assembly.md §2.7-2.9]({D}/assembly.md), [docs/voice.md]({D}/voice.md))。
3 つは互いに独立 → 3 人で並行可。

| サブシステム | 部品 | 実測ゲート |
|---|---|---|
| 目ポッド ×2 (キョロキョロ) | eye_pod (白, 印刷済) + eye_carrier (PETG) + サブマイクロ ES9251II | サーボ実測 ({{{{P-03}}}}) |
| カメラ目 (中央, 固定) | eye_pod_camera shell/base (白, 印刷済) + camera_carrier + XIAO ESP32S3 Sense | センサー/子基板実測 ({{{{P-07}}}}) |
| 音声 (砲身内蔵) | Mouth_*_Bored + audio_cradle_mic/spk + INMP441 + φ20 SPK + MAX98357A | 基板/SPK 実測 ({{{{P-06}}}}) |

## 未決事項
{{{{H-06}}}} ESP32 の恒久マウント位置 (頭内は不成立が確定) — 頭を閉じる前に決める。
""")

issue("E7", "E7 [エピック] 意匠シェル・Cabin・仕上げ (フルドレス)",
      milestone="M4 フルドレス", labels=["type/エピック", "area/仕上げ", "prio/P1"],
      body=f"""
## このストリームの目的
歩行が成立した本体に、キット準拠の意匠シェルを被せて**タチコマの見た目**にする
([assembly.md §3]({D}/assembly.md))。

## 並列性
- {{{{S-05}}}} Cabin 組立は純粋な模型組立で、大物 (Front/Back/Eye) は印刷済み → 小物印刷 {{{{PR-01}}}}〜{{{{PR-03}}}} が終わり次第**ボランティア向け** (good first issue)
- 脚装飾 {{{{S-02a}}}} {{{{S-02b}}}} {{{{S-02c}}}} {{{{S-02d}}}} は脚ごとに独立 → 歩行ゲート後に 4 人並行可
- 頭部 {{{{S-03}}}} → Mouth {{{{S-04}}}} は直列。pod_neck {{{{S-01}}}} → Cabin 取付 {{{{S-06}}}} も直列

## 鉄則
見える形状は元キット準拠。加工済み版 (`_Bored`/`_Eyecut`/`_Armcut`) は内部だけ加工されている。
接着は「現物合わせ」と書かれた箇所以外は設計どおり嵌まる前提 — 嵌まらなければ `type/不具合` で報告。
""")

issue("E8", "E8 [エピック] 統合・調整・完成",
      milestone="M5 統合・完成", labels=["type/エピック", "area/試験", "prio/P1"],
      body=f"""
## このストリームの目的
フルドレス状態での歩行調整 ([assembly.md §4]({D}/assembly.md))、音声会話・カメラ連携
([voice.md]({D}/voice.md))、演出確認、ドキュメント反映、お披露目。
""")


# ===========================================================================
# E1 準備
# ===========================================================================
issue("P-01", "P-01 [運営] 購入部品の棚卸し・検品 (BOM / shopping.md D-1 と照合)",
      parent="E1", milestone="M0 準備完了",
      labels=["type/タスク", "area/運営", "area/測定", "並行作業OK", "prio/P0", "good first issue"],
      body=f"""
## ゴール
届いている部品を [docs/BOM.md]({D}/BOM.md) と [docs/shopping.md D-1 (Amazon 一括カート)]({D}/shopping.md)
の全品目と突き合わせ、**あり / 未着 / 欠品 / 仕様違い** を確定する。
(ユーザー申告では調達完了。ただし 2026-08-09 の注文履歴照合で疑義が残っていた品目がある)

## 必ず結論を出す疑義品目
- [ ] **UBEC 6V** (BOM #6): HENGE 8A (B07JJCW9W2) が実際に届いているか。入力下限 7V なので 2S 末期のブラウンアウト懸念あり
- [ ] **M2.5×16 M-F スタンドオフ ×4 + M2.5×6 ×4** (BOM #21b/21c, PCA スタック用): 2026-08-22 時点で未購入と記録
- [ ] **microSD (≤32GB, FAT32)** (DFPlayer 用, BOM #10): 履歴に無し
- [ ] **M3×10 なべ小ねじ** (BOM #18b): M3×8 を購入していた記録 → 秋月かご分が届いているか
- [ ] **脚サーボの型番**: 注文は Hiwonder LD-20MG ×14 (B07CMBMWZW)。2026-09-03 に「LD-220MG」表記の図面が出ているので**現物ラベルの型番・回転角 (180°)・出力軸ベアリング** を確認
- [ ] MG90S ×6 (+予備) / ES9251II ×3 (6V 耐圧) / PCA9685 ×2 / ESP32 DevKitC / XIAO ESP32S3 Sense / INMP441 / MAX98357A / φ20 SPK / DFPlayer / WS2812B ×12 / 74AHCT125 / 2S 2200mAh / 充電器 / ヒューズ / SW / コンデンサ / 線材 (AWG16/20/26/30) / ねじ類 (#18-29, #35 接着剤)
- [ ] フィラメント在庫: 青 (Sapphire Blue) 残量 / 灰 / 白 / 黒 / 赤 (Lava Red) / PETG / TPU 黒

## 完了条件 (DoD)
- [ ] 全品目の状態表 (あり/未着/欠品/仕様違い) をコメントに貼る (表形式)
- [ ] 欠品・仕様違いがあれば **個別イシュー (`type/要判断`) を切って本イシューからリンク**
- [ ] サーボ型番の写真 (ラベル面) を添付

## 証拠
部品を並べた写真、状態表。

## 2026-09-04 追記: 未購入・追加購入 (組立成立性監査)
- [ ] **#21b M2.5×16 M-F スタンドオフ ×4 / #21c M2.5×6 なべ ×4** — BOM.md で「未購入」、shopping.md にも未収載 ({{{{L-06}}}} の PCA スタック固定に必須)
- [ ] **φ2.2mm ドリル刃 ×2** — `HORN_PILOT_D` 2.0→2.2 (2026-09-04) の印刷済み coxa/femur 追加工用。手持ちドリルセットは 2.0/2.35 のみ
- [ ] **DC クランプメータ** (UNI-T UT210E 級) — 歩行中の 6V バス電流 9〜14A 級を非接触で測る ({{{{L-02}}}} {{{{L-10}}}} {{{{EL-09}}}})。直列式テスタ (DT-830B, 10A レンジ) では測れない
- 条件付き ({{{{L-11}}}} / {{{{EL-09}}}} の決定後): 高トルクサーボ ×12〜14 / 15〜20A UBEC・ロッカー SW / 7.4V 化なら 3S LiPo + 6V 副 BEC
詳細は [shopping.md「2026-09-04 追加購入」]({D}/shopping.md)
""")

issue("P-02", "P-02 [運営] 印刷済みパーツの棚卸し・検品・ラベリング (旧 rev 混入チェック)",
      parent="E1", milestone="M0 準備完了",
      labels=["type/タスク", "area/運営", "area/印刷", "並行作業OK", "prio/P0", "good first issue"],
      body=f"""
## ゴール
2026-08-31 時点の「印刷済み」を現物で確認し、**旧リビジョンの混入**を除外、標準/ミラーを取り違えない
ようマーキングする。基準表は [docs/print_manifest.md §1 と「単色プレート 3mf 一覧」]({D}/print_manifest.md)。

## 印刷済みとされているもの (現物と突合)
| プレート | 内容 | 確認ポイント |
|---|---|---|
| PETG_Walk_1 | chassis + battery_cradle + audio_cradle_mic | chassis に ESP32 ボス (浮遊島) が**無い**こと (2026-08-21b 修正版)。mic は 0.2 層で印刷 (圧入面が粗ければ PETG_5_Mic 0.12 で刷り直し) |
| PETG_Walk_2 (8/27) | coxa_bracket ×2 + `_m` ×2, femur_link ×2 + `_m` ×2 | **標準 / `_m` を油性マーカーで刻む** (FL,RR=標準 / FR,RL=`_m`)。膝ディスクが分離していない tibia 修正 (8/21b) は femur には無関係 |
| PLA_Matte_Blue_1 (8/20) | Head_Top_Eyecut + shin_shell ×2 + `_m` ×2 | **Head_Top_Eyecut は v1 (中実) の可能性が高い** — v2 (2026-08-22, 内殻ホロー+スカート切欠き×6) は 8/22 以降に単体印刷したか要確認。v1 なら**使用不可** (脚/腕サーボ・PCA と干渉) → {{{{PR-08}}}} へ。shin_shell も標準/`_m` をマーキング |
| PLA_Matte_Blue_2 (8/20) | Head_Bottom_Armcut + arm_pod ×4 + Mouth_Neck_Bored | Head_Bottom_Armcut が**リムカスプ除去版** (腕ソケット周りに尖った羽根が無い) か |
| PLA_Matte_Blue_3 / 4 | Cabin_Front / Cabin_Back + TailJoint_Blue + Thigh_Guard ×4 | 反り・ブリム跡。Thigh_Guard は 4 個揃っているか (初回はブリム無しで失敗した記録) |
| PLA_Matte_White / White_2 | eye_pod ×2, eye_pod_camera shell+base (+base 予備 ×2), Cabin_Eye_White | 白は透光用 8% インフィル |
| elbow_shells | elbow_shell + `_L` | |
| claw_mount_L 先行分 | claw_mount_L ×1 | Walk_4_Rest で重複するので手元にある方は Studio で削除 |
| foot_pad TPU ×4 | | 糸引き除去済みか |
| leg_foot_bored ×2 | | 旧 rev の可能性 → どのみち Gray プレートで ×4 印刷するので参考扱い |

## 完了条件 (DoD)
- [ ] 上表の全行に「OK / 要再印刷 / 要確認」を付けてコメント (写真付き)
- [ ] 標準/`_m`、左右 (`_L`) をマーキング済み
- [ ] サポート・ブリム除去、バリ取り済み
- [ ] 再印刷が必要なものは {{{{PR-08}}}} / {{{{PR-09}}}} に反映

## 証拠
全パーツを並べた写真、Head_Top 内側の写真 (v1/v2 判定)。

## 2026-09-04 追記 (組立成立性監査)
- **eye_pod_camera_base の印刷済み品は旧 rev**: 現行 STL とポケット壁が y 方向に ~1mm 違う (体積 3.70→3.63cm³, 差分 220/147mm³ を実メッシュで確認)。
  camera_carrier ({{{{PR-06}}}}) と嵌合しない恐れ → {{{{PR-10}}}} で再印刷し、旧品は「旧 rev」ラベルで隔離
- 印刷済み coxa×4 / femur×4 はホーン共締め下穴が φ2.0 (現行 φ2.2) — {{{{P-05}}}} 参照 (ドリル追加工で使用可の見込み)
- Head_Top_Eyecut v1 (中実) は使用不可 → {{{{PR-08}}}}。旧 leg_foot_bored ×2 は旧 rev の疑い → {{{{PR-05}}}} で作り直し
""")

issue("P-03", "P-03 [測定] サーボ・ホーン実測 (STD ×14 / MG90S ×6 / ES9251II ×3) → 実測表",
      parent="E1", milestone="M0 準備完了",
      labels=["type/タスク", "area/測定", "並行作業OK", "prio/P0"],
      body=f"""
## ゴール
`hardware/src/config.py` の `[要実測]` (STD / MICRO / SUBMICRO プロファイルと `HORN_*`) を
埋めるための**実測表**を作る。設計はここまで DS3218 の公称値で進んでおり、**実測値との差が
印刷済み骨格 (coxa/femur) の再印刷要否と tibia 印刷の可否を決める** (プロジェクト最大の不確実性)。

## 測る項目 (各型式 3 個体以上、ノギス 0.05mm)
- ケース: 長 L / 幅 W / タブ下高さ / タブ上高さ (ギヤヘッド含む) / タブ厚 / タブ穴 φ / タブ穴スパン (外-外) と ピッチ / シャフト中心のケース端からの偏心 (SHAFT_OFF)
- 出力軸: スプライン外径 / 歯数 / 軸高さ / 中心ビス径
- ホーン (付属シングルアーム): 全長 / ハブ径 / ハブ厚 / アーム穴の中心距離と φ / アーム厚
- ケーブル出口の位置と向き
- 参考: `config.py` 冒頭のコメントと `STD = dict(...)` / `MICRO` / `SUBMICRO` / `HORN_*` の各キー名に合わせて表を作ると {{{{P-04}}}} が楽

## 完了条件 (DoD)
- [ ] 3 型式 × 上記項目の実測表 (config.py のキー名対応付き) をコメント
- [ ] DS3218 公称 (config.STD: L=40.7 / W=20.2 / タブスパン 54.5 / ピッチ 49.5 / spread 10.0) との差分を明記
- [ ] 実測時の写真 (ノギス表示が読める)

## 注意
- 2026-09-03 に「LD-220MG→DS3218 取付タブ変換クランプ R1」図面 (外部設計, Blender) が持ち込まれた。作者の説明は
  **「ポケットへ差し込まない・ガイド (治具) として使うだけ」**。実測値が出るまで要否は未確定 — LD-220MG のタブ穴が
  既に 49.5×10 パターンでタブ面高さも DS3218 相当なら不要、違うなら「ドリルガイド化」か「config 更新→再印刷」のどちらか
  (評価と修正指示はこのイシューのコメント参照)。**いずれにせよ入口はこの実測**
- 落とし穴 #24: 「[要実測] の値を設計前提にする前に測る」— 測る前に印刷しない

## 2026-09-04 追記: 型番の確定が最優先
書類上は 3 説が併存: shopping.md #1 = Goolsky **DS3218MG** (B07FS9JC2G) / 注文メモ = Hiwonder **LD-20MG** (B07CMBMWZW) /
持込図面 = **LD-220MG**。仕様は大きく違う (確認日 2026-09-04, 販売ページ集約値 — Hiwonder 公式ページは本文取得不可のため UNVERIFIED):
| 型番 | 動作電圧 | トルク | 寸法 L×W×H | 備考 |
|---|---|---|---|---|
| DS3218MG | 4.8–6.8V | 19@5V / 21.5@6.8V kgf·cm | 40×20×40.5 | 7.4V 不可 |
| LD-20MG | 6–7.4V | 20 kgf·cm@6.6V | 40×20×40.5, 65g | 7.4V 可 |
| LD-220MG | 6–8.4V | 20 kgf·cm@7.4V | **40×20×51.4** (両軸: 背面に補助軸), 66g | 箱枠 (TAB_BELOW 28.2) から背面軸が突き出る → 干渉再検査が要る |
- [ ] 現物ラベルの型番と、背面 (タブ下側) に補助軸があるかを写真で記録
- [ ] L / W / H (タブ下側の突出高さ) をノギスで実測 → config.py `STD` の `TAB_BELOW` と比較。LD-220MG なら {{{{P-04}}}} で箱枠と隣接脚の干渉を再検査
""")

issue("P-04", "P-04 [CAD] config.py を実測値へ更新 → STL 再生成 → 全チェッカー緑 → 影響パーツ判定",
      parent="E1", milestone="M0 準備完了",
      labels=["type/タスク", "area/CAD", "skill/CAD-Python", "prio/P0"],
      blocked_by=["P-03"],
      body=f"""
## ゴール
{{{{P-03}}}} の実測値で `hardware/src/config.py` を更新し、STL を再生成して検証を全緑にする。
その上で「どの STL が変わったか」を機械的に出し、**印刷済み骨格の再印刷要否**を判定材料として提示する。

## 手順 (AGENTS.md「検証の掟」)
```bash
# 1. 更新前の STL ハッシュを退避
md5 hardware/stl/*.stl > /tmp/stl_before.md5
# 2. config.py の STD / MICRO / SUBMICRO / HORN_* を実測値へ
# 3. 再生成 (Head_Top_Eyecut は build_all 対象外なので別途)
cd hardware/src && ../../.venv/bin/python build_all.py && cd ../..
.venv/bin/python tools/make_head_eyecut.py
.venv/bin/python tools/export_urdf.py
# 4. 全チェッカー
for t in check_leg_assembly check_screw_bosses sim_gait check_arm check_eye check_audio \\
         check_camera check_shin_arm_leg check_head_pod_clearance check_pod_neck_strength check_urdf \\
         check_leg_link_strength; do
  .venv/bin/python tools/$t.py || echo "FAIL $t"; done
.venv/bin/pio run -d firmware
# 5. 差分
md5 hardware/stl/*.stl | diff /tmp/stl_before.md5 -
# 6. プレート 3mf 再生成 + 検証
.venv/bin/python tools/make_plates.py all && .venv/bin/python tools/make_plates.py verify
```

## 完了条件 (DoD)
- [ ] config.py の diff をコメント (PR でも可)
- [ ] 全チェッカー + `pio run` の出力末尾 (PASS/OK/SUCCESS) を貼る。**1 つでも落ちたら原因切り分けまでがこのイシュー**
- [ ] MD5 が変わった STL の一覧と、それが**印刷済み** (chassis / coxa×4 / femur×4 / Blue_2 の arm_pod など) に該当するかの表
- [ ] 上記の表を {{{{P-05}}}} に貼ってオーナー判断を仰ぐ
- [ ] 3mf 再生成後、`docs/print_manifest.md` の重量/時間が変わっていれば更新

## 注意
- ミラー版 `_m` / `_L` も再生成対象。firmware 定数は sim_gait / check_arm が config.h を regex 実読して突合する (drift 検出)
- **HORN_\*** (ホーン腕長/幅/厚/ハブ/下穴) も実測で更新すること。ホーンが設計値 (腕 32mm) より大きいと tibia 膝ネックの
  増厚部が更に削れるので、更新後は `check_leg_link_strength.py` (SF ≥2.0) を必ず見る (2026-09-04 M-08)
- 2026-09-04 に `HORN_PILOT_D` 2.0→2.2 (共締め M2.6 の下穴, M-02) と tibia ネック修正 (M-01) を先行適用済み。
  実測で更に変わる分だけをこのイシューで扱う
- レンダ・検証は**新規プロセス**で (make_visuals のキャッシュは mtime 非対応)
""")

issue("P-05", "P-05 [要判断] 印刷済み骨格 (chassis / coxa×4 / femur×4 / arm_pod) の再印刷要否",
      parent="E1", milestone="M0 準備完了",
      labels=["type/要判断", "area/印刷", "prio/P0"],
      blocked_by=["P-04"],
      body=f"""
## 決めること
{{{{P-04}}}} の結果、実測サーボ寸法で STL が変わった場合に、**印刷済みの骨格をそのまま使うか再印刷するか**。

## 判断材料 (P-04 が貼る)
- 変わった STL の一覧と変化量 (ポケット寸法差 mm)
- はめあいへの影響: ポケットは片側 CLEAR 分の余裕あり。差が +0.2mm 以内なら現物でヤスリ調整で吸収できる可能性
- 再印刷コスト: Walk_2 (coxa×4+femur×4) = 210g / 13.8h、Walk_1 (chassis) = 66g / 3.7h
- 2026-09-04 先行変更: `HORN_PILOT_D` 2.0→2.2 で coxa/femur の STL も変わっている (ホーン共締め下穴の径のみ)。
  **印刷済み coxa/femur は φ2.2 ドリルで追加工すれば再印刷不要**の見込み。tibia は未印刷 ({{{{PR-04}}}}) なので新 3mf で印刷

## 決定の書き方
「そのまま使う / 一部 (どれ) 再印刷 / 全再印刷」を根拠付きでコメントし、再印刷なら {{{{PR-09}}}} に対象を書いて Close。
変更なし (MD5 一致) なら「変更なし」で Close。

## 2026-09-04 追記
`HORN_PILOT_D` 2.0→2.2 の差は **φ2.2 ドリルで追加工** すれば再印刷不要 (ホーン共締め穴 2 箇所/ホーン。coxa は天板、femur は円板)。
ドリルは未購入 ({{{{P-01}}}})。サーボ寸法 ({{{{P-03}}}}) が DS3218/LD-20MG 相当なら箱枠はそのまま使える見込み。
""")

issue("P-06", "P-06 [測定] 音声ユニット部品の実測 (INMP441 基板 / φ20 SPK 厚) → AUDIO_* 更新 → check_audio",
      parent="E1", milestone="M0 準備完了",
      labels=["type/タスク", "area/測定", "area/CAD", "並行作業OK", "prio/P1"],
      body=f"""
## ゴール
砲身内蔵クレードルは INMP441 基板 (設計仮定 14×11×3mm, `AUDIO_MIC_L/W/T`) とスピーカー厚
(仮定 5mm, `AUDIO_SPK_REAL_H`) を前提に設計されている ([assembly.md §2.8]({D}/assembly.md))。
実物を測って差があれば `config.py` を更新し、`check_audio.py` 緑を確認する。

## 手順
1. INMP441 基板の長・幅・厚 (部品実装込みの最大厚)、音孔位置、ピン配列の向き
2. φ20 スピーカーの外径・厚・端子位置
3. `config.py` の `AUDIO_MIC_*` / `AUDIO_SPK_REAL_H` を更新 → `build_all.py` → `.venv/bin/python tools/check_audio.py` (38 項目)
4. 変更で `Mouth_Cannon_Bored / Mouth_Neck_Bored / Mouth_Ball_Bored / audio_cradle_mic / audio_cradle_spk` の STL が変わったかを MD5 で確認

## 完了条件 (DoD)
- [ ] 実測値と設計仮定の差をコメント
- [ ] config 更新の有無、check_audio の出力末尾 (OK)
- [ ] STL が変わった場合: **audio_cradle_mic (Walk_1 で印刷済) と Mouth_Neck_Bored (Blue_2 で印刷済) の再印刷要否**を明記 → {{{{PR-07}}}} に反映

## 注意
このイシューが終わるまで Mouth_Cannon/Ball_Bored は印刷しない ({{{{PR-05}}}} では除外)。
""")

issue("P-07", "P-07 [測定] カメラモジュール確認 (XIAO ESP32S3 Sense: センサー型番・子基板寸法・FOV) → CAM2_* 更新 → check_camera",
      parent="E1", milestone="M0 準備完了",
      labels=["type/タスク", "area/測定", "area/CAD", "並行作業OK", "prio/P1"],
      body=f"""
## ゴール
カメラ目 (`eye_pod_camera` + `camera_carrier`) は OV2640 子基板 20.5×12.5×5.54mm・FOV 68.7°・EFL 3.29mm
前提で設計されている ([BOM #34]({D}/BOM.md))。2026 年出荷品は **OV3660 の可能性が高く、レンズ違いで FOV が
64〜160° とばらつく**ため、届いた個体を確認してから印刷・接着に進む。

## 手順
1. 本体基板のシルク/箱でセンサー型番 (OV2640 / OV3660) を確認、子基板の実寸 (長・幅・厚, レンズ突出) をノギスで
2. OV3660 の場合: レンズ FOV/EFL をデータシートまたは実測 (既知寸法の対象を既知距離から撮影して画角を算出) で確認
3. 差があれば `config.py` の `CAM2_MODULE_*` / `CAM2_PUPIL_D` / `CAM2_LENS_STANDOFF` を更新 → `build_all.py` → `.venv/bin/python tools/check_camera.py` (瞳ケラレ [2]・収容 [3]・全アセンブリ視界 [4]・分割整合 [5])
4. XIAO にカメラストリーミングのファームを書き込み、USB 給電で静止画が取れることまで確認しておくと {{{{H-02}}}} が楽

## 完了条件 (DoD)
- [ ] センサー型番・子基板実寸・FOV の根拠をコメント
- [ ] check_camera の出力末尾 (OK)
- [ ] STL が変わった場合: eye_pod_camera shell/base (White プレートで印刷済) と camera_carrier (未印刷, {{{{PR-06}}}} 収載) の扱いを明記
""")

issue("P-08", "P-08 [運営] イシュー運用の立ち上げ (CONTRIBUTING / テンプレ / Project ボード / 協力者招待)",
      parent="E1", milestone="M0 準備完了",
      labels=["type/タスク", "area/運営", "prio/P1"],
      body=f"""
## ゴール
複数人が並行して作業できる状態にする。ファイル側 (CONTRIBUTING.md, `.github/ISSUE_TEMPLATE/`,
`docs/build_plan.md`, `tools/issues/`) は 2026-09-03 のセッションでローカル作成済み。

## オーナーがやること
- [x] 上記ファイルを `main` へコミット・push 済み (65fba4e, 2026-09-03)
- [x] `tools/issues/setup_project.sh` 実行済み → [Project #2「Tachikoma 物理製作」](https://github.com/users/hapx2yuki/projects/2) に全 72 件を投入
      (Status: Todo/Ready/In Progress/Blocked/Done、レーン = エピック、Board + Table ビュー)
- [x] Project の Visibility は Public (アカウント無しの人も URL で閲覧可)
- [x] Board ビューの Swimlane を「レーン」に設定済み (2026-09-03。UI: View → Swimlanes → レーン → Save view → 確認ダイアログの Save)
- [ ] Workflows で「Item closed → Done」「Item reopened → Todo」が ON か確認
- [ ] 協力者を Collaborator に招待 (Settings → Collaborators)。作業者は GitHub アカウント必須 (Assignee になるため)
- [ ] 最初の「Ready」なイシュー (依存なし: {{{{P-01}}}} {{{{P-02}}}} {{{{P-03}}}} {{{{P-06}}}} {{{{P-07}}}} {{{{EL-01}}}} {{{{EL-02}}}} {{{{EL-03}}}} {{{{EL-05}}}} {{{{EL-06}}}} {{{{PR-01}}}} {{{{PR-02}}}} {{{{PR-03}}}}) を周知。
      Cabin 組立 {{{{S-05}}}} は小物印刷 (PR-01〜03) が終わり次第ボランティア向けに解放

## 完了条件 (DoD)
- [ ] Project ボードの URL をコメント
- [ ] 協力者が自分でイシューを取って Assignee になれることを確認
""")


# ===========================================================================
# E2 印刷キュー
# ===========================================================================
_PRINT_COMMON = f"""
## 印刷の共通手順
1. `hardware/stl/<プレート>.3mf` を Bambu Studio で開く (キット STL は 3mf 内で 150% 済み)
2. **Sync info → Continue to sync filaments → フィラメント行の AMS 同期** の 3 段階でスロットを合わせる
3. 送信ダイアログで Main Nozzle → スロット対応を確認してから Send
4. 開始時にコメント: 「送信 (g / h, スロット)」。完了時: **ベッド上の写真** + 不良の有無
5. 取り外し後、[docs/print_manifest.md §1]({D}/print_manifest.md) の向き・壁・インフィルどおりだったか確認

## 完了条件 (DoD) 共通
- [ ] 全オブジェクトが欠けなく印刷され、反り・層剥離・糸引きが許容範囲
- [ ] 主要な嵌合部 (ポケット・穴・プラグ) にバリが無い
- [ ] 写真をコメントし、パーツを {{{{P-02}}}} の棚卸し箱へ (ラベル付き)
"""

issue("PR-01", "PR-01 [印刷] PLA_Matte_Gray_2 — キット灰意匠 21 種 36 個 (70g / 3.7h, A3 灰)",
      parent="E2", milestone="M3 サブアセンブリ",
      labels=["type/タスク", "area/印刷", "res/プリンタ", "skill/プリンタ操作", "prio/P1"],
      body=f"""
## 内容
Turret 左右 + Peg×2 / Mouth_Cap / Mouth_Key / Mouth_Peg / TailJoint_Ball / Shin_Guard ×4 /
爪ハブ Arm_Left_Claw_Grey ×2 / 指先 FingerTip ×6 / Arm Guard 左右 / Spinnarette ×4 /
Head_Dome / Head_Plug / Head_Screw ×2 / Cabin_Peg ×2 / Head_Peg 上下 / TailJoint_Peg ×2 (予備)。
**サーボ寸法に依存しない** → 今すぐ流せる (キューの先頭)。

## 使う先
{{{{A-01}}}} {{{{A-02}}}} (爪ハブ・指先・Guard) / {{{{H-05}}}} (Mouth Cap/Key/Peg) / {{{{S-01}}}} (TailJoint_Ball) /
{{{{S-02a}}}}〜{{{{S-02d}}}} (Shin_Guard) / {{{{S-03}}}} (Head 装飾) / {{{{S-05}}}} (Turret, Spinnarette, Peg)
{_PRINT_COMMON}
""")

issue("PR-02", "PR-02 [印刷] PLA_Black_1 — トゥ×12 + 指×6 + Insert×12 (34g / 2.2h, 黒へ差替)",
      parent="E2", milestone="M3 サブアセンブリ",
      labels=["type/タスク", "area/印刷", "res/プリンタ", "skill/プリンタ操作", "prio/P1"],
      body=f"""
## 内容
Leg_Toe_Black ×12 / Arm_Left_Finger_Black ×6 / Cabin_Front_Insert 6 種 (計 8 個) / Head_Insert ×4。
**3mf はスロット 1 (青) で出力されているので、Studio で右 Ext の PLA Basic 黒へ割当を差し替える**
(ファイル名に色を明示してある理由)。サーボ非依存 → 今すぐ流せる。

## 使う先
{{{{S-02a}}}}〜{{{{S-02d}}}} (トゥ) / {{{{A-01}}}} {{{{A-02}}}} (指) / {{{{S-03}}}} (Head_Insert) / {{{{S-05}}}} (Cabin Insert)
{_PRINT_COMMON}
""")

issue("PR-03", "PR-03 [印刷] PLA_Red_1 — 赤ランプ 大×4 小×4 (2.5g, 赤へ差替 or 白+赤塗装)",
      parent="E2", milestone="M4 フルドレス",
      labels=["type/タスク", "area/印刷", "res/プリンタ", "skill/プリンタ操作", "prio/P2"],
      body=f"""
## 内容
Cabin_RedLight_Large ×4 + Small ×4 (計 2.5g)。3mf はスロット 1 出力 → Lava Red (Polymaker, 購入済) を
AMS のどれかに一時装填して割当変更、または外部スプール。量が僅少なので**白で印刷して赤塗装**でも可
([filament.md]({D}/filament.md))。

## 使う先
{{{{S-05}}}} Cabin 組立
{_PRINT_COMMON}
""")

issue("PR-04", "PR-04 [印刷] PETG_Walk_3_Tibia — tibia_link ×2 + _m ×2 立て (~160g / ~9.8h, A4 PETG, 2026-09-04 ネック修正版)",
      parent="E2", milestone="M1 片脚 Go/No-Go",
      labels=["type/タスク", "area/印刷", "res/プリンタ", "skill/プリンタ操作", "prio/P0"],
      blocked_by=["P-04", "P-05"],
      body=f"""
## 内容
歩行チェーン (chassis→coxa→femur→**tibia**) の最後の未印刷部品。膝サーボのホーンポケットと
足ソケットを持つので、**サーボ実測 → config 確定 ({{{{P-04}}}}) 後に印刷** (2026-09-03 の判断で保留中)。
**2026-09-04 の膝ネック強度修正 (機構レビュー M-01)** が入った 3mf であること: 旧 45° ウェッジ×2 は
ネックを 11mm² (SF 0.1, 破断確実) まで痩せさせていた → femur 掃引領域の正確な減算 + 外側 3mm 増厚
(`check_leg_link_strength.py` SF 2.1, tibia 体積 32.4→34.3cm³, 質量 ≈+2g/本)。`PETG_Walk_3_Tibia.3mf` は
再生成済み (2026-09-04)。**それ以前の 3mf/印刷物は使用不可**。

## 使う先
{{{{L-01}}}} (FL) / {{{{L-03}}}} (FR, `_m`) / {{{{L-04}}}} (RL, `_m`) / {{{{L-05}}}} (RR)

## 注意
- 立てて印刷 (向きは 3mf に焼き込み済み)。壁 4 / 40%
- 取り外し後すぐ**標準 / `_m` をマーキング** (形状はミラーなので見分けにくい)
{_PRINT_COMMON}
""")

issue("PR-05", "PR-05 [印刷] PLA_Matte_Gray — leg_foot_bored ×4 + thigh_cap ×4 (Mouth 2 点は除外, A3 灰)",
      parent="E2", milestone="M1 片脚 Go/No-Go",
      labels=["type/タスク", "area/印刷", "res/プリンタ", "skill/プリンタ操作", "prio/P0"],
      blocked_by=["P-04"],
      body=f"""
## 内容
`PLA_Matte_Gray.3mf` (47g / 2.5h) のうち **leg_foot_bored ×4 + thigh_cap ×4** を印刷する。
同プレートの **Mouth_Ball_Bored / Mouth_Cannon_Bored は音声実測 ({{{{P-06}}}}) が終わるまで Studio 上で
削除して除外** → 別途 {{{{PR-07}}}}。
thigh_cap は femur に被せる意匠なので config 確定 ({{{{P-04}}}}) 後。leg_foot_bored は tibia ソケット互換。

## 使う先
{{{{L-01}}}} 〜 {{{{L-05}}}} (足) / {{{{S-02a}}}}〜{{{{S-02d}}}} (thigh_cap)

## 注意
- leg_foot_bored は**プラグ側 (tibia 差込面) を下** (3mf 焼き込み済み)。手元の旧 rev ×2 とは混ぜない
- P-06 で Mouth の STL が変わらなかった場合は、このプレートに Mouth を残して一緒に印刷して {{{{PR-07}}}} を Close してよい
{_PRINT_COMMON}
""")

issue("PR-06", "PR-06 [印刷] PETG_Walk_4_Rest — pod_neck + 腕骨格 ×2 + eye_carrier ×2 + camera_carrier + claw_mount ×2 + spk (84g / 5.2h)",
      parent="E2", milestone="M3 サブアセンブリ",
      labels=["type/タスク", "area/印刷", "res/プリンタ", "skill/プリンタ操作", "prio/P1"],
      blocked_by=["P-04"],
      body=f"""
## 内容
残り PETG の一括プレート (2026-08-31 作成): pod_neck / shoulder_bracket + `_L` / upper_arm + `_L` /
forearm + `_L` / eye_carrier ×2 / camera_carrier / claw_mount + `_L` / audio_cradle_spk。
MG90S (肩・上腕) と ES9251II (eye_carrier) のポケットを持つので **config 確定 ({{{{P-04}}}}) 後**。

## 除外・調整
- `claw_mount_L` は先行印刷済み → 手元にある方を Studio で削除
- `camera_carrier` は {{{{P-07}}}} でカメラ寸法が変わる可能性 → P-07 未完なら削除して後で単体印刷
- pod_neck は 90° 回転で平置き (3mf 焼き込み済み)

## 使う先
{{{{A-01}}}} {{{{A-02}}}} (腕) / {{{{H-01}}}} (eye_carrier) / {{{{H-02}}}} (camera_carrier) / {{{{H-03}}}} (spk ワッシャ) / {{{{S-01}}}} (pod_neck)
{_PRINT_COMMON}
""")

issue("PR-07", "PR-07 [印刷] Mouth_Cannon_Bored + Mouth_Ball_Bored (+ Neck / mic クレードルは変更時のみ)",
      parent="E2", milestone="M3 サブアセンブリ",
      labels=["type/タスク", "area/印刷", "res/プリンタ", "skill/プリンタ操作", "prio/P1"],
      blocked_by=["P-06"],
      body=f"""
## 内容
砲身 (Cannon, 灰 9g) と Ball (灰 4g) の内部ボーリング版。{{{{P-06}}}} の実測で STL が変わっていれば
再生成後の 3mf (`make_plates.py PLA_Matte_Gray`) から、変わっていなければ {{{{PR-05}}}} と同時でよい。
P-06 で `Mouth_Neck_Bored` (青, Blue_2 で印刷済) / `audio_cradle_mic` (Walk_1 で印刷済) も変わった場合はそれも含める。

## 注意
- Cannon は**砲口を上** (印刷面より先端が出ないよう向き現物合わせ、[print_manifest §1]({D}/print_manifest.md))
- `audio_cradle_mic` はレイヤー 0.12 指定 (`PETG_5_Mic.3mf`)。Walk_1 同乗分 (0.2) で圧入が粗ければここで刷り直す

## 使う先
{{{{H-03}}}} 音声ユニット組込
{_PRINT_COMMON}
""")

issue("PR-08", "PR-08 [印刷] Head_Top_Eyecut v2 単体 (青 64g) — v1 が印刷済みなら差し替え",
      parent="E2", milestone="M4 フルドレス",
      labels=["type/タスク", "area/印刷", "res/プリンタ", "skill/プリンタ操作", "prio/P1"],
      blocked_by=["P-02"],
      body=f"""
## 内容
`hardware/stl/Head_Top_Eyecut.stl` **v2 (2026-08-22: 内殻ホロー 306→52cm³ + スカート切欠き ×6)** を
単体で印刷する。Blue_1 (8/20) に含まれていた v1 は中実で、脚/腕サーボケース・PCA スタックと干渉して
**頭が被せられない** ([printing.md]({D}/printing.md) 表の注記)。
{{{{P-02}}}} で「v2 を印刷済み」と確認できればこのイシューは Close。

## 設定
PLA 青 (A1) / 壁 2 / 8% / STL のまま (外観面が上)。内部天井は 45° コーンで彫り止めしてあり内部サポート原則不要。
スカート切欠き天面 (~24×46mm ×4) はブリッジ — 不可視なので垂れは許容、気になるなら内部のみ tree サポート。
`build_all.py` の対象外 (`tools/make_head_eyecut.py` で生成) なので、{{{{P-04}}}} で目ソケット系の値を変えていたら再生成してから。

## 使う先
{{{{S-03}}}} 頭部組立
{_PRINT_COMMON}
""")

issue("PR-09", "PR-09 [印刷] (条件付き) 骨格の再印刷 — P-05 で「再印刷」と決まった部品のみ",
      parent="E2", milestone="M1 片脚 Go/No-Go",
      labels=["type/タスク", "area/印刷", "res/プリンタ", "skill/プリンタ操作", "prio/P0"],
      blocked_by=["P-05"],
      body=f"""
## 内容
{{{{P-05}}}} の決定に従い、chassis (`PETG_Walk_1`, 66g/3.7h) / coxa×4+femur×4 (`PETG_Walk_2`, 210g/13.8h) /
arm_pod ×4 (`PLA_Matte_Blue_2`) などのうち**再印刷対象だけ**を、再生成済み 3mf から印刷する
(不要オブジェクトは Studio で削除)。P-05 が「変更なし / そのまま使う」なら **何もせず Close**。

## 使う先
{{{{L-01}}}} 〜 {{{{L-07}}}}
{_PRINT_COMMON}
""")


# ===========================================================================
# E3 電装・電源・ファーム
# ===========================================================================
issue("PR-10", "PR-10 [印刷] eye_pod_camera_base 再印刷 (白 5g) — 印刷済み品は旧 rev (ポケット壁 ~1mm 違い)",
      parent="E2", milestone="M3 サブアセンブリ",
      labels=["type/タスク", "area/印刷", "res/プリンタ", "skill/プリンタ操作", "prio/P1"],
      body=f"""
## 内容
`PLA_Matte_White_3_CamBase.3mf` (2026-09-04 新設, 1 個 / ~5g / ~20 分) を印刷する。
印刷済みの eye_pod_camera_base は現行 STL とポケット壁が y 方向に ~1mm 違う旧 rev (体積 3.70→3.63cm³) で、
camera_carrier ({{{{PR-06}}}}) との嵌合が保証できない。eye_pod_camera_shell / eye_pod ×2 (同プレート印刷済み) は現行と一致しているのでそのまま使う。

## 使う先
{{{{H-02}}}}

## 注意
- 旧品は「旧 rev」ラベルで隔離 ({{{{P-02}}}})
{_PRINT_COMMON}
""")

issue("EL-01", "EL-01 [電装] 電源系ベンチ組立 (LiPo→ヒューズ→SW→UBEC 6V / DC-DC 5V, バスバー, コンデンサ, 分圧)",
      parent="E3", milestone="M0 準備完了",
      labels=["type/タスク", "area/電装", "skill/はんだ", "並行作業OK", "prio/P0"],
      body=f"""
## ゴール
[wiring.md「電源系統」]({D}/wiring.md) のとおりに電源ユニットを作り、無負荷で 6.0V / 5.0V が出ることを確認する。

```
2S LiPo 7.4V ─[15Aヒューズ]─[ロッカーSW]─┬─ UBEC 6V/10A ══(AWG16)══→ サーボ電源バス (+1000µF×2)
 (2200mAh 35C, XT60)                      └─ DC-DC 5V/3A ──→ ESP32 VIN / DFPlayer / 74AHCT125 / WS2812 (+470µF)
```

## 手順
1. XT60 → インラインヒューズ (15A) → ロッカー SW を AWG16 で
2. UBEC 入力を分岐、出力を 6V に設定 (HENGE は 5/6V 切替) → AWG16 バスバー (または太線ハブ)。1000µF/16V ×2 をバス直近に
3. DC-DC (mini560 等) を 5V に調整 → 470µF/10V
4. VBAT 監視の分圧 100kΩ / 33kΩ (8.4V→2.08V) を ESP32 GPIO34 用に用意
5. 通電: **サーボ未接続で** 各レール電圧をテスタ実測

## 完了条件 (DoD)
- [ ] 6V レール・5V レールの実測電圧 (無負荷) をコメント
- [ ] 配線写真 (ヒューズ・SW・UBEC・DC-DC・コンデンサが見える)
- [ ] UBEC の型式と入力下限電圧を記録 (HENGE 8A は 7V — 2S 末期の挙動は {{{{L-10}}}} で観察)
- [ ] **UBEC 出力を 6.0V に設定した証拠** (切替ジャンパ/スイッチの写真 + サーボ未接続でのテスタ実測)。7.4/8.4V のまま
  サーボを繋ぐと DS3218 (〜6.8V) を壊す ([wiring.md「電源系統」]({D}/wiring.md), 2026-09-04 E-01)
- [ ] 74AHCT125 の 1OE (pin1) を GND、未使用 OE を VCC に結線 (E-02)

## 注意 (2026-09-04 S-03)
歩行時のサーボ電流は静力学見積りで **9〜14A 級 = UBEC 10A 連続定格と同水準**。{{{{L-10}}}} でクランプメータ実測し、
8A 超が続くなら {{{{EL-09}}}} で 15〜20A 級への変更を判断する。ロッカー SW も 15〜20A 級を推奨 (BOM #12)。

## 注意
LiPo の取り扱い (充電は LiPo バッグ内、短絡注意)。バッテリーは最終的に `battery_cradle` (プレート下面) に入るので、幹線長はコネクタ前向きで届く長さに。
""")

issue("EL-02", "EL-02 [電装] PCA9685 ×2 準備 (board1 A0 ジャンパ→0x41, ヘッダ実装規約) + ESP32 I2C 疎通",
      parent="E3", milestone="M0 準備完了",
      labels=["type/タスク", "area/電装", "skill/はんだ", "並行作業OK", "prio/P0"],
      body=f"""
## ゴール
2 枚の PCA9685 を firmware の想定 (board0=0x40 脚+頭 / board1=0x41 腕+目) に合わせて準備し、
ESP32 から両方が I2C で見えることを確認する ([wiring.md「PCA9685 チャンネル割当」「スタック実装の向きルール」]({D}/wiring.md))。

## 手順
1. board1 の **A0 ジャンパをはんだブリッジ** → 0x41
2. ヘッダ実装規約 (Head_Top 内クリアランスの前提。守らないと頭が被らない):
   - サーボプラグ列 (3 ピン×16) は両ボード実装、**ボードを載せたとき +x (右) 側に向く**
   - **I2C ヘッダは北端 (ch0 側) のみ実装** (南端の直立ピンは上下段間 16mm に収まらない)
   - **V+ 端子台は未接続** (サーボ電源はバス直結)。GND は共通
3. board0 に M2.5×16 M-F スタンドオフ ×4 を通す段取り (オス側がシャーシボスへセルフタップ) — 部品は {{{{P-01}}}} で確認
4. ESP32 (GPIO21 SDA / GPIO22 SCL) に両ボードを接続し、I2C スキャンで 0x40 / 0x41 を確認 (firmware 起動ログ、または簡単なスキャンスケッチ)

## 完了条件 (DoD)
- [ ] I2C スキャン結果 (0x40, 0x41) のログをコメント
- [ ] 両ボードの表裏写真 (ジャンパ・ヘッダの向きが分かる)
""")

issue("EL-03", "EL-03 [ファーム] CALIBRATION_MODE ビルド + ESP32 書き込み + AP 接続確認",
      parent="E3", milestone="M0 準備完了",
      labels=["type/タスク", "area/ファーム", "skill/ファーム", "並行作業OK", "prio/P0"],
      body=f"""
## ゴール
サーボ中立化 ({{{{EL-04}}}}) に使う `CALIBRATION_MODE` ビルド (全 ch 1500µs) を ESP32 に書き込み、
WiFi AP `Tachikoma` に接続して Web UI (http://192.168.4.1/) が開けることを確認する。

## 手順
```bash
cd firmware
# platformio.ini の build_flags で -DCALIBRATION_MODE のコメントアウトを外す (L20 付近)
# config.h の AP_PASS をローカルで実値に戻す (リポジトリ上はプレースホルダ "change-me-8chars")
../.venv/bin/pio run -t upload   # PATH に pio は無い。USB 接続して実行
../.venv/bin/pio device monitor   # 起動ログ (I2C 検出、AP 起動)
```

## 完了条件 (DoD)
- [ ] ビルド SUCCESS と Flash 使用率をコメント
- [ ] シリアル起動ログ (PCA 検出 / AP 起動) を貼る
- [ ] スマホで AP に接続し Web UI が表示されたスクリーンショット

## 注意
- **AP_PASS の実値は絶対にコミットしない** (`git diff` で確認してから作業終了)
- CALIBRATION_MODE は中立出力のみ。通常ビルドへ戻すのは {{{{EL-07}}}}
""")

issue("EL-04", "EL-04 [電装] 全サーボの中立化 (1500µs) とマーキング (STD ×14 / MG90S ×6 / ES9251II ×3)",
      parent="E3", milestone="M0 準備完了",
      labels=["type/タスク", "area/電装", "prio/P0"],
      blocked_by=["EL-01", "EL-02", "EL-03"],
      body=f"""
## ゴール
組付け前に**全サーボを中立 (1500µs) にして出力軸の位相をマーキング**する ([assembly.md §1-1]({D}/assembly.md))。
組付け後に中立が狂っていると、ホーン共締めをやり直すことになる。

## 手順
1. {{{{EL-01}}}} の 6V バス + {{{{EL-02}}}} の PCA + {{{{EL-03}}}} の CALIBRATION_MODE ESP32 をベンチ接続
2. サーボを 1 本ずつ (脚 STD は電流が大きいので 1 本ずつ) 任意 ch に挿し、中立で静止したら出力軸とケースに油性ペンで合いマーク
3. 個体番号を付けて記録 (脚 12 + 予備 2 / 腕 6 / 目 2 + 予備 1)。初期不良 (ジッタ・異音・回らない) を除外

## 完了条件 (DoD)
- [ ] 全数の中立マーク写真 (まとめて 1 枚で可)
- [ ] 個体番号と用途 (FR ヨー/…) の対応表、不良品の有無
- [ ] 中立保持時の 1 本あたり電流 (STD) を参考値として記録
- [ ] **可動端の確認**: `/cal?us=500` / `/cal?us=2500` で両端を出し、STD が ±90° (180° 品) か ±135° (270° 品) かを記録
  (firmware は 500-2500µs=180° 前提。270° 品なら `US_MIN/US_MAX/DEG_RANGE` を要変更, 2026-09-04 E-06)
""")

issue("EL-05", "EL-05 [配線] 脚サーボハーネス ×12 製作 (電源 AWG20→AWG16 バス分岐 / 信号 AWG26→PCA)",
      parent="E3", milestone="M2 頭無し歩行",
      labels=["type/タスク", "area/配線", "skill/はんだ", "並行作業OK", "prio/P1"],
      body=f"""
## ゴール
脚 12 サーボ分の延長ハーネスを作る。**電源線はサーボ直近でバスへ分岐、PCA には信号+GND のみ**
([wiring.md「脚サーボの配線経路」]({D}/wiring.md))。

## 長さの目安 (最終は本体で調整するので余裕長で)
- 膝サーボ線: femur ウェブ内側 → 股 → coxa 箱枠の配線逃がし → シャーシ φ9 穴 ((±16,-6) の 2 穴)
- **可動域全域 (股ピッチ -45〜+55°, 膝 ±44°, ヨー ±40°) で張らない長さ**
- 3 本 (ヨー/ピッチ/膝) をスパイラルチューブで束ねる前提

## 完了条件 (DoD)
- [ ] 12 本 (+予備) の写真、線種 (AWG16/20/26) と長さの表
- [ ] 導通・極性チェック済み (サーボコネクタの信号/電源/GND の並び)
""")

issue("EL-06", "EL-06 [配線] 腕・目サーボハーネス製作 (MG90S ×6 / ES9251II ×2, AWG26/30)",
      parent="E3", milestone="M3 サブアセンブリ",
      labels=["type/タスク", "area/配線", "skill/はんだ", "並行作業OK", "prio/P1"],
      body=f"""
## ゴール
腕 6 + 目 2 のハーネス。電流が小さいので 6V バス直結で統一 (board1 の V+ 経由でも可だが統一推奨)。
[wiring.md「腕サーボの配線経路」]({D}/wiring.md): 肘 → upper_arm フレーム内側 → 肩ブラケット背面 → シャーシ MICRO 開口 → プレート上面。
腕の全可動域 (ヨー ±15° / ピッチ -45〜85° / 肘 0-95°) で張らない長さ、束ねはヨー根本を避けシャーシ前縁で 1 点固定。
目サーボ線はケース後端から接線方向 → シャーシ 7 タブ間隙間から胴へ。

## 完了条件 (DoD)
- [ ] 8 本の写真と長さ表、導通チェック
""")

issue("EL-07", "EL-07 [ファーム] 本番ビルド (CALIBRATION_MODE 解除, JOINT_SIGN/ARM_SIGN 確認) + 書き込み + Web UI 動作",
      parent="E3", milestone="M2 頭無し歩行",
      labels=["type/タスク", "area/ファーム", "skill/ファーム", "prio/P0"],
      blocked_by=["EL-03", "P-04"],
      body=f"""
## ゴール
通常動作の firmware を書き込み、Web UI から歩行/腕/目/トリム/WiFi 設定の各画面が操作できることをベンチで確認する。
{{{{P-04}}}} で `config.h` の定数 (ARM_MOUNT 系など) が変わっていればそれを含める。

## 手順
1. `-DCALIBRATION_MODE` を外す、AP_PASS をローカル実値に (コミット禁止)
2. `config.h` の `JOINT_SIGN` (FR/RL のミラー脚はピッチ/膝 -1) / `ARM_SIGN` / `PCA_CH` / `ARM_CH` を wiring.md と突合。
   2026-09-04 の変更 (脚スルーレート `LEG_SLEW_DPS 240`, WDT 3s, `STANCE_OFF_Y` / `SWAY_MM[4]` / `BODY_H_MIN 110` /
   `LIM_YAW_POD 30`) が入っていること (`sim_gait.py` 全 OK を貼る)
3. `../.venv/bin/pio run -t upload` → 起動ログ → Web UI
4. ベンチ: 任意のサーボ 1 本を挿して歩行スライダ / 腕プリセット / 目モード / Trim が反映されること

## 完了条件 (DoD)
- [ ] ビルド SUCCESS ログ、Web UI 各タブのスクリーンショット
- [ ] 低電圧保護 (6.8V 警告 / 6.4V×3s 脱力) の閾値が config.h にあることを確認
""")

issue("EL-08", "EL-08 [電装] WS2812 ×12 + 74AHCT125 + DFPlayer (+microSD 音源) のベンチ配線・点灯/再生確認",
      parent="E3", milestone="M4 フルドレス",
      labels=["type/タスク", "area/電装", "skill/はんだ", "並行作業OK", "prio/P2"],
      blocked_by=["EL-07"],
      body=f"""
## ゴール
演出系 (LED / サウンド) をベンチで動作確認しておく。本体組込は {{{{S-07}}}}。

## 手順 ([wiring.md「信号系統」「WS2812B 直列順」「DFPlayer」]({D}/wiring.md))
- GPIO4 → 74AHCT125 A1 → Y1 → (330Ω) → WS2812 DIN。VCC は 5V。直列順: 0 メインアイ → 1-3 頭部目 (2 は未使用可) → 4-7 赤ランプ大 → 8-11 赤ランプ小
- DFPlayer: GPIO16 RX2 ← TX、GPIO17 TX2 → RX に **1kΩ 直列**。microSD (≤32GB FAT32) に `/mp3/0001.mp3` (起動音) 等を配置。スピーカーは SPK1/SPK2 直結
- LED は最終的に各シェル裏へ分散するので、リード線を長めに (現物合わせ)

## 完了条件 (DoD)
- [ ] 12 灯全点灯の写真、起動音再生の動画
- [ ] 5V レール電流 (全灯時) の実測値
""")


# ===========================================================================
# E4 脚・歩行
# ===========================================================================
_LEG_STEPS = f"""
## 手順 ([assembly.md §1]({D}/assembly.md))
1. 膝サーボ (中立マーク済) を **femur_link の箱枠へ +Y 側から挿入**、M3×10 タッピング ×4 でタブ固定 (ケース底が -Y へ突き出るのは仕様)
2. 股ピッチサーボを coxa_bracket の箱枠へ挿入、同様にタブ固定
3. tibia_link の円板ポケットに付属ホーンを埋め **M2.6 タッピングで共締め** → ホーンを膝サーボ軸へ (tibia 鉛直下向き = 中立)、中心ビスで固定
4. femur のホーン円板を股ピッチサーボへ (femur 水平 = 中立)
5. leg_foot_bored を tibia 先端ソケットへ差し込み**接着** (抜け止めスナップは無い) → 隠しポケットへ foot_pad (TPU) を圧入接着 → Leg_Toe は {{{{S-02a}}}} 系で後付け
6. 手でスイープし干渉・ガタ・ホーン緩みを確認

## 完了条件 (DoD)
- [ ] 完成写真 (側面・ホーン部クローズアップ)
- [ ] 股ピッチ -45〜+55° / 膝 ±44° を手で動かして干渉なし
- [ ] 使ったサーボの個体番号を記録 ({{{{EL-04}}}} の表と対応)
"""

issue("L-01", "L-01 [組立] 脚 FL (標準版) を組む — 片脚ゲート用の 1 本目",
      parent="E4", milestone="M1 片脚 Go/No-Go",
      labels=["type/タスク", "area/組立", "prio/P0"],
      blocked_by=["PR-04", "PR-05", "EL-04", "P-05"],
      body=f"""
## ゴール
Go/No-Go 判定 ({{{{L-02}}}}) に使う最初の 1 本。**標準版** (coxa_bracket / femur_link / tibia_link, `_m` ではない) を使う。
{_LEG_STEPS}
""")

issue("L-02", "L-02 [ゲート] 片脚ベンチ試験 — 1.2kg×レバー155mm (=18.6kgf·cm) 保持 (6V) / スイープ / 保持電流",
      parent="E4", milestone="M1 片脚 Go/No-Go",
      labels=["type/ゲート", "area/試験", "area/測定", "prio/P0"],
      blocked_by=["L-01", "EL-01", "EL-03"],
      body=f"""
## 判定基準 ([assembly.md §1-7]({D}/assembly.md))
coxa をバイスで水平固定し、6V 給電で:
- [ ] スイープ動作で干渉・ガタ・ホーン緩みなし
- [ ] **股ピッチ軸から足先まで水平 155mm** の姿勢で足先に **1.2kg の錘** → 股ピッチ ≈ **18.6 kgf·cm** を 10 秒保持
  (脱調・振動・ホーン滑りなし)。`sim_gait.py` [3] の全域最悪 18.5 kgf·cm (総重量 3.0kg・実重心 y=-39mm・
  3/4 点支持静力学, 2026-09-04) と同等の負荷。**レバー長を必ず写真で残す** (旧「1.2kg (40%)」はレバー未指定で不定だった)
- [ ] 保持電流を記録: 期待 1.5〜2.2A/サーボ (DS3218 ストール 2.2〜2.6A)。**2.5A 超 or 保持不能 → No-Go**
- [ ] 膝ネック増厚部 (tibia 外側 +3mm, 2026-09-04 M-01) に白化・割れなし
- [ ] 6V 給電での実測ストールトルク (バネ秤, 参考) — DS3218 系の定格は出典で 18〜21.5 kgf·cm とばらつく (S-06)

## Go の場合
{{{{L-03}}}} {{{{L-04}}}} {{{{L-05}}}} を解放 (3 人並行可)。

## No-Go の場合 (持ち上がらない / 電流過大 / ホーンが滑る)
**全数印刷へ進まない。** 原因を `type/不具合` イシューに切り分け (サーボ実力不足 / ホーン結合 / リンク長 / 電圧降下) →
設計値見直し → `config.py` → `build_all.py` → 再テスト印刷 のループ。設計側の想定トルクは**股ピッチ最悪 18.5 kgf·cm**
(2026-09-04 全域再計算。旧 8.9 は 1 点評価) で DS3218 の 6V 実力 (~18) と同水準 = **余裕ほぼゼロ**。サーボ実力不足なら
{{{{L-11}}}} (7.4V 給電 / 高トルク品 / 低トルク歩容) をオーナー判断。

## 証拠
リフト動画、電流計の読み (写真)、錘の重量。
""")

for _k, _n, _v, _st in (("L-03", "FR", "ミラー版 `_m`", "coxa_bracket_m / femur_link_m / tibia_link_m"),
                        ("L-04", "RL", "ミラー版 `_m`", "coxa_bracket_m / femur_link_m / tibia_link_m"),
                        ("L-05", "RR", "標準版", "coxa_bracket / femur_link / tibia_link")):
    issue(_k, f"{_k} [組立] 脚 {_n} ({_v}) を組む",
          parent="E4", milestone="M2 頭無し歩行",
          labels=["type/タスク", "area/組立", "並行作業OK", "prio/P0"],
          blocked_by=["L-02"],
          body=f"""
## ゴール
{_n} 脚。**{_v}** ({_st}) を使う — 取り違えると 45° ペアの股ピッチサーボ同士が干渉して組めない。
ゲート ({{{{L-02}}}}) 合格後、残り 3 脚は 3 人で並行して組める。
{_LEG_STEPS}
""")

issue("L-06", "L-06 [組立] シャーシ電装組付け (ヨーサーボ ×4 / PCA スタック / ESP32 / UBEC / DC-DC / SW / battery_cradle)",
      parent="E4", milestone="M2 頭無し歩行",
      labels=["type/タスク", "area/組立", "area/電装", "res/本体", "prio/P0"],
      blocked_by=["EL-01", "EL-02", "EL-04", "P-05"],
      body=f"""
## ゴール
chassis (印刷済, {{{{P-05}}}} で使用可と判断されたもの) に電装を載せ、脚を受けられる状態にする
([assembly.md §2-1, §2-4]({D}/assembly.md))。脚ゲートと**並行**して進められる。

## 手順
1. **ヨーサーボ ×4 をシャーシ上面から挿入**: ケース長手は 4 個とも **X 軸平行・ボディ中央向き**。タブは台座ボス (h3) に着座、M3×10 で固定。ギヤヘッドはプレート下面へ突き出る
2. **PCA9685 スタック** (`config.py PCA_*` が正): board0 (0x40) を中央縦向きボスへ M2.5×16 M-F スタンドオフ ×4 で (オス側がボスへセルフタップ、六角肩が board0 を押さえる) → board1 (0x41) をスタンドオフ上へ M2.5×6 ×4。**両ボード ch0 側を北 (+y, 頭正面)**、プラグ列 +x、board1 南端 ~4ch は空ける
3. **ESP32 はテープ/マジックテープ留め** (ネジ止めボスは撤去済み。頭内の恒久マウントは不成立 → {{{{H-06}}}})
4. UBEC / DC-DC は後方 (±30,-58) に両面テープ、SW + ヒューズは右前 (48,28) 付近 (Head_Top 内面と 0.11cm³ 重なる見込み → 数 mm 内側へ寄せる)
5. **battery_cradle をプレート下面へ** (M3 タッピング ×4)、バッテリーは -Y 側から差し込みベルクロ固定、コネクタ前向き
6. 配線通し穴 φ9 ×2 ((±16,-6)) と中央菱形の穴を確認。**実ハーネス (脚 8 本分 + バッテリー線) を実際に通してみる**
   (概算では収まるが実ケーブル外径は未確認, 2026-09-04 M-09)。詰まるなら `type/不具合` で φ10〜11 拡口を検討

## 完了条件 (DoD)
- [ ] 上面・下面の写真 (サーボ向き・スタック向き・プラグ列が分かる)
- [ ] ヨーサーボ 4 本の個体番号と ch (FR0 / FL3 / RL6 / RR9) の対応
- [ ] スタックのヘッダ規約 (ch0 北 / プラグ +x / I2C 北端のみ) を満たしている
""")

issue("L-07", "L-07 [組立] 4 脚をシャーシへ取付 (ヨーホーン共締め, 方位 FR15/FL165/RL210/RR330)",
      parent="E4", milestone="M2 頭無し歩行",
      labels=["type/タスク", "area/組立", "res/本体", "prio/P0"],
      blocked_by=["L-01", "L-03", "L-04", "L-05", "L-06"],
      body=f"""
## 手順 ([assembly.md §2-2]({D}/assembly.md))
1. ヨーホーンを coxa 天板ポケットへ埋めて M2.6 共締め
2. ヨーサーボ中立 (マーク) で、各脚を**中立姿勢**でサーボ軸へ固定 (中心ビス)。脚方位 (正面=90°): **FR 15° / FL 165° / RL 210° / RR 330°** (前脚は正面から ±75°、後脚は ±120°)
3. **FR と RL はミラー版 `_m`** の脚を付ける
4. 隣接脚 (45° ペア FR-RR / FL-RL) を手で内側へ寄せて、股ピッチサーボの張り出しが当たらないことを確認

## 完了条件 (DoD)
- [ ] 上面からの写真 (4 脚の方位が分かる) と各脚のマーキング (標準/`_m`) が写った写真
- [ ] 4 脚とも中立で床に均等接地 (ガタ・傾きなし)
""")

issue("L-08", "L-08 [配線] 脚 12ch 本体配線 (経路・束ね・バス直結・PCA ch 割当)",
      parent="E4", milestone="M2 頭無し歩行",
      labels=["type/タスク", "area/配線", "skill/はんだ", "res/本体", "prio/P0"],
      blocked_by=["L-07", "EL-05"],
      body=f"""
## 手順 ([wiring.md「脚サーボの配線経路」「board0 割当」]({D}/wiring.md))
1. 膝線を femur ウェブ内側へ、股ピッチ線を coxa 箱枠の逃がしから上へ、3 本をスパイラルチューブで束ね、φ9 穴から内部へ
2. **可動域全域で張らない**ことを脚を動かして確認してから固定
3. 電源はサーボ直近で AWG16 バスへ、信号+GND を board0 へ: FR 0/1/2, FL 3/4/5, RL 6/7/8, RR 9/10/11 (ヨー/ピッチ/膝)
4. バッテリー線も同じ穴経由

## 完了条件 (DoD)
- [ ] ch 対応表 (脚×関節→ch→サーボ個体番号) をコメント
- [ ] 配線写真 (束ねと穴通し)。全脚を可動域端まで動かして張り・擦れなし
""")

issue("L-09", "L-09 [試験] 通電・中立確認・トリム (サーボ未接続電圧 → 1 本 → 全数 → Web UI Trim)",
      parent="E4", milestone="M2 頭無し歩行",
      labels=["type/タスク", "area/試験", "res/本体", "prio/P0"],
      blocked_by=["L-08", "EL-07"],
      body=f"""
## 手順 ([assembly.md §2-6, §4-1/2]({D}/assembly.md))
1. **サーボ全て外した状態**で電源 ON → 6V / 5V 実測
2. サーボ 1 本だけ接続 → 中立 → 全数接続
3. Web UI の Trim で脚 12ch のセンター微調整 (足先が正方形の四隅に来る)
4. **逆に動く関節があれば `config.h` の `JOINT_SIGN` を反転** (FR/RL はミラー脚なのでピッチ/膝の初期値 -1。標準脚と鏡向きに動くのが正)
5. 立位 (体高 115) で静止させ、全サーボ保持電流を記録 (6V バス合計をクランプメータで)

## 完了条件 (DoD)
- [ ] 各段階の電圧・電流の実測値
- [ ] Trim 値と JOINT_SIGN の最終値 (config.h の diff)
- [ ] 立位写真
""")

issue("L-10", "L-10 [ゲート] 頭無し歩行試験 — 立位→前進→旋回→停止, 転倒なし, 電流・電圧ログ",
      parent="E4", milestone="M2 頭無し歩行",
      labels=["type/ゲート", "area/試験", "area/測定", "prio/P0"],
      blocked_by=["L-09"],
      body=f"""
## 判定基準
物理シム (MuJoCo, `docs/vis_physics_walk.mp4`) では転倒なしで成立。実機で:
- [ ] 体高 115mm・歩幅 50% で前進 → 旋回 → 停止が**転倒なし** (フローリング等平坦面)
- [ ] 歩行中の 6V レール電流 (クランプメータ, ピーク/平均)・バッテリー電圧を記録 (UBEC 連続 10A に対し見積り 9〜14A —
  8A 超が続くなら {{{{EL-09}}}}。入力下限電圧によるブラウンアウトが出ないか)
- [ ] `config.h` が 2026-09-04 の歩容定数 (`STANCE_OFF_Y -30` / `SWAY_MM {{34,34,40,40}}` / `BODY_H_MIN 110` /
  `LIM_YAW_POD 30`) を含むビルドであること (`sim_gait.py` が突合)
- [ ] 後脚が最もポッド側へ寄る位相 (旋回) で pod_neck / バッテリーと擦れない (設計 28.9°/30°, 接触開始 34°)
- [ ] 装飾トゥ装着前でよいが、体高 110 の極端姿勢で foot_pad より先にトゥが接地しないか (S-04) を後で S-02 系で再確認
- [ ] 低電圧保護 (6.8V 警告 / 6.4V 脱力) が誤動作しない
- [ ] フローリングで滑る場合は foot_pad の接地径・硬度を検討 (別イシュー)

## Go の場合
意匠シェル ({{{{S-02a}}}} 系, {{{{S-03}}}}) と腕吊り下げ ({{{{A-03}}}}) を解放。

## No-Go の場合
症状 (転倒方向・脱調ch・電圧ドロップ) を `type/不具合` に切る。歩容定数は `config.py`/`config.h` で `sim_gait.py` と突合しながら調整。

## 証拠
歩行動画 (側面+上面)、電流・電圧のログまたは写真。
""")


# ===========================================================================
# E5 腕
# ===========================================================================
_ARM_STEPS = f"""
## 手順 ([assembly.md §2.5-2〜6]({D}/assembly.md))
1. 肩ピッチ用 MG90S を shoulder_bracket へタブ固定 (M2×8 ×2)。shoulder_bracket **上面**のホーンポケットに付属ホーンを埋め M2.6 共締め (肩ヨー軸へ吊る用 — 吊り下げ自体は {{{{A-03}}}})
2. upper_arm のホーン円板を肩ピッチサーボへ (**腕鉛直下向き = ピッチ 85° 付近** が中立ではない点に注意: 0° = 上腕が中立ヨー方向に水平)。肘用 MG90S を upper_arm の箱枠へタブ固定
3. forearm のホーン円板を肘サーボへ (**伸ばし = 肘 0°**、サーボ中立は 45° 曲げ)
4. **固定爪**: claw_mount の背面を forearm の手首面へ接着 (瞬着/エポキシ) → 爪ハブ `Arm_Left_Claw_Grey` の平坦近位面を claw_mount 前面へ突き合わせ接着 (軽く均す。**接着面を #400 で粗面化しエポキシ**推奨 — 機械的キーが無い接合なので瞬着より靱性の高い接着剤で, 2026-09-04 M-03) → `Arm_Left_Finger_Black` ×3 をハブの 3 ペグへ圧入+接着 → `Arm_Left_FingerTip_Grey` ×3 を指根元へ接着
5. ベンチで肘 0-95°、ピッチ -45〜85° を手で動かし、シェル無しの骨格同士が干渉しないこと

## 完了条件 (DoD)
- [ ] 完成写真 (骨格側面・爪クローズアップ)
- [ ] 使った MG90S の個体番号 (肩ピッチ / 肘)
- [ ] 爪がぐらつかない (接着硬化後)
"""

issue("EL-09", "EL-09 [要判断] UBEC 定格 (連続 10A) の妥当性 — L-10 の実測電流で 15〜20A 級への変更を判断",
      parent="E3", milestone="M2 頭無し歩行",
      labels=["type/要判断", "area/電装", "prio/P1"],
      blocked_by=["L-10"],
      body=f"""
## 決めること
歩行時のサーボ電流は静力学見積り (2026-09-04 システム監査 S-03) で **9〜14A 級** — HOBBYWING UBEC-10A-V2 の
連続 10A / ピーク 20A と同水準で、出力 6.0V 設定時の入力ヘッドルーム (2S 末期 6.4V カット) も小さい。

## 判断材料 ({{{{L-10}}}} が貼る)
- 歩行 (前進/旋回) 中の 6V バス電流のピーク・平均 (クランプメータ)、バッテリー電圧の落ち込み
- UBEC の発熱、ブラウンアウト (ESP32 リセット / サーボ脱力) の有無

## 決定の書き方
「現行 10A で可 / 15A 級へ交換 / 20A 級へ交換 (+ロッカー SW 15〜20A 級)」を理由付きでコメントし、交換なら
`docs/BOM.md` #6/#12 と `docs/wiring.md` を更新して Close。

## 注意
歩行中の電流はクランプ式でないと測れない (直列テスタ 10A レンジでは不足・回路を切る必要あり) → DC クランプメータを {{{{P-01}}}} で購入。
""")

issue("EL-10", "EL-10 [ファーム] アイドル時の発熱対策 — 一定時間無指令で休止姿勢 / 段階脱力 (S-07)",
      parent="E3", milestone="M5 統合・完成",
      labels=["type/タスク", "area/ファーム", "skill/ファーム", "並行作業OK", "prio/P2"],
      blocked_by=["EL-07"],
      body=f"""
## ゴール
firmware には低電圧カット以外に脱力/デューティ低減が無く、立位保持だけで股ピッチ/膝が定格の 45〜55% を
連続要求する (2026-09-04 S-07)。長時間の静止立位でのサーボ発熱を抑える。

## 案 (オーナーと相談)
1. 無指令 N 秒 (例 60s) で足先を内側へ寄せた「休止スタンス」(股ピッチのレバー短縮 → トルク減) へ遷移し、指令で復帰
2. さらに長時間 (例 5 分) で座り姿勢 (体高最小) → 脱力。復帰は立ち上がりシーケンス
3. Web UI に手動「休止/脱力」ボタン

## 完了条件 (DoD)
- [ ] `sim_gait.py` で休止スタンスのトルク低減量を数値で示す
- [ ] 実機で 10 分静止時のサーボ温度 (非接触温度計) を対策前後で比較
""")

issue("L-11", "L-11 [要判断] 股ピッチトルク余裕ゼロ対策 — 7.4V 給電 / 高トルクサーボ / 低トルク歩容 (S-02)",
      parent="E4", milestone="M1 片脚 Go/No-Go",
      labels=["type/要判断", "area/試験", "area/電装", "prio/P0"],
      blocked_by=["L-02"],
      body=f"""
## 背景 (2026-09-04 システム監査 S-02)
`sim_gait.py` [3] を 1 点評価から全域 (全指令×全位相, 3/4 点支持静力学, 実重心 y=-39mm) に直した結果、
股ピッチの最悪トルクは **18.5 kgf·cm** (脚荷重 1.07kgf × レバー 173mm)。DS3218 系の定格は 20 kgf·cm@6.8V、
UBEC 6.0V 運用では ~18 (出典で 18〜21.5 とばらつく, UNVERIFIED) — **余裕ほぼゼロ**。総重量が +20% なら 22 で確実にストール域。

## 2026-09-04 仕様確認 (7.4V 給電案の可否)
販売ページ集約値 (確認日 2026-09-04。Hiwonder 公式ページは本文取得不可のため一次資料としては UNVERIFIED):
| サーボ | 動作電圧 | 7.4V 給電 |
|---|---|---|
| Goolsky DS3218MG (shopping.md 確定リンク) | 4.8–6.8V, 19@5V/21.5@6.8V kgf·cm | **不可** (定格 +9%) |
| Hiwonder LD-20MG (注文メモ) | 6–7.4V, 20 kgf·cm@6.6V | 可 (上限ぎりぎり) |
| Hiwonder LD-220MG (持込図面) | 6–8.4V, 20 kgf·cm@7.4V, H 51.4 両軸 | 可 |
| MG90S (腕 ×6) | 4.8–6.0V | **不可** (+23%) → 6V 副レール分離が必須 |
| EMAX ES9251II (目 ×2) | 4.5–6.0V | **不可** → 同上 |
さらに **2S LiPo (8.4→6.4V) では降圧 UBEC は 7.4V を維持できない** (入力が ~7.8V を割ると出力は入力追従) —
放電の前半しか効かない。常時 7.4V にするなら 3S (11.1V) 化 = バッテリー買い直し + `battery_cradle` 再印刷 (厚み 24→~33mm) +
VBAT 分圧/閾値変更が付随する。トルク増分も DS3218MG の傾き (5→6.8V で +13%) から換算すると 6.0→7.4V で **+10% 程度**。

## 選択肢 ({{{{L-02}}}} の実測後に決める) — 推奨順
| 案 | 効果 | 条件・コスト |
|---|---|---|
| **C. 低トルク歩容**: `STANCE_R 129→122` + `MAX_STEP 30→26` | 18.5→17.0 kgf·cm (-8%) | 定数のみ・即日。歩幅 -13%、安定マージン +8.7mm |
| **B. 高トルク品 (25〜35 kgf·cm 級, 同寸法 40×20×40.5, 6V 定格内)** | +25〜75% | 追加購入 12〜14 本 (¥3,000〜4,500/本)。ケース同寸なら STL 無変更 (P-03 実測で確認) |
| D. 軽量化 (Cabin を薄壁/低インフィルで再印刷, 電装整理) | 質量比例 | Cabin 534g が最大。-100g で -3% |
| A. UBEC 7.4V | +10% 程度 (UNVERIFIED) | 実サーボが LD-20MG/220MG の場合のみ。**6V 副 BEC (腕・目) + 3S 化 + cradle 再印刷 + VBAT 変更**が付随 → 非推奨 |

## 決定の書き方
L-02 の保持試験結果 (18.6 kgf·cm 相当を保持できたか、電流) を根拠に A〜D の組合せをコメントし、
`config.py` / `config.h` / `docs/BOM.md` へ反映して Close。
""")

issue("A-01", "A-01 [組立] 右腕チェーンをベンチで組む (肩ブラケット→上腕→前腕→固定爪)",
      parent="E5", milestone="M3 サブアセンブリ",
      labels=["type/タスク", "area/組立", "skill/模型仕上げ", "並行作業OK", "prio/P1"],
      blocked_by=["PR-06", "PR-01", "PR-02", "EL-04"],
      body=f"""
## ゴール
右腕 (標準版: shoulder_bracket / upper_arm / forearm / claw_mount)。左腕 ({{{{A-02}}}}) と並行可。
{_ARM_STEPS}
""")

issue("A-02", "A-02 [組立] 左腕チェーンをベンチで組む (_L パーツ)",
      parent="E5", milestone="M3 サブアセンブリ",
      labels=["type/タスク", "area/組立", "skill/模型仕上げ", "並行作業OK", "prio/P1"],
      blocked_by=["PR-06", "PR-01", "PR-02", "EL-04"],
      body=f"""
## ゴール
左腕 (`_L` 版: shoulder_bracket_L / upper_arm_L / forearm_L / claw_mount_L [先行印刷済])。機構は左右ミラー、firmware がヨーを反転する。
爪ハブは**左腕も `Arm_Left_Claw_Grey`** (Right 版は別形状で不使用)。
{_ARM_STEPS}
""")

issue("A-03", "A-03 [組立] 肩ヨーサーボ ×2 をシャーシへ + 両腕を吊り下げ + 配線 + ベンチ確認 (TUCK/READY/REACH/WAVE)",
      parent="E5", milestone="M3 サブアセンブリ",
      labels=["type/タスク", "area/組立", "area/配線", "res/本体", "prio/P1"],
      blocked_by=["A-01", "A-02", "L-06", "EL-06", "EL-07"],
      body=f"""
## 手順 ([assembly.md §2.5-1,2,8,9]({D}/assembly.md))
脚の通電確認 (L-09) は前提にしない — シャーシ電装 ({{{{L-06}}}}) と本番ファーム ({{{{EL-07}}}}) があればベンチ電源で
腕だけ動かせる (脚ストリームと並行可, 2026-09-04 依存見直し)。ただし手順 5 の前脚クリアランス確認は脚取付 ({{{{L-07}}}}) 後に行う。
1. **肩ヨー MG90S** をシャーシの腕マウント開口 (Head_Bottom ソケット直下, 正面から ±40°) へ**上から挿入・軸下向き**、タブを台座ボスへ M2×8 ×2。ギヤヘッドとホーンはプレート下面側
2. サーボ中立 (1500µs) で shoulder_bracket のホーンをヨー軸へ吊り下げ。**腕は正面向きでなく放射外向き (正面から 40°) = 中立** — 上腕が Head_Bottom ソケットの開口方向を向くことを目視
3. 配線: 肘 → upper_arm 内側 → 肩ブラケット背面 → MICRO 開口 → プレート上面。board1 ch: 右 16/17/18 (ヨー/ピッチ/肘)、左 20/21/22。全可動域で張らない長さ、束ねはヨー根本を避けシャーシ前縁で 1 点固定
4. `/arm?pose=tuck` → `ready` → `reach` → `wave` を順に実行し、各姿勢で干渉・脱調なし
5. **脚を静止 (立位) にしたまま**腕の全域 (特にヨー ±15° 端 × 脚ヨー大) を低速で試し、前脚・coxa とのクリアランスを目視。逆に動く軸は `ARM_SIGN` 反転

## 完了条件 (DoD)
- [ ] 4 プリセットの写真 (または動画)
- [ ] ch 対応表 (腕×関節→ch→個体番号)、ARM_SIGN の最終値
- [ ] 歩行試験中は TUCK にする旨を {{{{L-10}}}} / {{{{I-02}}}} の担当と共有

## 注意
歩行中は firmware の脚×腕連成クランプで、前脚ヨー >20° のとき腕ヨーが -15° へ自動退避する (発火率 42-44% は正常)。
""")

issue("A-04", "A-04 [仕上げ] 腕シェル被せ (arm_pod 上下 ×2 / elbow_shell ×2 / Arm Guard 左右)",
      parent="E5", milestone="M4 フルドレス",
      labels=["type/タスク", "area/仕上げ", "skill/模型仕上げ", "prio/P2"],
      blocked_by=["A-03"],
      body=f"""
## 手順 ([assembly.md §2.5-6]({D}/assembly.md))
- arm_pod_upper / lower (元 Arm ポッドのクラムシェル 2 分割, Blue_2 印刷済) を upper_arm を上下から挟むように被せて接着 (**ホットボンド推奨 = 脱着可**)。肘側下面の大きな開口は肘窩 (前腕の掃引域) なので塞がない
- elbow_shell (元 Elbow 球の半殻, 印刷済) を肘サーボの突出ケース底へ接着 [現物合わせ]。ケース底は欠き底 (球中心 -6mm) に当てる
- Arm_Left_Guard_Grey / Arm_Right_Guard_Grey ({{{{PR-01}}}}) をそれぞれの腕のポッド表面へ接着 (任意。上殻フランクの ~18×5mm 開口を隠せる)
- 被せた後に 4 プリセットを再実行し、シェルが肘 0-95° の掃引で干渉しないこと

## 完了条件 (DoD)
- [ ] 両腕の完成写真、肘 95° での写真 (シェル干渉なし)
""")


# ===========================================================================
# E6 頭部: 目・カメラ・音声
# ===========================================================================
issue("H-01", "H-01 [組立] 目ポッド ×2 (キョロキョロ) — ドット黒仕上げ / ES9251II / ホーン先付け / 中立位相",
      parent="E6", milestone="M3 サブアセンブリ",
      labels=["type/タスク", "area/組立", "skill/模型仕上げ", "並行作業OK", "prio/P1"],
      blocked_by=["PR-06", "EL-04"],
      body=f"""
## 手順 ([assembly.md §2.7-1〜4]({D}/assembly.md))
1. eye_pod (白, 印刷済) の**ドット穴 3 つを黒く仕上げる** (黒塗料流し込み or 黒フィラメント片接着) — これが視線マーク
2. ES9251II を eye_carrier のポケットへ (付属ビス)
3. **ホーンを先に eye_pod へ共締め**: 背面ポケットにホーンを埋め、アーム穴 2 個から極小タッピング (BOM #29, M1.7×6 級) を下穴 φ1.1 へ。**中心ビスは使わない** (ポッドに塞がれて締められない)。穴が合わなければホーン穴経由で φ1.2 で現物開け直し
4. **サーボ中立 (1500µs) でドット群がほぼ真下を向く**スプライン位相を選び、眼球+ホーンを押し込む (残差は Web UI の目トリム ±200µs≈±18° で吸収)。抜けが心配なら軸に微量の瞬着
5. ベンチで `/eye?mode=kyoro` / `front` / `scan` を確認 (ESP32 + board1 ch24 右 / ch26 左)

## 完了条件 (DoD)
- [ ] 2 個の完成写真 (ドット黒仕上げ・背面ホーン)
- [ ] 中立でドットが真下を向いている写真、キョロキョロの動画
- [ ] 頭部装着 ({{{{S-03}}}}) 前に carrier のロール向き規約 (ケース長辺 = 前後方向の接線) を確認済み
""")

issue("H-02", "H-02 [組立] カメラ目 — 子基板→camera_carrier→base→shell 接着, XIAO ファーム + WiFi 静止画疎通",
      parent="E6", milestone="M3 サブアセンブリ",
      labels=["type/タスク", "area/組立", "area/ファーム", "skill/ファーム", "並行作業OK", "prio/P1"],
      blocked_by=["P-07", "PR-06", "PR-10"],
      body=f"""
## 手順 ([assembly.md §2.9-1〜3, 6]({D}/assembly.md), [voice.md §5]({D}/voice.md))
1. カメラ子基板 (レンズ側) を camera_carrier のポケットへ差し込み瞬着で軽く固定 (片側 0.4mm クリア設計)
2. 電源 AWG30 2 芯を carrier 側方の切り欠きから引き出す (信号線は無し — 独立 WiFi)
3. carrier+モジュールを eye_pod_camera_base の斜めポケットへ落とし込み (向きは機械的に一意)、FPC を base 底面のスロットから出す
4. eye_pod_camera_shell を被せ、リングプラグを溝へ嵌合して接着 (回転位相も一意)。レンズ面は瞳の奥 6.57mm に自動で位置する。
   光軸仰角の残差 8.6° は要求 ±10° に対し余裕 1.4° — 接着中は机面+スコヤで水平を出す (2026-09-04 M-04)
5. XIAO ESP32S3 Sense にストリーミングファーム (静止画 `/capture` + MJPEG) を書き込み、iPhone テザリング (2.4GHz) へ STA 接続、HTTP で静止画取得
6. 本体基板 (ESP32S3/USB-C) は頭内の空きスペースへ両面テープ (現物合わせ, {{{{S-03}}}})

## 完了条件 (DoD)
- [ ] 組み上がったポッドの写真 (瞳の偏心方向にマーキングしておく — 取付位相が重要)
- [ ] カメラから取得した静止画 1 枚、消費電流 (5V, 配信中) の実測
""")

issue("H-03", "H-03 [組立] 音声ユニット組込 — INMP441→cradle_mic 圧入 / φ20 SPK / cradle_spk / 8 芯を Neck・Ball ボアへ",
      parent="E6", milestone="M3 サブアセンブリ",
      labels=["type/タスク", "area/組立", "skill/はんだ", "並行作業OK", "prio/P1"],
      blocked_by=["PR-07", "PR-06", "P-02"],
      body=f"""
## 手順 ([assembly.md §2.8-1〜5]({D}/assembly.md), [printing.md「音声内蔵」]({D}/printing.md))
1. INMP441 に AWG30 6 芯 (SCK/WS/SD/GND + 3.3V/GND, **L/R は GND**) をはんだ → audio_cradle_mic の中央トレイへ差し込み瞬着。音孔は cradle の φ1.8 ポート穴 (キー突起の反対側) に合わせる
2. 配線を砲身中心ボア方向へ引き出しながら、**キー突起をポケットのキー溝に合わせて**マイクポケットへ圧入 (合わない向きでは入らない)。Walk_1 同乗の cradle (0.2 層) が粗ければ {{{{PR-07}}}} の 0.12 版
3. φ20 スピーカーを砲口側から挿入 (振動板を砲口へ)、2 芯を後方へ
4. audio_cradle_spk (抜け止めワッシャ) を奥へ押し込む (ホットボンド併用可)
5. 計 8 芯を Mouth_Cannon 後端 → Mouth_Neck_Bored → Mouth_Ball_Bored の φ6 ボア経由で引き出す (Head_Bottom_Armcut の φ7 受け穴を通すのは {{{{S-04}}}})

## 完了条件 (DoD)
- [ ] 組込前後の写真 (キー合わせ・砲口のスピーカー)
- [ ] 8 芯の導通チェック、マイクポートが砲身下面の φ1.8 開口と一致している写真
""")

issue("H-04", "H-04 [電装] MAX98357A + I2S ベンチ配線 → voice_bridge --mock で PTT 録音 / 440Hz 再生を確認",
      parent="E6", milestone="M3 サブアセンブリ",
      labels=["type/タスク", "area/電装", "area/ファーム", "skill/はんだ", "prio/P1"],
      blocked_by=["H-03", "EL-07"],
      body=f"""
## 手順 ([wiring.md「音声ユニット (I2S) 配線」]({D}/wiring.md), [voice.md §2]({D}/voice.md))
| ESP32 | 接続先 |
|---|---|
| GPIO26 | INMP441 SCK + MAX98357A BCLK (共有) |
| GPIO25 | INMP441 WS + MAX98357A LRC (共有) |
| GPIO27 | MAX98357A DIN |
| GPIO33 | INMP441 SD |
| 3.3V | INMP441 VDD (L/R=GND) |
| 5V | MAX98357A VIN (GAIN/SD 未結線) |

1. MAX98357A は頭部/シャーシ側、砲身内のスピーカーへは SPK 2 線のみ
2. `.venv/bin/python tools/voice_bridge.py --mock --self-test` (オフライン) → 次に `--mock` で実機接続: Web UI ボイスタブの PTT でマイク音声がブリッジへ届く / スピーカーから 440Hz トーン

## 完了条件 (DoD)
- [ ] 配線写真、`--mock --self-test` の PASS ログ
- [ ] PTT 録音が届いた証拠 (ブリッジ側ログ) と 440Hz 再生の動画
""")

issue("H-05", "H-05 [組立] Mouth 一式 (Ball→Neck→Cannon チェーン + Cap/Key/Peg) をキット標準で組む",
      parent="E6", milestone="M3 サブアセンブリ",
      labels=["type/タスク", "area/組立", "skill/模型仕上げ", "並行作業OK", "prio/P1"],
      blocked_by=["H-03", "PR-01"],
      body=f"""
## 手順 ([assembly.md §2.8-6]({D}/assembly.md))
- Ball (`Mouth_Ball_Bored`, 球径 φ25.1) — Neck (`Mouth_Neck_Bored`, 全長 16.09) — Cannon 後端ソケット (φ10.7) を現物合わせで継ぐ。**Ball↔Neck の standoff は 20.0mm** (設計値: 非重なりとレイアウトの妥協点、Neck の半分近くが Ball の陰に隠れるのは仕様)
- Mouth_Cap_Grey で砲身外套を被せ、Mouth_Key / Mouth_Peg で固定 (キット標準)。**外側の組立手順は音声内蔵で変わらない**
- 8 芯配線が Ball の後ろから出ている状態にしておく
- Mouth_Neck_Blue と Ball は完成後も見えるので意匠パーツとして仕上げる
- 頭部ソケットへの取付 (ポーズ -18° 下向き) は {{{{S-04}}}}

## 完了条件 (DoD)
- [ ] 組み上がった Mouth 一式の写真 (正面・側面)
""")

issue("H-06", "H-06 [要判断] ESP32 DevKit の恒久マウント位置 (頭内は全姿勢不成立 → 頭外 3 択)",
      parent="E6", milestone="M4 フルドレス",
      labels=["type/要判断", "area/電装", "prio/P1"],
      body=f"""
## 背景
2026-08-22 の実占有ボクセル場での網羅探索で、**Head_Top 内に ESP32 DevKit (58×28) を置ける姿勢は存在しない**
と確定 ([HANDOFF §6]({D}/HANDOFF.md), `config.py` ESP32_SLOT コメント)。歩行実験 (頭無し) はテープ留めで支障なし。
**Head_Top を被せる ({{{{S-03}}}}) までに決める必要がある。**

## 選択肢
1. **Cabin_Front 内ベイ化** — 「Cabin_Front 無加工」方針の転換が必要 (ベイ加工自体はポッド自身基準なので位置不確実性の問題は無い)
2. **後方デッキ縦置き** — 可視領域なので意匠判断
3. **小型ボードへ変更** (例: ESP32 の小型モジュール) — config.h のピン割当見直し

## 決定の書き方
選択肢と理由をコメント → 必要なら CAD イシュー (`area/CAD`) を切って本イシューをリンク → Close。
""")

issue("H-07", "H-07 [要判断] CH_HEAD (ch12) の扱い — 駆動対象が実在しない (推奨: 予備維持)",
      parent="E6", milestone="M5 統合・完成",
      labels=["type/要判断", "area/ファーム", "prio/P2"],
      body=f"""
## 背景
firmware の `CH_HEAD` (board0 ch12, wz 連動 ±25°) は「頭頂の回転ドーム」を想定していたが、2026-07-30 の実測で
`Head_Dome_Grey` は前頭部の φ7.4 リベット (回転対称) であり、頭部は完全固定と確定 ([wiring.md 末尾]({D}/wiring.md))。

## 選択肢
(a) リベットをそのまま回す (効果ゼロ) / (b) 別の可動要素へ再割当 / **(c) 予備維持 (推奨)**。
(c) なら SG90 は 1 個予備専用、firmware は変更不要。決めたらコメントして Close。
""")


# ===========================================================================
# E7 意匠シェル・Cabin・仕上げ
# ===========================================================================
issue("S-01", "S-01 [組立] pod_neck 取付 + TailJoint (青コーン+Ball リング) 化粧スリーブ接着",
      parent="E7", milestone="M4 フルドレス",
      labels=["type/タスク", "area/組立", "res/本体", "prio/P1"],
      blocked_by=["PR-06", "PR-01", "L-06"],
      body=f"""
## 手順 ([assembly.md §2-3]({D}/assembly.md), [printing.md「Head_TailJoint の使い方」]({D}/printing.md))
1. pod_neck 梁をシャーシ後端 (0,-58) のブラケットへ **M3×4 で共締め** (スタンド治具と共用)
2. `Head_TailJoint_Blue_Optional_Cross` (Blue_4 印刷済) + `Head_TailJoint_Ball_Grey` ({{{{PR-01}}}}) を**無加工のまま**梁の先端 (φ12 丸ポスト化済み, 被せ代 20mm) へ被せて接着。十字キー溝は当てにしない (位相合わせ不要)
3. 梁先端フランジ (36×30, M3×4 穴) が Cabin_Front 取付 ({{{{S-06}}}}) 用に露出していること

## 完了条件 (DoD)
- [ ] 取付後の写真 (後方から)。梁が水平で左右にガタが無い
- [ ] 頭部 (Head_Bottom) と pod_neck の逃がしカット部のクリアランスが目視で確保できている (設計 2.7-3.8mm)
""")

for _k, _n, _sh, _blk in (("S-02a", "FL", "shin_shell (標準)", "L-01"),
                          ("S-02b", "FR", "shin_shell_m (ミラー)", "L-03"),
                          ("S-02c", "RL", "shin_shell_m (ミラー)", "L-04"),
                          ("S-02d", "RR", "shin_shell (標準)", "L-05")):
    issue(_k, f"{_k} [仕上げ] 脚 {_n} の装飾 — thigh_cap / Thigh_Guard / {_sh} / Shin_Guard / Leg_Toe ×3",
          parent="E7", milestone="M4 フルドレス",
          labels=["type/タスク", "area/仕上げ", "skill/模型仕上げ", "並行作業OK", "prio/P2"],
          blocked_by=["L-10", "PR-05", "PR-01", "PR-02"],
          body=f"""
## ゴール
{_n} 脚に意匠を付ける。4 脚は独立なので**歩行ゲート ({{{{L-10}}}}) 合格後に 4 人で並行可**。
FR/RL は**ミラー脚**なのでシェルも `_m` を使う ([assembly.md §3]({D}/assembly.md))。

## 手順
1. thigh_cap (灰, {{{{PR-05}}}}) を femur 上面へ被せ接着、Leg_Thigh_Guard_Blue (Blue_4 印刷済) を接着 (embed 率で位置決め済み — キット完成写真準拠)
2. **{_sh}** (Blue_1 印刷済) を tibia へ下からスライド → **M3×40 ×2 本を横から貫通、ナイロンナット留め**。装飾ドット面が放射外向きになる (shin_rotz=0)
3. Leg_Shin_Guard_Grey ({{{{PR-01}}}}) を shin_shell 表面へ接着 [現物合わせ — 曲率不一致で最大 6.5mm の残差は既知、接着で吸収]
4. **Leg_Toe_Black ×3** ({{{{PR-02}}}}) を leg_foot_bored の甲底面スタブ 3 箇所 (-98.4° / +45.5° / +145.4° の非等間隔) へ瞬着。爪の腹側 (湾曲の凹み) を接地方向へ
5. 膝はメカ剥き出しで確定 (カバー無し)

## 完了条件 (DoD)
- [ ] 脚の完成写真 (外側・内側)。脚を可動域端まで動かしてシェルが隣接脚・腕に当たらない
- [ ] foot_pad が接地し、トゥは装飾 (接地力は foot_pad が受ける) ことを確認
""")

issue("S-03", "S-03 [組立] 頭部組立 — Head_Bottom_Armcut / 目ポッド ×2 + カメラ目装着 / Head 装飾 / Head_Top 被せ",
      parent="E7", milestone="M4 フルドレス",
      labels=["type/タスク", "area/組立", "area/仕上げ", "res/本体", "prio/P1"],
      blocked_by=["H-01", "H-02", "H-06", "PR-08", "PR-01", "PR-02", "L-10"],
      body=f"""
## 手順 ([assembly.md §2.7-5,6 / §2.9-4,5 / §3]({D}/assembly.md))
1. **ESP32 を頭外へ退避** ({{{{H-06}}}} の決定どおり)。頭内にテープ留めできる平面はもう無い
2. **Head_Bottom_Armcut** (浅いボウル, Blue_2 印刷済・カスプ除去版) の上端リング面をプレート下面へホットボンド。Head_Plate / Head_Bottom_Cap は**使わない**
3. **目ポッド ×2**: シェル内側からネック φ24 を Head_Top_Eyecut v2 ({{{{PR-08}}}}) の φ30 ボアへ通し、carrier を内側へ接着 [現物合わせ]。**キャップ底を座グリ床から ~1.5mm 浮かせ**、全回転で床・縁に擦らない。**carrier のロール = ケース長辺が水平接線方向 (左右目は前後方向)** — サーボ尾が中央のカメラ carrier と近づくため必須
4. **カメラ目**: ネック φ28 を中央ボアへ、**瞳が水平前方をまっすぐ向く取付位相**でシェル内側から接着 (残差 ~8.6° は設計値)。本体基板 (XIAO) は頭内空きスペースへ両面テープ、電源 2 芯をタブ間隙間から胴へ
5. Head 装飾 (`Head_Dome_Grey`, `Head_Plug_Grey`, `Head_Screw_Grey` ×2, `Head_Insert_Black` ×4 [2 個は位置未確定 → 実物写真/現物合わせ, {{{{S-08}}}}], `Head_Peg` 上下はダウエル)
6. PCA スタックのプラグを全て挿した状態で **Head_Top を被せ、7 タブへ点接着** (ホットボンド)。目・音声・カメラ配線はタブ間隙間 (弦長 ~47mm) を通す
7. SW+ヒューズが Head_Top 内面と当たれば数 mm 内側へ

## 完了条件 (DoD)
- [ ] 頭部が浮かずに被さっている写真 (合わせ目)。**脚ヨー ×4 を全域動かしてもスカート切欠きに当たらない**
- [ ] 目のキョロキョロ動画 (装着後)、カメラ静止画 (正面・水平が確認できる)
- [ ] Head_Top を外す手順 (プラグ挿抜は Head_Top を外して行う) をコメントに残す
""")

issue("S-04", "S-04 [組立] Mouth 一式を頭部ソケットへ取付 (Ball スナップ, -18° 下向きポーズ, 配線 φ7 穴→MAX98357A)",
      parent="E7", milestone="M4 フルドレス",
      labels=["type/タスク", "area/組立", "res/本体", "prio/P1"],
      blocked_by=["H-05", "S-03"],
      body=f"""
## 手順 ([assembly.md §2.8-5,6「ポーズ角」]({D}/assembly.md))
1. 8 芯配線を Head_Bottom_Armcut のマウス配線受け穴 (φ7, 焼き込み済み) → 頭部内部キャビティ → MAX98357A (頭部/シャーシ側) へ。ボア内で弛まない取り回し
2. Ball (φ25.1) を Head_Bottom 前面ソケット (φ26.8) へスナップ嵌合、**砲身が水平から 18° 下向き** (`MOUTH_CANNON_ROT_X_DEG=-18`) のポーズで接着。実物写真「ほぼ前方・わずかに下向き」準拠
3. 両腕の TUCK/READY で砲身に当たらないこと (設計: 全域スイープ交差 0、最小 38mm)

## 完了条件 (DoD)
- [ ] 正面・側面の写真 (角度が分かる)、腕 4 プリセットで干渉なし
- [ ] {{{{H-04}}}} の PTT/再生が装着後も動く
""")

issue("S-05", "S-05 [仕上げ] Cabin (背中ポッド) をキット標準で組む — Front/Back/Turret/RedLight/Spinnarette/Insert/Cabin_Eye + LED 仕込み",
      parent="E7", milestone="M4 フルドレス",
      labels=["type/タスク", "area/仕上げ", "skill/模型仕上げ", "並行作業OK", "prio/P1", "good first issue", "help wanted"],
      blocked_by=["PR-01", "PR-02", "PR-03"],
      body=f"""
## ゴール
Cabin は本体と無関係に**純粋な模型組立**として最初から並行できる (Cabin_Front / Back / Cabin_Eye は印刷済み)。
完成写真 (`~/Downloads/TACHIKOMA.3mf` の `Auxiliaries/.thumbnails/thumbnail_middle.png`) と
[docs/vis_proportions.png]({D}/vis_proportions.png) を見ながらキット説明どおりに。

## 手順
1. **Cabin_Back の向き**: 上下反転しやすい (シーム一致だけでは判定できない)。ハッチ・ベント・スカートなど**非対称フィーチャー**と写真で上下・前後を決める (落とし穴 #53)
2. Cabin_Peg ×2 で Front/Back を嵌合 (接着は {{{{S-06}}}} の配線通しの後でも可)
3. Turret 左右 + Turret_Peg ×2 (銃身ロールは前方 25.9° 下向き、左右ミラー)、Spinnarette ×4 (側面)、RedLight 大×4 小×4、Insert 6 種 (Bottom_Wide と Peg の位置は UNVERIFIED — 現物合わせ)
4. **Cabin_Eye_White** を前面に (裏に WS2812 index 0 を仕込む: {{{{EL-08}}}} のチェーン先頭)。赤ランプ ×8 の裏にも index 4-11 を仕込み、リード線を pod_neck 側へまとめて出す
5. pod_neck フランジ (36×30, M3×4) 用の穴は {{{{S-06}}}} で現物合わせ

## 完了条件 (DoD)
- [ ] 組み上がった Cabin の写真 (正面・側面・背面)、LED 点灯写真
- [ ] 向き判定の根拠 (どのフィーチャーで上下を決めたか) をコメント
""")

issue("S-06", "S-06 [組立] Cabin を pod_neck へ取付 (フランジ M3×4 現物合わせ穴あけ, LED 配線を梁沿いにシャーシへ)",
      parent="E7", milestone="M4 フルドレス",
      labels=["type/タスク", "area/組立", "res/本体", "skill/模型仕上げ", "prio/P1"],
      blocked_by=["S-05", "S-01"],
      body=f"""
## 手順 ([assembly.md §3「Cabin (ポッド) v3」]({D}/assembly.md), [wiring.md 悉皆確認 #9/#10]({D}/wiring.md))
1. Cabin_Front の後端に pod_neck フランジ (36×30, M3×4) の穴を**現物合わせ**で開ける (φ3.2 + 内側当て板)。Cabin は足と同じ高さの後方に来る (ghost: CabinF (0,-156,zb+55))
2. M3×10 なべ小ねじ + ナットで共締め (BOM #18b)
3. Cabin 内の LED / DFPlayer 配線を梁沿いに TailJoint スリーブの隙間を通してシャーシへ (正式チャンネル無し — 現物合わせ)
4. 完成後に対角 2 脚で持ち上げて前後バランスを確認 (バッテリー位置で調整、{{{{I-01}}}})

## 完了条件 (DoD)
- [ ] 取付後の写真 (側面: Cabin の高さ・水平)。歩行姿勢で後脚 (RL/RR) のヨー ±22° (ポッド側クランプ) で当たらない
""")

issue("S-07", "S-07 [電装] LED ×12 / DFPlayer / 74AHCT125 を本体へ組込 (直列順 0→11, 目 LED は carrier 裏)",
      parent="E7", milestone="M4 フルドレス",
      labels=["type/タスク", "area/電装", "area/配線", "skill/はんだ", "res/本体", "prio/P2"],
      blocked_by=["EL-08", "S-03", "S-06"],
      body=f"""
## 手順 ([wiring.md「WS2812B 直列順」「DFPlayer」]({D}/wiring.md))
- 0 メインアイ (Cabin_Eye 裏) → 1 右目 / 3 左目 (eye_carrier 裏, 白ポッドがバックライトされる。2 中央は未使用) → 4-7 赤ランプ大 → 8-11 赤ランプ小
- 74AHCT125 と DFPlayer はシャーシ上 (Head 内クリアランス包絡の外) に。DFPlayer のスピーカーは Cabin 内
- 5V レールの電流収支 (ESP32 + LED 全灯 + DFPlayer + 音声 + カメラ ≈ 2.6A vs 3A 定格) を実測

## 完了条件 (DoD)
- [ ] 全灯写真、5V 電流実測、起動音の動画
""")

issue("S-08", "S-08 [要判断] Head_Insert_Black ×4 のうち 2 個の位置 (3MF に座標無し → 実物写真で確定)",
      parent="E7", milestone="M4 フルドレス",
      labels=["type/要判断", "area/仕上げ", "prio/P2"],
      body=f"""
## 背景
`tools/data/kit_assembly_front.json` の unresolved リスト。3MF 側に座標情報が無く復元不能。
オーナーが所有する組立済み実物フィギュアの頭部写真 (正面・側面・上面) があれば確定できる。
実機組立には影響なし (現物合わせで貼ってよい) — {{{{S-03}}}} をブロックしない。

## 決定の書き方
写真をコメントに添付 → 位置を決めて JSON / robot_meshes / URDF へ反映する CAD イシューを切る (任意) → Close。
""")


# ===========================================================================
# E8 統合・調整・完成
# ===========================================================================
issue("I-01", "I-01 [測定] 実重量計測・重心確認 (対角 2 脚持ち上げ, バッテリー位置調整, 3.0kg 想定と比較)",
      parent="E8", milestone="M5 統合・完成",
      labels=["type/タスク", "area/測定", "area/試験", "res/本体", "prio/P1"],
      blocked_by=["S-03", "S-06", "A-04", "S-02a", "S-02b", "S-02c", "S-02d"],
      body=f"""
## 手順
1. フルドレス状態 (バッテリー込み) の総重量を計る。設計想定 **~3.0kg** (歩容・トルク検証の前提。URDF 質量 2.78kg)
2. 対角 2 脚 (FR-RL / FL-RR) で持ち上げ、前後の傾きでバッテリー位置 (cradle 内の前後) を調整 ([assembly.md §3「重心確認」]({D}/assembly.md))
3. **前後の重心位置を実測** (対角持ち上げの釣り合い点、または 2 台の秤で前後脚の荷重比) → `config.py CG_XY`
   (設計値 y=-39mm, Cabin が主因) を更新して `sim_gait.py` [3][4] を再実行。マージンが 8mm を切るなら `STANCE_OFF_Y` /
   `SWAY_MM` を再調整 (2026-09-04 S-01)
4. 超過が大きい (>3.3kg) 場合は股ピッチトルク (全域最悪 18.5 kgf·cm, 余裕ほぼゼロ) を再計算 → `TOTAL_KG` を更新し {{{{L-11}}}} を再検討

## 完了条件 (DoD)
- [ ] 総重量の実測値と内訳 (本体 / バッテリー) をコメント
- [ ] 重心位置の所見と、バッテリー位置の最終決定
""")

issue("I-02", "I-02 [試験] フルドレス歩行調整 (Trim / 体高 115・歩幅 50% から / 腕 TUCK / foot_pad 滑り / 電流電圧ログ)",
      parent="E8", milestone="M5 統合・完成",
      labels=["type/タスク", "area/試験", "area/測定", "res/本体", "prio/P1"],
      blocked_by=["I-01"],
      body=f"""
## 手順 ([assembly.md §4]({D}/assembly.md))
1. Trim で脚 12ch + 腕 6ch + 目 2ch のセンター再調整 (シェル装着で重量分布が変わる)
2. 体高 115 / 歩幅 50% から始め、安定したら上げる。歩行中の腕は TUCK (肩ピッチに ±8° スイングが自動で乗る)
3. 前進・後退・横歩き・旋回・併進+旋回同時 (歩幅ノルムクランプの確認)
4. 6V 電流・バッテリー電圧のログ。低電圧保護の発動電圧
5. 滑る場合は foot_pad の接地径/硬度を別イシューで

## 完了条件 (DoD)
- [ ] 歩行動画 (フルドレス)、最終 Trim 値、歩容パラメータの変更があれば config の diff
- [ ] 連続歩行の実測時間とバッテリー電圧推移
""")

issue("I-03", "I-03 [ファーム] 音声会話の実運用 (iPhone テザリング 2.4GHz / STA 設定 / mDNS / API キー / voice_bridge)",
      parent="E8", milestone="M5 統合・完成",
      labels=["type/タスク", "area/ファーム", "skill/ファーム", "prio/P1"],
      blocked_by=["H-04", "S-04"],
      body=f"""
## 手順 ([voice.md §1-§4]({D}/voice.md))
1. iPhone インターネット共有 ON + **「互換性を最大にする」ON** (2.4GHz 化。OFF だと ESP32-WROOM から SSID が見えず無言で失敗)
2. AP `Tachikoma` に接続 → Web UI の Wi-Fi 設定 (POST /wifi) にテザリング SSID/パスワード → `GET /wifi` で connected:true
3. Mac も同じテザリングへ。`ping tachikoma.local` (mDNS 不可なら IP 直指定)
4. 環境変数 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID` を用意 (**ハードコード禁止**) → `.venv/bin/python tools/voice_bridge.py --host tachikoma.local`
5. PTT で会話が往復すること。遅延の目安は voice.md §4

## 完了条件 (DoD)
- [ ] 会話の動画 (PTT → 応答音声)、往復遅延の実測
- [ ] 声クローンは私的利用限定 (voice.md §6) を確認済み
""")

issue("I-04", "I-04 [ファーム] カメラ連携 (voice_bridge --camera-url で「見て」に答える)",
      parent="E8", milestone="M5 統合・完成",
      labels=["type/タスク", "area/ファーム", "skill/ファーム", "prio/P2"],
      blocked_by=["H-02", "I-03"],
      body=f"""
## 手順 ([voice.md §5]({D}/voice.md))
カメラ (XIAO) を同じテザリングへ接続し、`voice_bridge.py --camera-url http://<camera-ip>/capture` で発話ごとに
静止画 1 枚を LLM へ渡す。プライバシー注意は voice.md 参照。

## 完了条件 (DoD)
- [ ] 「何が見える?」に対して画像内容を答える動画
""")

issue("I-05", "I-05 [試験] 演出確認 — 目 (KYORO/FRONT/SCAN + 歩行方向バイアス) / LED / DFPlayer 起動音",
      parent="E8", milestone="M5 統合・完成",
      labels=["type/タスク", "area/試験", "prio/P2"],
      blocked_by=["S-07", "I-02"],
      body=f"""
## 確認項目
- [ ] `/eye?mode=kyoro` / `front` / `scan` の切替、前進中に視線が進行方向へ寄る (vy バイアス)
- [ ] LED 12 灯 (メインアイ / 目バックライト / 赤ランプ) の点灯パターン
- [ ] DFPlayer の起動音・歩行音
- [ ] 歩行中に腕 WAVE を実行したときの連成クランプ挙動 (半分未満の時間帯で内寄せ退避 = 正常)

## 証拠
動画 1 本にまとめて添付。
""")

issue("I-06", "I-06 [ドキュメント] 組立で得た現物合わせ知見を docs へ反映 (assembly / wiring / print_manifest / HANDOFF)",
      parent="E8", milestone="M5 統合・完成",
      labels=["type/タスク", "area/ドキュメント", "並行作業OK", "prio/P2"],
      body=f"""
## ゴール
各イシューのコメントに残った実測値・現物合わせ・失敗談を、次に作る人のために docs へ戻す。**各イシューが Close するたびに随時**やってよい (このイシューは受け皿)。

## 反映先
- `docs/assembly.md`: 手順の齟齬、現物合わせで決まった値 (Cabin 穴位置、Shin_Guard 位置、ESP32 位置)
- `docs/wiring.md`: 実配線の ch 対応表、線長
- `docs/print_manifest.md`: 印刷状況 (印刷済み/未印刷) の更新、実測重量・時間
- `docs/HANDOFF.md` §5/§6 と落とし穴集: 新しい教訓
- `hardware/src/config.py`: 実測値 (P-03/P-06/P-07 で更新済みのはず) のコメントに確認日

## 完了条件 (DoD)
- [ ] PR (または直接コミット) へのリンク。イシュー番号を `Refs #N` で本文に書く
""")

issue("I-08", "I-08 [検証] sim_physics: 関節トルク飽和ログ追加 + metrics/help 文言の整合 (S-08/S-10)",
      parent="E8", milestone="M5 統合・完成",
      labels=["type/タスク", "area/ファーム", "skill/CAD-Python", "並行作業OK", "prio/P2"],
      body=f"""
## 背景 (2026-09-04 システム監査)
- S-10: `tools/sim_physics.py` (MuJoCo) は全脚関節に `effort=1.96N·m` (=20 kgf·cm, 6.8V 相当) を一律適用しているが、
  静力学の最悪要求 18.5 kgf·cm はその 92%。実際にどの時刻で飽和したかは記録されていない
- S-08: `docs/physics_walk_metrics.json` の前進距離 (0.114m = 上限の 76%) と CLI help 文言 (0.138m/92%) が食い違う

## やること
1. `data.actuator_force` を metrics に記録し、飽和 (|τ| ≥ 0.95·effort) の時間率を出す
2. 6V 相当 (effort 1.77N·m = 18 kgf·cm) でも走らせ、転倒有無を比較
3. 2026-09-04 の歩容 (STANCE_OFF_Y / SWAY[4] / BODY_H_MIN 110) で metrics を再生成し、help 文言を実測値に合わせる
4. 可能なら `docs/vis_physics_walk.mp4` も再生成

## 完了条件 (DoD)
- [ ] 飽和時間率と転倒有無 (両 effort) をコメント、metrics.json 更新
""")

issue("I-07", "I-07 [運営] 完成お披露目 — 写真・動画・README 更新",
      parent="E8", milestone="M5 統合・完成",
      labels=["type/タスク", "area/ドキュメント", "prio/P2"],
      blocked_by=["I-02", "I-03", "I-05"],
      body=f"""
## ゴール
完成状態の写真 (正面・側面・上面) と歩行/会話の動画を撮り、README の冒頭に載せる。協力者のクレジットも。

## 完了条件 (DoD)
- [ ] README.md 更新の PR
- [ ] 動画リンク
""")


# ---------------------------------------------------------------------------
# 整合性チェック (import 時に実行)
# ---------------------------------------------------------------------------
def _validate():
    keys = [i["key"] for i in ISSUES]
    assert len(keys) == len(set(keys)), "key 重複"
    kset = set(keys)
    mset = {m[0] for m in MILESTONES}
    lset = {l[0] for l in LABELS} | {"good first issue", "help wanted"}
    for i in ISSUES:
        assert i["parent"] is None or i["parent"] in kset, f"{i['key']}: parent {i['parent']} 不明"
        assert i["milestone"] is None or i["milestone"] in mset, f"{i['key']}: milestone {i['milestone']} 不明"
        for l in i["labels"]:
            assert l in lset, f"{i['key']}: label {l} 未定義"
        for b in i["blocked_by"]:
            assert b in kset, f"{i['key']}: blocked_by {b} 不明"
            assert b != i["key"], f"{i['key']}: 自己依存"
    # 循環依存チェック (DFS)
    graph = {i["key"]: i["blocked_by"] for i in ISSUES}
    state = {}
    def dfs(k, path):
        state[k] = 1
        for b in graph[k]:
            if state.get(b) == 1:
                raise AssertionError(f"循環依存: {' -> '.join(path + [b])}")
            if state.get(b) is None:
                dfs(b, path + [b])
        state[k] = 2
    for k in graph:
        if state.get(k) is None:
            dfs(k, [k])


_validate()
