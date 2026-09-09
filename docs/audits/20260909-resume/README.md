# 2026-09-09 再開監査・公開準備（WIP）

確認日: **2026-09-09（JST）**。対象は、現行 `print-first` 候補、履歴3MFの保護経路、通常物理の限界、頭候補の薄肉分解、公開準備です。公開候補は [PR #111](https://github.com/hapx2yuki/Tachikoma/pull/111) の証拠 [commit `14c3728`](https://github.com/hapx2yuki/Tachikoma/commit/14c372880507462f7eb181a1b75a0ad7b217ccb5) と追補 [commit `a8156fb`](https://github.com/hapx2yuki/Tachikoma/commit/a8156fbfc7539f35f0fbaad25808c44f98247540) の到達履歴として記録・push済みです。外部反映の読み戻しは [github-publication-result.json](github-publication-result.json)（確認時刻: **2026-09-08T19:12:30Z / 2026-09-09 04:12:30 JST**）に保存し、Issue #3〜#109の107件追記、既存コメント16件保持、Project #2の96→107（#99〜#109の11件追加）、依存156→174（18件追加）、親87件保持・循環0を確認しました。現物の印刷、組立、通電、歩行も確認していません。候補生成・検証の入力SHAと成果物はcommit 14c3728と追補commit a8156fbの記録に対応しています。

現時点の判定は **WIP / REVIEW_REQUIRED / 量産・実機未確定** です。今回の頭部確認では、既存の `pf_head_top_clearanced` を監査用に参照しただけで、新しい逃げ形状は作っていません。旧 `pod_neck` 用の重複候補も追加していません。

## 最新の生成結果

2026-09-08T18:28:51Z〜18:32:24Z（`run_id=d93022334ee143b68ee0d06646e8f40e`）の `tools/generate_print_first.py` と `--validate-only` は、いずれも終了コード0でした。生成結果は **86パーツ・23リンク、visual STL 56枚、collision STL 24枚、URDF参照メッシュ80枚**です。Cabin保管後のruntime台帳は29源部品、合計587.3919603718443 g、追加保管568.7360089849115 g（既保管18.655951386932827 gを二重計上せず）です。生成前後の対象ソース236件はSHA差分0、履歴3MF26件は全件保持・SHA一致でした。これは形状・台帳の生成確認であり、実印刷、実組立、通電、歩行、材料強度の合格ではありません。

生成済み候補URDFのruntime bindingは、Cabin保管後の29源部品・追加保管568.7360089849115 gを含めて確定しました。形状変更の根拠へ自動昇格させず、候補URDF・manifest・参照STLのSHAを公開候補へ記録済みです。最新の数値境界と公開向けリンクは [公開根拠要約](public-evidence-summary.md) に集約しています。

同じ候補を部品別に分けた診断は144件で、51 PASS、87 FAIL、6 TIMEOUTでした。FAILの主因はVHACDの表面被覆・検証変換であり、87件を機体欠陥87件とは読みません。現行衝突メッシュの `linked-hulls` 粗い凸包近似は、stand/T0が `INVALID_INITIAL_CONTACT_MODEL`（4.4秒積分、最大初期自己貫入28.1084 mm、roll 24.9095°、pitch 11.5022°）、別の26秒歩行・自己衝突なし診断が `FAIL` でした。これは近似モデルの切り分け結果であり、機構欠陥や歩行合格の実証ではありません。部品別診断のPASS/FAIL/TIMEOUTは、この候補で保存した診断値として扱い、厳密な全構成物理の合格へ広げません。

## 3MFの復元と保護

`Downloads/TACHIKOMA.3mf` は、旧再現記録の実行時に不在でした。過去のJSONや抽出済みメッシュを、元ファイルの再確認や復元済みの証拠とは扱いません。開始時台帳は、リポジトリ内の元モデル58個と印刷プロジェクト27個を記録し、変更前の保護台帳は124ファイルの不変を記録しています。STL差分は12ファイル（形状10、配置のみ2）で、変更STLは閉形状でした。

今回の保護確認では、`hardware/stl` の履歴3MF 26件について、quarantine経路が現位置を保持すること、欠落時に移動前に拒否することを実際の入口で確認しました。履歴3MF保持・欠落拒否2件と生成器契約49件がPASSです。これは `restore_from_backup` / quarantine の保護契約が通ったという意味で、欠在するDownloads原本の復元、実印刷、実機適合を意味しません。元の変更前バックアップはリポジトリ外のため、公開ファイルにはパスを載せません。

ローカル保存根拠（公開束外）: [3MF保護記録](../../../outputs/audits/20260909-resume/artifact-preservation-20260908T171154Z-3mf/README.md)、[検証JSON](../../../outputs/audits/20260909-resume/artifact-preservation-20260908T171154Z-3mf/verification.json)。公開版は [公開根拠要約](public-evidence-summary.md) に集約しています。

## 通常物理PASSの限界

標準 `hardware/urdf/tachikoma.urdf` を `PRINT_FIRST_ACTIVE=False`、`linked-hulls`、通常の13秒条件で実行した記録は、`simulation_acceptance=PASS`、終了コード0でした。有限状態、転倒なし、IK fallbackなし、脚以外の床接触なし、物理警告なしを確認しています。`physical_readiness=UNVERIFIED` です。

このPASSは、標準URDFの指定条件に対する数値診断です。自己衝突は無効、材料別接触は分離せず、print-firstの採用STL・サーボ外形・親子自己衝突・4脚の支持・実購入個体を評価していません。したがって、機体完成・実機歩行・print-first物理成立へ読み替えません。ローカル保存根拠（公開束外）は [通常物理README](../../../outputs/audits/20260909-resume/physics-20260908T163200Z/README.md) と [metrics.json](../../../outputs/audits/20260909-resume/physics-20260908T163200Z/metrics.json) です。公開版の境界は [公開根拠要約](public-evidence-summary.md) にまとめています。

## 電装の最新回復確認

2026-09-08の再確認では、`.venv/bin/pio run -e esp32dev -e esp32dev_print_first -e esp32dev_print_first_cal` が3環境すべて `SUCCESS`、続く `.venv/bin/python -m unittest ...` が57件すべて `OK` でした。`post-verification.json` で、実機書込みなし、入力変更なし、実機未接続・未通電を確認しています。これはホスト回帰とビルドの根拠であり、実機起動・電源能力・歩行・print-first物理完走を示しません。ローカル保存根拠（公開束外）は [回帰の再確認](../../../outputs/audits/20260909-resume/firmware-regression-20260908T171626Z/post-verification.json)、[3環境ビルド](../../../outputs/audits/20260909-resume/firmware-regression-20260908T171626Z/build.log)、[57件回帰](../../../outputs/audits/20260909-resume/firmware-regression-20260908T171626Z/regression.log) です。

## print-firstのbatteryからCabinへの停止

最初のprint-first候補実行では、`base_link/battery_cradle` のVHACD被覆が 21,103/23,026、`0.9164857118040476`、許容値0.011 mmで失敗し、`mj_step`前に停止しました。2026-09-08T17:18Zのbattery特則は1832凸セル、23,026/23,026、被覆率1.0、5帯すべての双方向Boolean対称差0.0 mm²、体積差`-4.9749360187e-09 mm³`でPASSしました。この局所特則はbatteryの形状境界だけを確認し、形状外距離や全体物理を確認していません。

最新のCabin保管後 formal stand/T0（2026-09-08T17:59Z）は事前検査296件がPASSし、入力ソース304件の前後差分もありませんでした。battery経路の後、`base_link/Mouth_Cannon_Grey` の被覆が `16007/19618 = 0.8159343460087675`、許容値0.011 mmで失敗し、`mj_step`前に停止しました。したがって静止姿勢・歩行・実機の物理結果はありません。`FINAL_FROZEN`は入力SHA固定だけを示し、物理合格や採用決定ではありません。17:18ZのCabin停止記録は旧履歴として保持し、現在の採用構成の正式結果には使いません。Cabinは殻・装飾・peg・turretを保管し、`pf_cabin_rail_l/r` の一体電装支柱・棚・battery・chassisを維持する方針です。根拠の要約は [公開根拠要約](public-evidence-summary.md)、詳細なformal記録はローカル監査台帳に保存しています。

## 頭候補の薄肉分解と実壁

旧入口 `.venv/bin/python tools/check_head_pod_clearance.py` は `Head_Top_Eyecut` と `pod_neck` の `89.23001 mm3` 交差を再現してFAILしました。`pf_head_top_clearanced` を同じ旧座標へ戻しても `89.229924705 mm3` で、旧 `pod_neck` 用の逃げがあるとは言えません。現行 `A.context(generated=True)` は `pod_neck` と旧頭殻を収集から外し、`pf_head_top_clearanced` と `pf_chassis` の実メッシュ交差は `0.0 mm3` でした。頭殻・シャーシ・候補は単一連結・watertight、目穴は3/3開口です。

固定乱数24,000面のうち、判定領域10,879点に対する1.25 mm内側プローブは候補 `10176/10879 = 93.538%` でした。失敗703点は次のように分かれます。

| 区分 | 点数 | 扱い |
|---|---:|---|
| 開口・切断端（原本の0.25 mm判定でも外側） | 59 | 薄肉欠陥へ数えない |
| 原本側の浅部・端部 | 61 | 直接断面がないため欠陥とは断定しない |
| 現行print-firstの占有逃げ | 583 | 目・腕・脚・カメラの設計逃げ |
| 未分類 | 0 | — |

開口・逃げを除き、q=1.25 mmから内向きへ法線整合レイを通した実壁は、候補6310本で最小 **1.923876 mm**、5%点1.935294 mm、中央値2.511614 mmでした。最小点の外側座標は `(11.616166, 68.282130, 23.736800) mm`、内側ヒットは `(10.625942, 67.102182, 24.889397) mm` です。支持座は `pf_chassis` の丸角8×12 mm断面で、最小実断面は `(x,y)=(56,35) mm`、`z=13.5 mm` の **50.5659 mm²** でした。座隙0.25 mmは `config.PRINT_FIRST.head_seat_gap` による支持座嵌合値で、旧 `head/pod_neck` の0.5 mm目標を緩めた値ではありません。

この結果は寸法確認用試作候補の根拠です。実サーボ・カメラ適合、PLA層間強度・たわみ、荷重・引抜き、上下殻接合、実印刷面は未確認で、量産可とは判定しません。詳細な数値はローカル保存根拠（公開束外）の [頭部実メッシュ再検査](../../../outputs/audits/20260909-resume/head-20260908T164318Z-pfcurrent/README.md)、[薄肉分解](../../../outputs/audits/20260909-resume/head-20260908T164318Z-pfcurrent/thin-wall-breakdown.md)、[座標表](../../../outputs/audits/20260909-resume/head-20260908T164318Z-pfcurrent/support-sections.csv) に保存し、公開版の要点は [公開根拠要約](public-evidence-summary.md) に転載しています。

## 個数と追加購入の境界

2026-09-06確認の台帳を再照合しました。現行の設計必要数は、body16＋legs20＋TPU靴4＋PLAスペーサー16で **56個**です。初回のAは **31個**（body16＋固有脚10＋TPU靴1＋スペーサー4）、A合格後のBは **25個**、Cは既存脛殻4個の局所加工・再使用が不成立のときだけ選ぶ **条件付き4個**です。CはA+Bへ加算しません。LD-220適合治具3種は各1個の非本番治具で、56個へ加えません。

追加購入台帳の `immediate_purchase_required=0`、対象IDなしは、現在の着手方針として追加購入を確定していないことを示します。`physical_stock_verified=false`、手持ちPLA/TPUの残量・良品数未確認、`current_shortage=null` は、不足ゼロや現物充足を確定しません。`currently_printable_quantity=0` はfreeze2・現物適合・材料・実C++物理試験のゲート未通過を示します。旧全体キット台帳139個、治具、予備、候補形状を本番56個へ混ぜません。

根拠は [print-firstマニフェスト](../../print-first-manifest.json)、[追加印刷台帳](../../additional-printing.json)、[追加購入台帳](../../additional-purchases.json)、[print-first現行説明](../../print-first.md) です。

## full trace / full dynamics

現行候補のbody構成では、[最新body実メッシュ記録](../../../outputs/audits/20260909-resume/native-trace-final-component-20260908T183435Z/final-body-mesh.json)がnative trace 20軸・12区間・全1141行を読み、`pose_rows_read=1141`、`pose_rows_checked=1141`、`fixed_geometry=true`、`dynamic_geometry=true`、交差・エラーなしで `status=DYNAMIC_PASS` となりました。これはbody構成の1ケースについて全1141姿勢を同じ実メッシュへ束縛した結果で、全構成の材料別VHACD・購入サーボ外形・電圧群適合・実機を確認したものではありません。`physical_readiness=UNVERIFIED` です。

一方、`linked-hulls` 粗い凸包近似はstand/T0が `INVALID_INITIAL_CONTACT_MODEL`（初期自己貫入最大28.1084 mm、roll 24.9095°、pitch 11.5022°）、別の26秒歩行・自己衝突なし診断が `FAIL` でした。粗い凸包近似の有限積分結果を厳密VHACD契約や歩行合格へ読み替えません。最終freeze2はWIP公開の前提ではなく、同一SHA台帳による全物理ゲートを後続で実施します。

## 旧来検査の集計

現行候補とは別に、`legacy-verify-20260908T184106Z/verify-results.json` の旧来検査23件は **PASS 14件 / FAIL 8件 / UNVERIFIED 1件**でした。FAILは旧static、arm、leg、head-pod、URDF、print-artifacts、toe、strengthの各検査で、powerはUNVERIFIEDです。この旧来集計は、body全1141姿勢のprint-first `DYNAMIC_PASS`とは別の回帰検査結果であり、実機適合・印刷強度・電源能力を確定しません。

CoACDの一部品調査は [公開要約JSON](mouth-cannon-coacd-public-summary.json) にコピーしています。`DIAGNOSTIC_ONLY` かつ `formal_gate_changed=false` であり、全体の衝突近似へ採用していません。

## 3組のソース候補と再現閉包

公開候補として精選した3組（履歴3MF保護、battery VHACD、native trace入口）は、各ソースと回帰試験だけを並べた6件の対です。現行 `tools/print_first_source_closure.py` が定める38件のソースと15件の追加凍結入力に対する、3組の直接列挙との差分は **50件**です。allowlist生成時点で未追跡と記録した18件のうち、commit 14c3728のHEADでは17件が追跡済みで、残る1件はcanonical `motion-quality`入力として意図的に公開外です。この差分は6件の直接列挙との差分を示す台帳値であり、合成treeから消えた依存数ではありません。不足パスと追跡状態、各組の直接依存は [publication-allowlist.json](publication-allowlist.json) の `source_closure_requirements` / `missing_from_three_pairs` に列挙しました。

今回の候補リストでは、既存の公開allowlist候補のソース群に加え、6件をASTでたどったローカルimport閉包 **31件**、生成器が直列に呼ぶ9段階、電池特則試験が読む `hardware/stl/battery_cradle.stl` を個別選択しました。生成器の静的入力である `tools/data/kit_assembly_front.json` / `kit_assembly_rear.json`、`model/`、全`hardware/stl`は個別の公開選択から外していますが、いずれもHEAD追跡物として仮合成treeで利用可能であり、今回の依存欠落ではありません。既存metadataにある絶対home表記は既公開履歴由来として今回の新規停止理由にせず、私的な調達記録だけは引き続き候補から除外しています。出力・衝突cacheは公開束へ含めないため、`outputs/print-first-20260905/final-simulation/cases/motion-quality-cases.json`だけがcanonical 53件の意図的な合成tree欠落です。

一時 `GIT_INDEX_FILE`でHEADと選択中の候補を合成して確認しました（treeハッシュは候補JSONの`virtual_tree_check.tree_hash`に記録）。選択候補ではgenerator契約49件を **49/49 PASS**、battery回帰4件とnative回帰3件をPASSとして確認済みです。`outputs/`全体やcacheを公開するのではなく、generatorが直接要求する13件を手渡し対象として選定済みです。canonical 53件のうち、motion-qualityの生成入力1件は意図的に公開外です。ソース閉包の差分50件とcanonical出力1件はこの状態を明示したまま、選択候補の再現検査を確認済みです。

## Issue 107件のWIP計画

外部反映の読み戻しは [github-publication-result.json](github-publication-result.json)（`status=PASS`、確認時刻 `2026-09-08T19:12:30Z`）に記録しています。Issue **#3〜#109の107件**へWIPコメントをappend-onlyで追記し、wip marker 107件を読み戻しました。既存コメント16件は保持され、反映後コメント総数は123件です。Issueの本文・タイトル・状態・ラベルは変更していません。

Project #2は既存96項目を保持したまま#99〜#109の11項目を追加し、107項目になりました。依存は既存156本を保持し、18本を追加して174本、親関係87本を維持し、循環は0件でした。ローカル計画との差分を反映済みで、Issue本文への既存内容の置換や実機完成・実印刷完了・物理完走の記載はありません。最終freeze2はWIP公開の前提にしません。実反映の根拠と読み戻し値は [github-publication-result.json](github-publication-result.json) を正とします。

## 公開対象の明示リスト

[publication-allowlist.json](publication-allowlist.json) と [公開根拠要約](public-evidence-summary.md) に、今回のREADME、公開根拠要約、CoACDの公開要約JSON、WIPコメント案、既存公開文書、依存閉包のソース/試験、生成済み現行候補を明示しました。候補は選択パスを個別列挙し、`outputs/`全体・cache・私的な発注情報・絶対homeパス・リポジトリ外バックアップ・元`model/`全体・元`hardware/stl`全体・動画・zip・全未追跡の一括取り込みは対象外です。URDF参照STL80件、body/legs/feetの印刷候補37件とassembly記録3件、generator契約の足部共有1件とXIAO候補12件、LD220治具3件＋記録1件を個別に選定済みです。電池試験用の `hardware/stl/battery_cradle.stl` も必要な原本として個別選定済みです。公開対象はWIP候補であり、量産認定や実機完了を意味しません。今回の既存doc修正は、状態と根拠の整合を取るために記録しています。

内容走査では、allowlistに実際に選んだテキストとSTLについて、絶対homeパス、秘密らしい値、発注番号・郵便番号・メールアドレスを検出しません。公開根拠要約の相対リンクは、選択文書またはHEAD追跡の公開ファイルを指します。raw監査出力は作業ツリーに保管しますが、公開束へ再帰的に含めません。

## 追加した回帰試験

2026-09-09 04:02 JSTに、追加公開する回帰試験2本を`.venv/bin/python`で実行し、`test_print_first_cabin_storage.py`は3件・3.804秒、`test_print_first_gate_hardening.py`は5件・0.065秒で、いずれも`OK`でした。`test_print_first_cabin_storage.py`は、直接CLIのruntime台帳、generated=FalseのCabin源mesh保持、generated=TrueのCabin保管と`pf_cabin_rail_l/r`・棚・`battery_cradle`・`pf_chassis`の維持、質量の二重計上防止を3件で確認します。`test_print_first_gate_hardening.py`は、完全trace必須化、旧freeze状態の拒否、安全な出力先、入力SHA変更の拒否、診断モードの明示を5件で確認します。両方を追加公開する回帰試験として扱います。

## 確認したソースのSHA-256

表は証拠commit 14c3728と追補commit a8156fb / PR #111の到達履歴に含まれる公開ソースSHAです。各実行の入力SHAは個別記録を正とし、途中の診断を最新ソースの検証へ読み替えません。

| 対象 | 確認時点のSHA-256 | 状態 |
|---|---|---|
| `tools/generate_print_first.py` | `1404d9f4849bc806aea89348c7b498f5b6b4c2ba993334072a18ff31eadfd037` | commit 14c3728で生成・validate-onlyを確認 |
| `tools/tests/test_generate_print_first_contracts.py` | `42591c2f305c06efc0879da34d7e4f00155c0d2dc9a0734d64c7a60ec4d9822e` | 保持2件＋契約49件の証跡あり |
| `tools/sim_collision.py` | `a6a0e300b6aa42fdb9df4f9c2bec1e8d211fd28e4014ed7bf47ecd29b0d061e1` | commit 14c3728の現行入力SHA。生成・検証へ反映済み |
| `tools/make_head_eyecut.py` | `071bfba70b6f287d5b45be4d8753e00b62b6422a6ac8b3985b63cdf6b3db41d8` | 現行頭候補生成器、今回ソース編集なし |
| `hardware/src/make_print_first_body.py` | `87e283b6eb7efbce3ca6b82c644ef6649ff95f6eeee72661fac116f71c467408` | commit 14c3728の生成入力として確認済み |
| `tools/print_first_assembly.py` | `4f69d67b646393e0a25e39f611686d3429d54190a238a30d062ef77493ee33fd` | commit 14c3728で追跡・仮合成tree検査済み |
| `tools/print_first_source_closure.py` | `ec525cdc0bfb7a6af9a10f1f64d6589a5d2abe733cf5ec93b6542466d1ae33b3` | 38件のソース＋15件の追加凍結入力を列挙する現行閉包。commit 14c3728で追跡済み、仮合成tree検査済み |
| `tools/tests/test_battery_cradle_feature.py` | `4012b3829b714323e951f07802887f21390fb0b90faf2c5c0b9bb08625a700ea` | commit 14c3728でbattery回帰証跡を確認済み |
| `tools/tests/test_print_first_cabin_storage.py` | `51c000ba3483a3d073be38766cfe4772793b8968a1f05d927071a7d4855be951` | 2026-09-09 04:02 JSTに3件OK。追加公開する回帰試験 |
| `tools/tests/test_print_first_gate_hardening.py` | `0a72aca072777a03b4c5434938709a428a9867449d767f33b3c4c78a2ed5c637` | 2026-09-09 04:02 JSTに5件OK。追加公開する回帰試験 |
