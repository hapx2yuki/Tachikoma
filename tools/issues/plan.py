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
- 条件付き ({{{{L-11}}}} / {{{{EL-09}}}} の決定後): 実機測定後の6V対応サーボ/電源部品。未同定サーボへの7.4V化を既定案にしない
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

issue("PR-04", "PR-04 [印刷] 片脚試験用の標準 tibia_link ×1 を先に印刷する",
      parent="E2", milestone="M1 片脚 Go/No-Go",
      labels=["type/タスク", "area/印刷", "res/プリンタ", "skill/プリンタ操作", "prio/P0"],
      blocked_by=["P-04", "P-05"],
      body=f"""
## 内容
**このIssueの成果物はFL用の標準1本だけ。** 既存4本配置3MFを別名保存し、標準1本以外を除外する。
残り3本は片脚ゲート {{{{L-02}}}} 合格後に {{{{PR-11}}}} で印刷する。
歩行チェーン (chassis→coxa→femur→**tibia**) の最後の未印刷部品。膝サーボのホーンポケットと
足ソケットを持つので、**サーボ実測 → config 確定 ({{{{P-04}}}}) 後に印刷** (2026-09-03 の判断で保留中)。
**2026-09-04 の膝ネック強度修正 (機構レビュー M-01)** が入った 3mf であること: 旧 45° ウェッジ×2 は
ネックを 11mm² (SF 0.1, 破断確実) まで痩せさせていた → femur 掃引領域の正確な減算 + 外側 3mm 増厚
(`check_leg_link_strength.py` SF 2.1, tibia 体積 32.4→34.3cm³, 質量 ≈+2g/本)。`PETG_Walk_3_Tibia.3mf` は
再生成済み (2026-09-04)。**それ以前の 3mf/印刷物は使用不可**。

## 使う先
{{{{L-01}}}} (FL) の片脚試験。残り3脚分は {{{{PR-11}}}}。

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
      blocked_by=["P-04", "P-07", "RV-10", "RV-09", "RV-15"],
      body=f"""
第2次監査でpod_neck/eye_carrier/camera_carrierが未確定。プレート全体の印刷は対応設計が決まってから行う。既印刷品は保持し、確認済み部品だけ別プレートで試す場合は対象名と版をコメントへ残す。

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
      blocked_by=["P-06", "RV-08"],
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
      blocked_by=["P-02", "RV-09"],
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
- [ ] 無負荷・リンクを外した1個だけ接続し、1500µsから小刻みに可動端を測定。異音・停止・電流増加で戻す。500/2500µsへ直接飛ばさない
- [ ] 実測角度とパルスの表を個体/型番ごとに記録。270°品を含む場合、全20軸共通の`DEG_RANGE`変更では他のサーボが狂うため、型番別またはch別の校正が必要
- [ ] 購入型番の許容電圧内で実施。測定器に電流測定機能と十分な定格があることを確認 (PM7aでは測れない)
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
腕6 + 目2のハーネス。8個の合計電流と現物基板の許容電流は未確認のため、電源は外部バスへ分岐しPCAは信号+GNDとする。各型番の6V適合も確認する。
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
使用サーボの型番を {{{{P-03}}}} で確認する。DS3218メーカー値は5Vで18kgf·cm/ストール1.8A、6.8Vで21.5kgf·cm/2.2A。6V連続保持は未実測。7.4Vを加えない。

## 判定基準 ([assembly.md §1-7]({D}/assembly.md))
coxa をバイスで水平固定し、6V 給電で:
- [ ] スイープ動作で干渉・ガタ・ホーン緩みなし
- [ ] **股ピッチ軸から足先まで水平 155mm** の姿勢で足先に **1.2kg の錘** → 股ピッチ ≈ **18.6 kgf·cm** を 10 秒保持
  (脱調・振動・ホーン滑りなし)。`sim_gait.py` [3] の全域最悪 18.5 kgf·cm (総重量 3.0kg・実重心 y=-39mm・
  3/4 点支持静力学, 2026-09-04) と同等の負荷。**レバー長を必ず写真で残す** (旧「1.2kg (40%)」はレバー未指定で不定だった)
- [ ] 無負荷から段階的に加重し保持電流・電圧・温度・時間を記録。保持不能・振動・滑り・急な電流増加で負荷を下ろす。期待電流1.5〜2.2Aと旧1.5A基準は未実測のため撤回。10秒保持だけで連続運転可能とは判定しない
- [ ] 膝ネック増厚部 (tibia 外側 +3mm, 2026-09-04 M-01) に白化・割れなし
- [ ] 6V 給電での実測ストールトルク (バネ秤, 参考) — DS3218 系の定格は出典で 18〜21.5 kgf·cm とばらつく (S-06)

## Go の場合
{{{{L-03}}}} {{{{L-04}}}} {{{{L-05}}}} を解放 (3 人並行可)。

## No-Go の場合 (持ち上がらない / 電流過大 / ホーンが滑る)
**全数印刷へ進まない。** 原因を `type/不具合` イシューに切り分け (サーボ実力不足 / ホーン結合 / リンク長 / 電圧降下) →
設計値見直し → `config.py` → `build_all.py` → 再テスト印刷 のループ。設計側の想定トルクは**股ピッチ最悪 18.5 kgf·cm**
(2026-09-04 全域再計算。旧 8.9 は 1 点評価) で DS3218 の 6V 実力 (~18) と同水準 = **余裕ほぼゼロ**。サーボ実力不足なら
{{{{L-11}}}} (定格内の高トルク品 / 低トルク歩容 / 軽量化) をオーナー判断。

## 証拠
リフト動画、電流計の読み (写真)、錘の重量。
""")

for _k, _n, _v, _st in (("L-03", "FR", "ミラー版 `_m`", "coxa_bracket_m / femur_link_m / tibia_link_m"),
                        ("L-04", "RL", "ミラー版 `_m`", "coxa_bracket_m / femur_link_m / tibia_link_m"),
                        ("L-05", "RR", "標準版", "coxa_bracket / femur_link / tibia_link")):
    issue(_k, f"{_k} [組立] 脚 {_n} ({_v}) を組む",
          parent="E4", milestone="M2 頭無し歩行",
          labels=["type/タスク", "area/組立", "並行作業OK", "prio/P0"],
          blocked_by=["L-02", "PR-11"],
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
**2026-09-05起動修正**: 本体を支持台で支え、通常版の初期脱力からWeb UI「立つ」で順次通電する。全軸の中立・方向・立位角を確認してから床へ下ろす。床上で順次起動すると未通電軸が折れて転倒することを物理計算で再現済み。USB接続時はDevKitCへの外部5V線を外す。

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
旧動画は全機の接触・材料・起動を証明しない。最新の {{{{I-08}}}} で用いた形状・接触条件・失敗例を確認し、支持台から始めて実機で:
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
      blocked_by=[],
      body=f"""
## 決めること
検討は歩行ゲートの合格を待たずに着手する。{{{{L-10}}}} が電源不足でNo-Goになった場合も対策できるよう、
L-10のCloseは前提にしない。EL-01/L-10の試験ログ（失敗ログも可）を判断に用いる。
歩行電流9〜14Aは測定前の仮定。HOBBYWING UBEC-10A-V2 の
連続 10A / ピーク 15A と同水準で、出力 6.0V 設定時の入力余裕 (2S 末期 6.4V カット) も小さい。
30603003 (V2)と旧型30603000 (ピーク20A)を混同しない。注文記録のHENGE 8Aとも別製品。
歩行電流9〜14Aは測定前の仮定であり、正機械電力から保持電流や銅損を逆算しない。

## 判断材料 ({{{{L-10}}}} が貼る)
- 歩行 (前進/旋回) 中の 6V バス電流のピーク・平均 (クランプメータ)、バッテリー電圧の落ち込み
- UBEC の発熱、ブラウンアウト (ESP32 リセット / サーボ脱力) の有無

## 決定の書き方
サーボバス・5Vバス・電池入力を別々に測る。入力は `(6 Iservo/ηUBEC + 5 Ilogic/ηDC)/Vbat`。
スイッチのDC定格、ヒューズの溶断特性、配線・接続部の許容電流と温度上昇を照合する。
「現物で可 / 電源やスイッチ変更が必要」を理由付きでコメントし、交換なら
`docs/BOM.md` #6/#12 と `docs/wiring.md` を更新して Close。

## 注意
十分な定格のDCクランプメータ、または電流シャントと記録器で測る。10A直列テスターを全機へ挿入しない。
SANWA PM7aに電流測定機能はない。型番と許容時間を確認した計器を使う。
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

issue("L-11", "L-11 [要判断] 股ピッチの実力を測り、低トルク歩容・軽量化・6V対応品を比較する",
      parent="E4", milestone="M1 片脚 Go/No-Go",
      labels=["type/要判断", "area/試験", "area/電装", "prio/P0"],
      blocked_by=[],
      body=f"""
## 決めること
検討は片脚ゲートの合格を待たずに着手する。{{{{L-02}}}} の成功/失敗ログと{{{{P-03}}}}の現物型番・寸法で判断する。
全位相の静力学では股ピッチ最大18.5 kgf·cmだったが、形状・質量修正後の最新版を再計算する。
静力学の一瞬の必要トルクと、実サーボが継続して支えられるトルクを区別する。

## 一次資料 (2026-09-05確認)
- DS3218メーカーPDF: 4.8–6.8V、5Vで18 kgf·cm / 1.8A、6.8Vで21.5 kgf·cm / 2.2A (ストール)。6Vの直線補間は19.94 kgf·cmだが実測値でも連続定格でもない。180°/270°品がある
- Hiwonder LD-220MG公式: 6–8.4V、7.4Vで20 kgf·cm、ケース高51.4mm。DS3218と形状・電気特性を共用しない
- 注文記録のLD-20MGと持込図面LD-220MGは一致していない。現物が判明するまで代入しない
- DS3218へ7.4Vを掛けない。購入済み2S電池・6V運用を活かす順に比較する

## 比較する案
1. 歩幅・スタンス・体高の変更: 全指令/全位相の静力学と動力学、干渉、足裏支持を同時に評価。旧案の18.5→17.0という数字は新モデルで未再検証
2. Cabin内側の軽量化: 外観、壁厚、基板保持、重心、首梁強度を併せて再計算
3. 許容電圧6V・実ケース寸法が合うサーボ: 現物の力不足が再現した場合に限って必要軸数と追加費用を比較

## 完了条件
- [ ] 無負荷から段階的に荷重し、トルク・パルス・角度誤差・電圧・電流・温度・継続時間を記録
- [ ] 短時間保持と長時間保持を区別し、限界試験を歩行可能の証明にしない
- [ ] 採用案を同じ質量・形状・電圧・動作条件で比較し、`config.py` / `config.h` / BOM / 再印刷対象へ反映

資料: https://www.dsservo.com/down.asp?id=22 / https://www.hiwonder.com/products/ld-220mg
保存PDFと電源計算: docs/audits/20260905-round2/。通電試験そのものは未実施。
""")

issue("A-01", "A-01 [組立] 右腕チェーンをベンチで組む (肩ブラケット→上腕→前腕→固定爪)",
      parent="E5", milestone="M3 サブアセンブリ",
      labels=["type/タスク", "area/組立", "skill/模型仕上げ", "並行作業OK", "prio/P1"],
      blocked_by=["PR-06", "PR-01", "PR-02", "EL-04", "RV-10", "RV-17"],
      body=f"""
## ゴール
右腕 (標準版: shoulder_bracket / upper_arm / forearm / claw_mount)。左腕 ({{{{A-02}}}}) と並行可。
{_ARM_STEPS}
""")

issue("A-02", "A-02 [組立] 左腕チェーンをベンチで組む (_L パーツ)",
      parent="E5", milestone="M3 サブアセンブリ",
      labels=["type/タスク", "area/組立", "skill/模型仕上げ", "並行作業OK", "prio/P1"],
      blocked_by=["PR-06", "PR-01", "PR-02", "EL-04", "RV-10", "RV-17"],
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
- elbow_shell (元 Elbow 球の半殻, 印刷済) は改訂配置へ合わせる。殻の原球中心は肘軸から局所Y=-13.8mm。殻の欠き底とサーボのケース底を混同しない。0.3mm設計隙間と接着保持を現物で確認
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
第2次監査の追加条件: 子基板だけでなくXIAO本体・USB端子・FPCの長さ/曲げ・保持/挿入を実メッシュで確認する。「頭内の空きスペースへテープ留め」は未検証のまま採用しない。

2026-09-05のSeeed公開図面: OV3660は対角102°/水平85°、EFL1.63mm±5%、センサー部8×8×5.30mm。
OV2640用の瞳10mm/後退6.57mmではケラレる。現物がOV3660なら外観を保つ後退≤4.05mmの内部キャリア候補を試す。
型番未確認のままCAM2設定を上書きしない。図面はdocs/audits/20260905-round2/primary-sources/に保存。

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
      blocked_by=["PR-07", "PR-06", "P-02", "RV-08"],
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
| 5V | MAX98357A VIN (Adafruit #3006はGAIN/SD未結線可。互換基板はSD回路・電圧確認) |

1. MAX98357A は頭部/シャーシ側、砲身内のスピーカーへは SPK 2 線のみ
2. `.venv/bin/python tools/voice_bridge.py --mock --self-test` (オフライン) → 次に `--mock` で実機接続: Web UI ボイスタブの PTT でマイク音声がブリッジへ届く / スピーカーから 440Hz トーン

## 完了条件 (DoD)
- [ ] 配線写真、`--mock --self-test` の PASS ログ
- [ ] PTT 録音が届いた証拠 (ブリッジ側ログ) と 440Hz 再生の動画
""")

issue("H-05", "H-05 [組立] Mouth 一式 (Ball→Neck→Cannon チェーン + Cap/Key/Peg) をキット標準で組む",
      parent="E6", milestone="M3 サブアセンブリ",
      labels=["type/タスク", "area/組立", "skill/模型仕上げ", "並行作業OK", "prio/P1"],
      blocked_by=["H-03", "PR-01", "RV-08"],
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

issue("H-06", "H-06 [CAD] Cabin内部の電装室・基板保持・挿入経路・配線を成立させる",
      parent="E6", milestone="M4 フルドレス",
      labels=["type/不具合", "area/CAD", "area/電装", "prio/P0", "並行作業OK"],
      body=f"""
## 背景
2026-08-22 の実占有ボクセル場での網羅探索で、**Head_Top 内に ESP32 DevKit (58×28) を置ける姿勢は存在しない**
と確定 ([HANDOFF §6]({D}/HANDOFF.md), `config.py` ESP32_SLOT コメント)。歩行実験 (頭無し) はテープ留めで支障なし。
**Head_Top を被せる ({{{{S-03}}}}) までに決める必要がある。**

第2次監査ではCabin_Front/Backも内部が中実で、テープ留めする空間そのものがないことを確認。
担当: 電装室CAD。外形と既購入基板を保ち、合わせ面から内部を加工する候補を作る。
XIAOのカメラ本体は短いFPCの制約があり、Cabinへ移す仮定は置かず{{{{H-02}}}}で頭内配置を検証する。

## 完了条件
- [ ] 基板本体・端子・USBプラグ・配線を含めた寸法と実購入型番を照合
- [ ] 内部ポケット・着脱棚・保持・挿入経路を実メッシュで検証
- [ ] Cabinの接合ペグとpod_neckの荷重伝達部を保存し、通線穴と強度を{{{{RV-07}}}}へ引き継ぐ
- [ ] 現行STL/印刷済み品と修正版を区別し、必要な加工・再印刷を一覧化
- [ ] 変更形状からURDF質量・重心を更新し{{{{I-08}}}}で再検証

内部加工は今回の修正依頼の範囲で進める。可視外装の変更が避けられない場合だけ寸法付き案を提示する。
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
2026-09-05修正版は(c)としてch12を常時停止にした。SG90は予備として保存。出力停止のホスト試験はRV-01、実機で予備端子未接続を確認してClose。
""")


# ===========================================================================
# E7 意匠シェル・Cabin・仕上げ
# ===========================================================================
issue("S-01", "S-01 [組立] pod_neck 取付 + TailJoint (青コーン+Ball リング) 化粧スリーブ接着",
      parent="E7", milestone="M4 フルドレス",
      labels=["type/タスク", "area/組立", "res/本体", "prio/P1"],
      blocked_by=["PR-06", "PR-01", "L-06", "RV-15", "RV-07"],
      body=f"""
第2次監査で、梁とTailJointBlue約3282mm³、Ball約2048mm³の実交差が判明。元の「無加工で丸ポストへ被せる」は全長では成立しない。{{{{RV-15}}}}で全組立と通線/支持を確定し、{{{{RV-07}}}}で印刷強度を確認してから組む。

- [ ] 確定した部品版・断面・取付位置・締結長を記録
- [ ] 無通電で全挿入経路を確認し、押込みや接着で実体重なりを隠さない
- [ ] Cabin/目/頭/梁/外装の隙間と支持荷重を確認
- [ ] 最終固定前に電装を取り出せることを確認

""")

for _k, _n, _sh, _blk in (("S-02a", "FL", "shin_shell (標準)", "L-01"),
                          ("S-02b", "FR", "shin_shell_m (ミラー)", "L-03"),
                          ("S-02c", "RL", "shin_shell_m (ミラー)", "L-04"),
                          ("S-02d", "RR", "shin_shell (標準)", "L-05")):
    issue(_k, f"{_k} [仕上げ] 脚 {_n} の装飾 — thigh_cap / Thigh_Guard / {_sh} / Shin_Guard / Leg_Toe ×3",
          parent="E7", milestone="M4 フルドレス",
          labels=["type/タスク", "area/仕上げ", "skill/模型仕上げ", "並行作業OK", "prio/P2"],
          blocked_by=["L-10", "PR-05", "PR-01", "PR-02", "RV-06", "RV-14", "RV-16"],
          body=f"""
## ゴール
{_n} 脚に意匠を付ける。4 脚は独立なので**歩行ゲート ({{{{L-10}}}}) 合格後に 4 人で並行可**。
FR/RL は**ミラー脚**なのでシェルも `_m` を使う ([assembly.md §3]({D}/assembly.md))。

## 手順
1. {{{{RV-16}}}}で成立した受け座/挿入経路に従いthigh_capとLeg_Thigh_Guard_Blueを仮組み。旧embed率の配置はガード体積の81%が埋まり組立できない。写真準拠という旧説明だけで接着しない
2. **{_sh}** (Blue_1 印刷済) を tibia へ下からスライド → **M3×40 ×2 本を横から貫通、ナイロンナット留め**。装飾ドット面が放射外向きになる (shin_rotz=0)
3. Leg_Shin_Guard_Greyは{{{{RV-16}}}}で確定した挿入/保持方法を使用。旧配置は約78%埋没し、単純な差引きでは入口がなく、開放加工の候補では脛殻が分離した
4. **Leg_Toe_Black ×3** ({{{{PR-02}}}}) を leg_foot_bored の甲底面スタブ 3 箇所 (-98.4° / +45.5° / +145.4° の非等間隔) へ瞬着。爪の腹側 (湾曲の凹み) を接地方向へ
5. 膝はメカ剥き出しで確定 (カバー無し)

## 完了条件 (DoD)
- [ ] 脚の完成写真 (外側・内側)。脚を可動域端まで動かしてシェルが隣接脚・腕に当たらない
- [ ] foot_pad が接地し、トゥは装飾 (接地力は foot_pad が受ける) ことを確認
""")

issue("S-03", "S-03 [組立] 頭部組立 — Head_Bottom_Armcut / 目ポッド ×2 + カメラ目装着 / Head 装飾 / Head_Top 被せ",
      parent="E7", milestone="M4 フルドレス",
      labels=["type/タスク", "area/組立", "area/仕上げ", "res/本体", "prio/P1"],
      blocked_by=["H-01", "H-02", "H-06", "PR-08", "PR-01", "PR-02", "L-10", "RV-09", "RV-13"],
      body=f"""
## 手順 ([assembly.md §2.7-5,6 / §2.9-4,5 / §3]({D}/assembly.md))
1. **ESP32 を頭外へ退避** ({{{{H-06}}}} の決定どおり)。頭内にテープ留めできる平面はもう無い
2. **Head_Bottom_Armcut** (浅いボウル, Blue_2 印刷済・カスプ除去版) の上端リング面をプレート下面へホットボンド。Head_Plate / Head_Bottom_Cap は**使わない**
3. **目ポッド ×2**: シェル内側からネック φ24 を Head_Top_Eyecut v2 ({{{{PR-08}}}}) の φ30 ボアへ通し、carrier を内側へ接着 [現物合わせ]。**キャップ底を座グリ床から ~1.5mm 浮かせ**、全回転で床・縁に擦らない。**carrier のロール = ケース長辺が水平接線方向 (左右目は前後方向)** — サーボ尾が中央のカメラ carrier と近づくため必須
4. **カメラ目**: ネック φ28 を中央ボアへ、**瞳が水平前方をまっすぐ向く取付位相**でシェル内側から接着 (残差 ~8.6° は設計値)。本体基板 (XIAO) は {{{{H-02}}}} の現品同定とFPC/基板保持の検証後、確定トレーへ取り付ける
5. Head 装飾 (`Head_Dome_Grey`, `Head_Plug_Grey`, `Head_Screw_Grey` ×2, `Head_Insert_Black` ×4 [2 個は位置未確定 → 実物写真/現物合わせ, {{{{S-08}}}}], `Head_Peg` 上下はダウエル)
6. PCA スタックのプラグを全て挿した状態で **{{{{RV-13}}}}で成立した支持柱/受け座へ固定**。旧7タブは垂直に頭へ届かず点接着では保持できない。目・音声・カメラ配線は確定した通路へ
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
      blocked_by=["PR-01", "PR-02", "PR-03", "H-06", "RV-15"],
      body=f"""
第2次監査でCabinの中実形状と電装配置の不成立を確認。既存部品を保存し、内部電装室 {{{{H-06}}}} の加工・通線方法が決まるまでFront/Backの最終接着をしない。

## ゴール
Cabin_Front / Back / Cabin_Eyeは既印刷品を保存。部品の仮照合は並行できるが、内部収納と首支持/装飾座を確定する前に最終接着しない。
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
      blocked_by=["I-01", "RV-06", "RV-07"],
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

issue("I-08", "I-08 [検証] 実制御・実ケース・材料別接触で物理シミュレーションを検証する",
      parent="E8", milestone="M5 統合・完成",
      labels=["type/タスク", "area/ファーム", "skill/CAD-Python", "並行作業OK", "prio/P0"],
      body="""
2026-09-05独立監査の物理担当。実C++のGait/LegOutput/Arms/Servosを50Hzで実行し、PWM量子化・スルー制限・20軸の順次通電を含める。1563フレームの出力照合は最大0.000895度差。

旧前進軸、停止時SWAY、省略されていた出力段、自由基部の架空ダンピング、転倒時exit0を修正した。部品別接触と凸分解を比較し、硬いトゥとTPUを別材料で評価する。
MuJoCoコンパイル後に慣性補正/質量/粘性を変更して派生定数が更新されない不具合も修正。設定はコンパイル前へ移し、部分木質量・逆慣性の再計算差0を検証する。分解キャッシュは設定/版のハッシュと原子的保存で並列実行時の読掛けを防ぐ。

- [ ] 修正後の同じSTL/URDF/制御ハッシュで方向、体高、停止再開、電圧/荷重/摩擦/トルク、坂/段差/外乱、長時間を比較
- [ ] 関節反力・速度・トルク飽和・材料別床反力・数値警告・転倒を保存
- [ ] 設計基準ケースを含む18姿勢の自己干渉と、{{RV-12}}の固定リンク内全組合せを分けて記録
- [ ] 刻み幅・未同定の慣性補正/粘性/制御ゲインへの感度を確認
- [ ] 実積分後のqposから動画を作り、演出用の運動学動画と区別する

現在の6V名目トルクはメーカー端点の内挿による1.956N·m。資料と異なる購入個体/連続トルク/電流/印刷摩擦は未同定。初期干渉で成立しないモデルを、有効な歩行合格に数えない。
{{RV-06}}足裏支持、{{RV-07}}印刷強度、{{RV-09}}頭部収納は、条件付きの歩容合格では解決しない。
根拠: docs/audits/20260905-round2/simulation.md。旧結果と中断した入力版は履歴として保存し、最終根拠へ流用しない。コードのGitHub反映と実機試験は別に記録する。

## 2026-09-05 監査結果の公開参照

[監査コミット 0de7154](https://github.com/hapx2yuki/Tachikoma/commit/0de71547c1af59abc6bcc3484b03b12e9430df2b)。設計基準ケースを用いた89条件の動的計算、実C++出力に基づく18姿勢の実体干渉、物理用回帰16試験を完了し、入力ハッシュ一致を確認した。基本71条件は58合格・2不合格・3転倒・8初期接触モデル不成立。未採用TPU靴4条件と追加の初期配置/関節条件/刻み比較14条件は別記録として残す。

[物理監査報告](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/simulation.md) / [全89条件・18姿勢の確認記録](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/simulation/final_refresh_evidence.json) / [16回帰と実C++照合](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/final-geometry/verify/simulation-regression.log)。

DS3218等の設計基準ケースと、注文記録のLD20MG/LD220MG等の購入個体との一致は {{P-03}} で未確認。実購入ケース確認済みとは扱わず、照合後の寸法・トルク等は {{RV-09}} と連携して再検証する。本課題は今回Closeしない。
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
# 2026-09-05 再監査。物理作業の既存イシューは証拠なしで閉じない。
# ---------------------------------------------------------------------------
issue("PR-11", "PR-11 [印刷] 片脚合格後に残りのtibiaを標準1本・ミラー2本印刷する",
      parent="E2", milestone="M2 頭無し歩行",
      labels=["type/タスク", "area/印刷", "res/プリンタ", "prio/P0"],
      blocked_by=["L-02"], body="""
{{PR-04}} の標準1本で {{L-02}} の1.2kg保持・干渉・発熱検査に合格してから残りを印刷する。
すでに印刷済みなら新規印刷せず {{P-02}} の版・向き・壁数・強度確認で再利用可否を記録する。

- [ ] 標準tibia_link ×1 (RR)、tibia_link_m ×2 (FR/RL) が現行の形状・印刷条件に適合
- [ ] 4壁/40%の設定、標準/ミラーの識別、写真を記録
- [ ] {{L-03}}/{{L-04}}/{{L-05}}へ引き渡し

現物3MFは別名保存して必要数のみ配置。プリンタは他のres/プリンタ作業と同時使用しない。
""")
issue("E9", "E9 [監査] 実機製作前提を独立検証し、修正と実機確認を切り分ける",
      labels=["type/エピック", "area/試験", "prio/P0"], body="""
2026-09-05 ユーザー依頼による全面再監査。購入済み・印刷済み部品とキット外観を維持する。
担当: Codex（ローカル作業、オーナー依頼）。実機への書込・印刷・追加購入はこの監査に含めない。

並行: {{RV-01}} 制御、{{RV-02}} 印刷成果物、{{I-08}} 物理シム、{{RV-04}} Issue同期。
直列: 個別修正と {{RV-03}} 検査の終了判定 → {{RV-05}} 全体検証・実機条件の引き継ぎ。
同じファイルの編集者を1人に限定。STL再生成→頭部加工→URDF再生成→最終検証は直列。

完了条件: 子作業の根拠・修正結果を記録し、残る実測を既存の P-03/L-02/L-10/I-01/I-02 等へ結ぶ。
ローカル合格・GitHubへのコード反映・実機合格は別々に記録する。

## 2026-09-05 監査結果の公開参照

[監査コミット 0de7154](https://github.com/hapx2yuki/Tachikoma/commit/0de71547c1af59abc6bcc3484b03b12e9430df2b)。STL/頭加工/URDFの生成3工程を完了。最終23検査は14合格・8不合格・1未検証、回帰53試験と物理用回帰16試験は合格。監査の実施・修正・記録が完了した範囲と、機体の組立・歩行が未成立の範囲を分ける。

[監査総括](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/README.md) / [23検査の実行結果](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/final-geometry/verify/verify-results.json) / [製作への影響](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/manufacturing.md)。

未解決: {{RV-06}} 足裏支持、{{RV-07}} 印刷強度、{{RV-08}} 口のキー公差、{{RV-09}} 頭内のケース収納、{{RV-10}} 肩と前脚のケース干渉、{{RV-11}} 電装の現物条件、{{RV-13}} 頭支持、{{RV-14}} 脚カバー、{{RV-15}} 首/Cabin、{{RV-16}} 脚ガード、{{RV-17}} 固定手、{{H-06}} 電装室。購入個体は {{P-03}}、印刷済み品は {{P-02}}/{{P-05}} で照合する。

実機書込・追加購入・印刷は未実施。残る子作業と全体ゲートがあるため本課題はOPENを維持する。
""")
issue("RV-01", "RV-01 [不具合] 操作画面・低電圧停止・腕脚ガードを修正して回帰検証する",
      parent="E9", labels=["type/不具合", "area/ファーム", "prio/P0", "並行作業OK"], body="""
担当: Codex 制御監査。実装所有: firmware/src、tools/voice_bridge.py と専用ホスト試験。

再現された問題: Web UI が変数初期化前の参照で停止、校正モードが低電圧停止を無視、
腕退避がスルーレート前の脚目標を参照して早く解除される。起動時の順次通電も確認する。

- [x] 修正前の再現と原因を記録
- [x] 操作画面、校正、本番の保護経路を最小修正
- [x] 実コードを用いたホスト回帰試験と通常/校正ビルド
- [x] {{I-08}} とサーボ出力段のモデルを照合

第2次ではVBAT0V見逃し、PCA書込失敗、UI再読込での再通電、未使用ch12、I2S形式/部分書込、MJPEG無限応答とTTS取消も修正。
ログ: docs/audits/20260905-round2/firmware.md。実機動作は {{EL-03}}/{{EL-07}}/{{L-09}} で別途確認。

## 2026-09-05 監査結果の公開参照

[監査コミット 0de7154](https://github.com/hapx2yuki/Tachikoma/commit/0de71547c1af59abc6bcc3484b03b12e9430df2b)。この課題の修正・ソフト検証は完了。実main/UIの故障注入、通常/校正ビルド、実C++出力1563フレームの照合を実施し、最大角度差は0.000895度。

[制御・音声の再現と修正](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/firmware.md) / [ホスト実行と通常・校正ビルド](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/final-geometry/verify/firmware-host-and-build.log) / [出力照合を含む物理用回帰](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/final-geometry/verify/simulation-regression.log)。

実機書込・サーボ位相/符号・音響は未確認で、{{EL-03}}/{{EL-07}}/{{L-09}}/{{H-04}}へ引き継ぐ。I2C断線・CPU停止中の既存PCAのPWMはソフトだけでは停止保証できず、独立OE/電源遮断は {{RV-11}} の実機条件として残す。
""")
issue("RV-02", "RV-02 [検証] 現物3MFの形状・数量・材質と再生成時の保持を検査する",
      parent="E9", labels=["type/不具合", "area/印刷", "area/CAD", "prio/P0", "並行作業OK"], body="""
担当: Codex 機構監査。既存3MFには印刷進行に合わせた手動変更があるため一括上書きしない。
現行検査の体積・高さだけではXY形状や材質の相違を見逃す。foot_pad生成時のTPU割当も調べる。

- [x] 現物3MFと元STLの独立比較、数量・配置・材料・未検査項目を報告
- [x] 生成コードの確定不具合を修正、現物ファイルは保存
- [x] 再印刷が必要な部品と現物確認だけでよい部品を区別

リポジトリ直下のPhase0を含む既存27個すべてを検査し、元3MFは保存。
ログ: docs/audits/20260905-round2/manufacturing.md。実際の印刷済み品の版は {{P-02}}、再印刷判断は {{P-05}}。

## 2026-09-05 監査結果の公開参照

[監査コミット 0de7154](https://github.com/hapx2yuki/Tachikoma/commit/0de71547c1af59abc6bcc3484b03b12e9430df2b)。この課題の3MF検査と生成コード修正は完了。既存27個のうち9個が現行STLと不一致。印刷用STLの変更12個は形状変更10個・配置変更2個に分け、元モデル58個・3MF27個・旧画像動画39個の保持を確認した。

[製作への影響と再使用判断](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/manufacturing.md) / [原型・3MF・旧媒体の保持記録](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/manufacturing-preservation.json) / [生成・比較処理の回帰ログ](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/final-geometry/verify/regression.log)。

既印刷品の現物検品や再印刷判断まで完了した意味ではない。{{P-02}}/{{P-05}}で使用版・材質・壁数・必要な加工を確定する。既存3MFは上書きせず、追加印刷も行っていない。
""")
issue("RV-03", "RV-03 [不具合] 幾何・歩容検査のNGと計算失敗を終了コードへ反映する",
      parent="E9", labels=["type/不具合", "area/CAD", "area/試験", "prio/P0"], body="""
担当: Codex 検証基盤。check_leg_assembly.py と sim_gait.py は NG を印字しても終了コード0。
脚のブーリアン失敗もNaNへ変換され、max比較により見逃し得る。

- [x] NG条件と計算失敗の再現試験
- [x] 判定閾値を緩めず、失敗を非0終了にする
- [x] 保存先指定で既存の検証画像を保持できる
- [x] 正常な実形状の再検証結果を保存

トルク余裕・接地・実測未完了は、数値チェッカーの合格とは別に報告する。

## 2026-09-05 監査結果の公開参照

[監査コミット 0de7154](https://github.com/hapx2yuki/Tachikoma/commit/0de71547c1af59abc6bcc3484b03b12e9430df2b)。検査器の不正な合格判定を修正し、負例と実形状の再実行を記録した。回帰53試験は合格。実形状の最終検査では8不合格・1未検証を非0終了として保存し、既知の不具合を免除していない。

[検査基盤の独立レビューと再現](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/integration-peer-review.md) / [負例を含む回帰ログ](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/final-geometry/verify/regression.log) / [最終23検査と終了判定](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/final-geometry/verify/verify-results.json)。

この課題の完了は検査器の修正完了を示す。足裏支持、実体干渉、印刷強度、電源・実機条件の不合格/未検証は対応課題へ残す。
""")
issue("RV-04", "RV-04 [不具合] Issue同期で進捗を保持し、重複と依存関係を検証する",
      parent="E9", labels=["type/不具合", "area/運営", "prio/P1", "並行作業OK"], body="""
担当: Codex Issue管理。現行 setup_project.sh は再実行時に全件の Status をTodo/Readyへ戻し、
既存ステータスの選択肢を再生成する。完了・着手中の作業を壊さず同期する。

- [x] 既存Projectを再利用し、既存Status/担当/コメントを保持
- [x] 同期対象を限定し、無関係なIssue本文を上書きしない
- [x] 同じ文書生成を2回実行しても節が増えない
- [x] dry-runでも既存本文・ラベルの変更を表示し、POST/PATCHしない
- [x] 管理ラベルの旧分類を置換し、利用者が付けた独自ラベルは保持
- [x] 依存循環・重複キーを検出、変更不要の再実行を確認

運用根拠: https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/creating-issue-dependencies

## 2026-09-05 監査結果の公開参照

[監査コミット 0de7154](https://github.com/hapx2yuki/Tachikoma/commit/0de71547c1af59abc6bcc3484b03b12e9430df2b)。同期処理の修正と監査時点の照合は完了。96課題・173依存の親子/依存が一致し、既存の設定済みStatus変更は0件。対象限定、再実行、dry-run、管理ラベルと独自ラベルの扱いを回帰検証した。

[親子・依存の全件照合](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/github-relations-final.json) / [既存Statusの保持](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/project-status-preservation.json) / [Issue・記録処理の回帰ログ](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/issue-and-coverage-final-regression.log)。

上記件数は監査コミット時点の記録。課題のCloseや担当者の今後の進捗更新を自動的な検証合格と混同しない。実機の製作・測定を完了扱いにする同期は行わない。
""")
issue("RV-05", "RV-05 [ゲート] 修正後の全検証と未確認の実機条件を記録する",
      parent="E9", labels=["type/ゲート", "area/試験", "prio/P0"],
      blocked_by=["RV-01", "RV-02", "RV-03", "RV-04", "I-08", "RV-08", "RV-09", "RV-10", "RV-11", "RV-12", "RV-13", "RV-14", "RV-15", "RV-16", "RV-17", "H-06"], body="""
第2次はソース・文書・3MF・全STL/旧URDF・画像/動画を記録付きで確認する。修正→STL生成→URDF生成→自己干渉/材料別接触を含む物理計算を直列に実行し、失敗条件も保存する。合格項目だけの抽出で完了扱いにしない。

担当: Codex 統合。個別監査は並行できるが、最終判定は修正が出揃ってから行う。

- [ ] AGENTS.md の全コマンドと脚リンク強度を実行し結果を保存
- [ ] 現行firmwareに対応するMuJoCoの前進・旋回・停止と失敗条件を記録
- [ ] 機構/電装/制御/印刷の指摘を重大度順に根拠付きで一覧化
- [ ] 購入済み部品への影響と実測の順序を既存Issueへ反映

実機未確認のまま「組立可能」「歩行実証済み」と結論しない。
ローカル成果物: docs/audits/20260905-round2/README.md。実機ゲート自体を閉じる作業ではない。

## 2026-09-05 監査結果の公開参照

[監査コミット 0de7154](https://github.com/hapx2yuki/Tachikoma/commit/0de71547c1af59abc6bcc3484b03b12e9430df2b)。STL/頭加工/URDFの生成3工程を完了。最終23検査は14合格・8不合格・1未検証、回帰53試験と物理用回帰16試験は合格。監査の実施・修正・記録が完了した範囲と、機体の組立・歩行が未成立の範囲を分ける。

[監査総括](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/README.md) / [最終検査結果](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/final-geometry/verify/verify-results.json) / [物理計算の最終確認](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/simulation/final_refresh_evidence.json)。

未解決: {{RV-06}} 足裏支持、{{RV-07}} 印刷強度、{{RV-08}} 口のキー公差、{{RV-09}} 頭内のケース収納、{{RV-10}} 肩と前脚のケース干渉、{{RV-11}} 電装の現物条件、{{RV-13}} 頭支持、{{RV-14}} 脚カバー、{{RV-15}} 首/Cabin、{{RV-16}} 脚ガード、{{RV-17}} 固定手、{{H-06}} 電装室。購入個体は {{P-03}}、印刷済み品は {{P-02}}/{{P-05}} で照合する。

記録の公開を全体合格に読み替えず、本課題と依存する機構・実機ゲートはOPENを維持する。
""")
issue("RV-06", "RV-06 [機構不具合] トゥと硬い足本体の先行接地を解消して足裏支持を成立させる",
      parent="E4", labels=["type/不具合", "area/CAD", "area/試験", "prio/P0"], body="""
2026-09-05第2次でトゥ12個の変換が100%、印刷は150%という不一致を修正。実150%で体高115mm・停止保持の後脚トゥ最低z=-13.335mm、TPU足裏+8.699mm、差22.034mm。前脚も18.102mm先行し、体高110〜130mm/小歩幅〜最大指令で解消しない。
トゥを外しても、硬い足本体がTPUより先に触れる姿勢がある。
根拠: tools/check_toe_contact.py、docs/audits/20260905-round2/。足の取付角だけの1441候補では解消せず、1.5mm TPU靴を作成。24960姿勢の角度範囲で最低1.642mm先行。材料別の4物理条件も比較し、接着/実摩擦/たわみは未検証として残す。

旧check_leg_assemblyは硬足+TPUの最下点を検査しており、TPU単独の接地保証ではなかった。
旧MuJoCoの単一凸包/同じ摩擦は根拠にならない。第2次では材料別形状と摩擦・反力積分へ修正済み。

方針: キット外観・購入済みサーボを保存し、まず着脱できる足裏支持部/内部取付の変更候補を検討する。
見えるトゥの切削や形状変更はオーナー判断。現行STL/3MFを上書きしない。

- [ ] 現物の取付位相・寸法を照合（CAD上の誤配置か実物と同じか）
- [ ] 全到達姿勢でTPUが先に接地し、可動域・強度・外観を満たす具体案
- [ ] 原寸の試験部品1脚で荷重・摩擦・たわみ・接地を確認
- [ ] config/生成/実形状チェッカー/URDF/物理シムを整合させ全回帰

{{I-02}} の前提。{{L-02}}/{{L-10}} は頭無し・装飾無しで条件を限定し別途実施できるが、硬足の先行も観察する。
""")
issue("RV-07", "RV-07 [試験] 実際の壁数・充填率による脚とポッド支持の強度を確かめる",
      parent="E7", labels=["type/ゲート", "area/測定", "area/印刷", "prio/P0"], body="""
2026-09-05監査。強度チェッカーは中実のSTL断面と文献強度を使用するが、実印刷は4壁/40%。
pod_neckの安全率3.23、tibiaの約2.1は印刷物の強度を測った値ではない。
独立の壁+低密度コア感度計算ではpod_neck安全率が3.0未満となり得る（破断の確定ではない）。
また単発 claw_mount_L.3mf には腕7部品が壁2/15%で保存されている。そこから刷った品の履歴を確認する。

- [ ] {{P-02}}で実際に用いた3MF・方向・材・壁数・充填率を特定
- [ ] 既印刷品を捨てず、現物/同条件試験片で荷重・たわみ・残留変形を確認
- [ ] ポッドを仮支持した状態で段階荷重し、たわみ・層割れ・締結ゆるみを記録
- [ ] 必要なら外観を変えず内部補強/印刷条件の変更を検討し、変更前後の根拠を残す

根拠: docs/audits/20260905-round2/mechanical.md。首のφ8通線/補強候補は{{RV-15}}と連携し、全長の装飾交差まで解消後に強度を確定。{{I-02}} の前提。サーボトルク試験{{L-02}}とは別の材料・部材の確認。
""")


issue("RV-08", "RV-08 [機構不具合] マイクの挿入経路とBall–Neckの実体干渉を直す",
      parent="E6", labels=["type/不具合", "area/CAD", "prio/P0", "並行作業OK"], body="""
担当: 音声機構。make_audio.py、check_audio_assembly.pyを所有。
旧砲身はφ16.4mmのマイク受けがφ10.7mmの開口を通らず、ポケット間に0.72mmの壁も残る。第2次ではシャーシ–MouthBall1149.468/Neck115.507mm³、Cap–Neck112.880mm³も再現し、固定内部逃げと前タブの支持カラーを追加。前タブの板厚増3mmに対応するねじ長さを頭支持設計RV-13と照合する。
Ball–Neckは1,862.7mm³重なり、接着で成立する組合せではない。

- [ ] 見える外形と取付軸を維持し、砲口からの挿入ボア/キー溝とNeck内側の球面受けを作る
- [ ] 実挿入経路・組立状態で交差0、単一閉体、配線経路と残肉を検査
- [ ] 元の印刷物と再印刷/内部加工の対象を明示、旧3MFを上書きしない
- [ ] STL再生成後の検査を保存して音声組立へ引き渡す

根拠と修正: docs/audits/20260905-round2/。音響・接着・印刷公差の実機確認はH-03/H-04で行う。

## 2026-09-05 監査結果の公開参照

[監査コミット 0de7154](https://github.com/hapx2yuki/Tachikoma/commit/0de71547c1af59abc6bcc3484b03b12e9430df2b)。砲口の挿入ボア/キー溝、Neck内のBall/Cap座、シャーシ逃げと前タブ補強は実装済み。口とシャーシの挿入363姿勢、支持材/リング保持を検査した。

[口・シャーシの検査](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/final-geometry/verify/check_mouth_chassis.json) / [製作への変更](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/manufacturing.md) / [残る固定部品の分類](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/static-intersection-final-triage.json)。

小さいMouth_Keyとの体積共有がCannon側0.27493mm³、Cap側0.07012mm³残り、公差・保持が未確認。音響/接着/印刷公差は {{H-03}}/{{H-04}}、前タブ増厚に対応する締結長は {{RV-13}} で確認する。全体の交差0や実機組立は未達のためOPENを維持する。
""")
issue("RV-09", "RV-09 [機構不具合] 頭部内の実サーボケースとポッド梁の干渉を解消する",
      parent="E7", labels=["type/不具合", "area/CAD", "prio/P0", "並行作業OK"], body="""
担当: 頭部機構。make_head_eyecut.py、make_head.pyと対応チェッカーを所有。
旧検査は下向きDS3218のケースをタブから11mmだけとしていたが、上側に約28mm必要。目の固定ケース/保持板まで含めた配置探索は未成立。支持柱{{RV-13}}、カバー{{RV-14}}、首/Cabin{{RV-15}}のため取付軸の寸法を先に確定する。
Head_Topと各ケースが597〜1,277mm³重なる。Head_Top–pod_neckも89.23mm³の実体交差。

- [ ] メーカー図面・現物型番・軸/タブ/ケース寸法を別々に検査
- [ ] 殻の凸包外へ出るケースを内部削りだけで解決した扱いにせず、軸/高さ/向きの有限候補を比較
- [ ] 外観変更なしの最小案を数値検証。可視変更が不可避なら寸法付き比較案を提示
- [ ] 梁/殻の隠れた干渉を除去し、挿入・壁厚・締結・電装も確認
- [ ] 同じ形状からURDFと物理検証を再生成

全姿勢の基板収容はH-06、カメラ本体はH-02と連携。設計を満たさないものは未解決として残す。
""")
issue("RV-10", "RV-10 [機構不具合] 腕と脚の隣接部品同士の自己干渉を取り除く",
      parent="E5", labels=["type/不具合", "area/CAD", "prio/P0", "並行作業OK"], body="""
担当: リンク機構。make_arm.pyとリンク生成を所有。
URDFを使わない生STLでも肩–上腕約48mm³、上腕–前腕は肘40°で396mm³の交差を再現。
肘殻の原球中心をケース底に合わせ局所Y=-13.8mmへ移し、前腕後面だけ追加148.794mm³を逃がした。肩/肘4可動ペア1664姿勢で交差0、ホーンと手首端面を維持。指内部の嵌合は{{RV-17}}、脚のガード座は{{RV-16}}へ分離。
脚カバー/シャーシは{{RV-14}}へ分離。肩pitchケースと前脚pitchケースは停止でも413/451mm³交差し、主ケースを軸周り12方向×18姿勢で比較しても未成立。頭内の軸配置{{RV-09}}と合わせて解決する。
第2次では通常停止でもcoxa背面40.68mm³を再現。天板後端のみ短縮し箱枠を保持、全6ペア約199万組と格子間回転上界を控除して最小隙間0.963mm。さらに天板合成がサーボポケットを埋め戻し、各pitch主ケース1563.87mm³が重なることを固定リンク全検査で再現。全正形状合成後にケース/ギヤ側/タブ/通線負形状を再減算した。ホーン溝床3.6mmと上板を維持し、印刷強度は別感度/実物試験で判定する。

- [ ] フレーム変換・意図した接着部・不可能な実体重なりを区別する
- [ ] 肩/前腕は軸間長と手位置を保つ内部逃げを作り、全可動域・殻・ねじ座・残肉を検査
- [ ] 脚の交差が到達可能な制御姿勢か確認し、実害のある干渉を修正する
- [ ] 自己衝突を有効にした物理計算で再確認
- [ ] 既印刷品の加工/再印刷を部品単位で提示

## 2026-09-05 監査結果の公開参照

[監査コミット 0de7154](https://github.com/hapx2yuki/Tachikoma/commit/0de71547c1af59abc6bcc3484b03b12e9430df2b)。腕の内部逃げと肘殻配置、coxa後端と埋戻されたサーボポケットの再切削は実装済み。腕1664姿勢と固定4項目の交差0、coxa約199万角度組合せの連続隙間下界0.963mm、ホーン溝床3.6mmを検査した。

[機構の修正と残件](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/mechanical.md) / [coxaポケットの独立評価](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/coxa-pocket-peer.md) / [変更した印刷部品](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/manufacturing.md)。

肩pitchと前脚pitchのケース交差は未解決で、{{RV-09}}の軸配置と連携が必要。固定手 {{RV-17}}、脚ガード {{RV-16}}、脚カバー {{RV-14}}、印刷強度 {{RV-07}}も別途残る。局所修正を全体の自己干渉解消に拡張せずOPENを維持する。
""")
issue("RV-11", "RV-11 [電装] 部品の実型番・電源収支・停止回路・測定手順を整合させる",
      parent="E3", labels=["type/不具合", "area/電装", "area/ドキュメント", "prio/P0", "並行作業OK"], body="""
担当: 電装統合。docs/BOM.md、wiring.md、shopping.md、assembly.md、電源計算を所有。
購入記録はLD20MG/LD220MGとHENGE 8A、設計値はDS3218とHobbywing。互換と仮定しない。

- [ ] 型番ごとの一次資料・寸法・電圧・電流を保存し、現物照合が必要な項目を分ける
- [ ] UBEC V2瞬間15A/旧型20A、PM7a電流機能なし、USB/外部5V排他、LED12個番号を訂正
- [ ] 異電圧レールの入力電力換算とスイッチ/ヒューズ保護協調の測定表を作る
- [ ] CPU/I2C故障でPCAのPWMが残り得る条件を再現し、独立停止/OEと支持台の実機ゲートを明記
- [ ] MAX98357Aの基板別SD回路、I2S形式、1Wスピーカーの出力制限を確認

電源実測はEL-09、サーボ実測はP-03/L-02。実機試験を文書修正の完了に混ぜない。

## 2026-09-05 監査結果の公開参照

[監査コミット 0de7154](https://github.com/hapx2yuki/Tachikoma/commit/0de71547c1af59abc6bcc3484b03b12e9430df2b)。UBEC型番別の定格、PM7aの電流測定不可、USB/外部5V排他、LED12個の番号、PCAロジック3.3V、電力換算と測定手順を訂正した。I2S形式/部分書込とPCA異常検知はソフト修正・故障注入を実施済み。

[配線と基板別条件](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/wiring.md) / [購入品・測定器の訂正](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/shopping.md) / [電源計算の未検証項目](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/final-geometry/verify/power-budget.json) / [制御・音声の検証](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/firmware.md)。

購入個体/電流/温度、独立OE・電源遮断の実効性、手元MAX98357A基板のSD回路と1Wスピーカーの出力制限は未確認。電源計算は終了2=UNVERIFIED。{{P-03}}/{{L-02}}/{{EL-09}}/{{H-04}}の実測へ引き継ぎ、文書訂正だけで本課題をCloseしない。
""")
issue("RV-12", "RV-12 [検証] 表示用モデルと全ファイルの検査記録を実設計に合わせる",
      parent="E9", labels=["type/不具合", "area/CAD", "area/ドキュメント", "prio/P1", "並行作業OK"], body="""
担当: 表示/記録。make_visuals.pyとpreview/render、ファイル別検査台帳を所有。
extract_meshes.pyもXML属性順/単位/同名異形状/原型上書きを検査し、原型保存と誤抽出拒否を修正。
生成時のメモリ形状が正常でもSTL往復でシャーシが非閉体になる不具合を再現。0.001mm簡約で形状差0.004mm³未満に直し、lib.exportとURDF入力で実保存形状の不良を拒否する。さらに旧to_trimeshが密閉空洞の内壁を反転し、784mm³を1216mm³とすることを再現。退化/重複面だけを除き、面向きを保持、元Manifoldと保存STLの体積を照合。書込途中失敗も旧版を保護する。生成成功の終了コードだけで旧/不良メッシュを物理試験へ渡さない。
左右反転後の二重面反転、体高の射影前後の食違い、古いSTLキャッシュ、仮姿勢表示、配線図のSW迂回を修正する。HeadTop/BottomのJSON y12とconfig y11の不一致も修正し、主要外殻と子部品の座標を回帰検査する。固定部検査はbaseに限定せず全link内へ拡張し、検査器自身の空入力/例外/証拠保護も負例で確認する。

- [x] 旧データ/非表示ファイルも一覧化し、読解・構造解析・幾何検査・画像確認を方法別に記録
- [x] 静止姿勢と運動学表示を実firmware契約へ合わせる
- [x] 現行STL/URDF/表示の位置一致を検査し、旧動画を歩行証拠に使わない
- [x] 旧画像/動画を保存したまま改訂版を別パスへ出力し、物理計算と運動学デモを明記
- [x] 数値・出典・未検証項目を横断照合する

## 2026-09-05 監査結果の公開参照

[監査コミット 0de7154](https://github.com/hapx2yuki/Tachikoma/commit/0de71547c1af59abc6bcc3484b03b12e9430df2b)。表示・変換・STL入出力・検査記録の修正と検証は完了。開始時515ファイルを方法別に確認し、現在の個別STL99件の閉形状を検査。空洞の内壁保持・鏡映・退化面・異常入力・保存失敗を含むSTL出力12試験も合格した。

[検査対象と確認方法の全件台帳](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/coverage-complete.json) / [STL出力の独立検証](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/stl-export-peer.md) / [出力・変換・検査器の回帰ログ](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/final-geometry/verify/regression.log) / [固定組立の未解決分類](https://github.com/hapx2yuki/Tachikoma/blob/0de71547c1af59abc6bcc3484b03b12e9430df2b/docs/audits/20260905-round2/static-assembly.md)。

全リンク内152部品・1898組から99組の体積共有を検出して残した。これは検査記録の完了であり、全組立の合格ではない。位置不明のHead_Insert、原Downloads3MF不在、候補部品の未採用、実機未確認も記録した。
""")

issue("RV-13", "RV-13 [機構不具合] 頭の7固定穴から外殻へ届く支持柱・受け座を設計する",
      parent="E7", labels=["type/不具合", "area/CAD", "prio/P0"], blocked_by=["RV-09"], body="""
担当: 頭部固定。シャーシとHeadの接続境界。共通make_chassis.pyはRV-14/RV-15と同時編集しない。
半径78mmの7穴は垂直軸が全てHeadTopを通らない。タブから頭まで前約5.7mm、後約18.5mm離れ、ホットボンドの点付けは成立根拠にならない。
半径/中心49候補も、頭に届く後部柱が脚サーボケースへ最大約794mm³交差した。

- [ ] RV-09の実サーボ/目/カメラ位置確定後、頭の受け座と7本に限らない必要支持数を設計
- [ ] 柱・穴・ネジの荷重経路、締結長、工具/配線/開閉の経路を実メッシュで確認
- [ ] 可視面を保ち、前後の頭合わせ目と受け座の残肉を確認
- [ ] 実印刷の引抜き/層割れ/保守開閉試験を別に残す

根拠: docs/audits/20260905-round2/head-attachment-candidates/。
""")
issue("RV-14", "RV-14 [機構不具合] シャーシと大腿カバーの実交差を解消する",
      parent="E4", labels=["type/不具合", "area/CAD", "prio/P0"], blocked_by=["RV-09"], body="""
担当: 脚/シャーシ境界。軸の位置はRV-09で先に確定。支持タブ改訂RV-13と共通生成ファイルを直列で変更する。
停止保持でchassis–thigh_cap前約116/後411mm³、可視外面にも交差がある。
前脚はシャーシ本体r72の内側まで入るので、取付耳だけの除去では直らない。
coxa–cap約11〜14mm³、大腿装飾約51〜127mm³も別に検査する。

- [ ] 実C++で出せる停止/全歩容/遷移の姿勢範囲を使う
- [ ] シャーシ/軸/取付耳の候補を比較し、見えるカバーを削る案は寸法付きで先に提示
- [ ] 他軸・頭支持・電池・ねじ・配線の残肉/干渉を同時確認
- [ ] STL/URDF/接触診断へ反映し、旧印刷品の再使用と変更対象を明示
""")
issue("RV-15", "RV-15 [機構不具合] 首・Cabin・装飾を全長で組み合わせ、支持と挿入を成立させる",
      parent="E7", labels=["type/不具合", "area/CAD", "prio/P0", "並行作業OK"], body="""
担当: 首とCabinの組立。H-06電装室、RV-07印刷強度と並行測定し、共通の接合面/通線径を合意してから生成する。
固定58部品1653ペアを全検査すると、neck–TailJointBlue3282/Ball2048mm³、Blue–Ball3016mm³、CabinFront–Eye7785mm³が重なる。
丸ポスト先端だけの検査、同じ固定リンクを接触しない扱いにする物理モデルでは検出できない。

- [ ] 全交差を部品名・体積・実座標で分類。旧配置推定を元3MF再確認と取り違えない
- [ ] 目の接着縁と座面、TailJoint全長の中実/空洞、Cabinと首フランジを合わせる
- [ ] 最小の内側加工/軸ボア/支持変更を候補STLで比較し、可視変更が要る案は明示
- [ ] 通線φ8候補・最小肉厚・4壁40%の強度・工具/挿入/取り外しを一緒に検証
- [ ] H-06の棚/基板/配線と干渉しない最終形状を決め、既印刷品への加工可否を示す

根拠: tools/check_static_assembly.py、docs/audits/20260905-round2/final/static-all-pairs.json。
現在の11首候補は成立証明ではない。Topだけを避ける補強案を全体合格にしない。
""")

# ---------------------------------------------------------------------------
# 整合性チェック (import 時に実行)
# ---------------------------------------------------------------------------
issue("RV-16", "RV-16 [機構不具合] 大腿・脛ガードの受け座と実際に入る組立経路を作る",
      parent="E7", labels=["type/不具合", "area/CAD", "prio/P1", "並行作業OK"], body="""
担当: 脚装飾の受け座。shell_mod.pyとkit脚配置を所有。RV-14のシャーシ/軸の変更とは座標境界を先に共有する。
配置最適化がガード表面を固体内へ約1mm埋めることを良好とし、支持殻には負形状がない。
大腿81.127%、脛77.940%のガード体積が殻と重なる。0.10/0.15mmの受け座を切っても26方向すべてで初動1mm以内に干渉。開放掃引1案は大腿外面約725mm²を除去し、脛は283mm³の見える片へ分離したため不採用。

- [ ] 見える元ガードを維持し、殻側の分割/着脱/挿入座を寸法付き候補で比較
- [ ] 殻だけでなくfemur/tibia骨格との交差、M3座、工具経路、接着支持面を検査
- [ ] 閉体判定と挿入全経路を分け、入口のないくり抜きを完成扱いにしない
- [ ] 可視面変更が必要な案は面積と比較図を提示し、採用後に旧印刷品への加工可否を整理

根拠: docs/audits/20260905-round2/guard-seat-candidates/。現段階は候補比較までで未解決。
""")
issue("RV-17", "RV-17 [機構不具合] 固定爪・指・指飾りの嵌合公差と接着座を検証する",
      parent="E5", labels=["type/不具合", "area/CAD", "prio/P1", "並行作業OK"], body="""
担当: 固定手の部品接合。claw_mountとCLAW/FINGER/FINGERTIP変換を対象にする。腕軸と長さはRV-10と共有し、共通make_arm.pyの編集を直列にする。
同一リンク内の実体検査で片腕あたりmount–Claw4.628mm³、Claw–指飾り15.879mm³、Finger–指飾り17.071mm³の重なりを検出。旧検査は手内部を調べずmountとの重なり50mm³未満を合格にしていた。元Downloads3MFは現在不在のため、過去の推定行列を一次資料の再検証と混同しない。

- [ ] 重なりの深さ/接着面/差込部/抜け方向を実メッシュで測り、意図した圧入か組立不能かを判定
- [ ] 実印刷の穴径・ペグ径と公差を測る。樹脂の重複体積を根拠なく許容しない
- [ ] 必要な隠し座/配置修正を比較し、指の見える原形と到達長を保持
- [ ] 手内部の全組合せと挿入経路を検査し、左右鏡像と旧印刷品への影響を残す

根拠: docs/audits/20260905-round2/final-geometry/verify/static-all-pairs.json と tools/check_arm.py。
""")

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
