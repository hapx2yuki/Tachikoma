# 2026-09-09 再開監査・公開準備（WIP）

確認日: **2026-09-09（JST）**。対象は、現行 `print-first` 候補、履歴3MFの保護経路、通常物理の限界、頭候補の薄肉分解、公開準備です。GitHubへの書込み、`git add`、`git commit`、`git push`、共有原本の上書きは行っていません。現物の印刷、組立、通電、歩行も確認していません。候補生成物は `hardware/urdf-print-first/` の専用経路で扱い、最終採用前に再生成後のSHAを確認します。

現時点の判定は **WIP / REVIEW_REQUIRED / 量産・実機未確定** です。今回の頭部確認では、既存の `pf_head_top_clearanced` を監査用に参照しただけで、新しい逃げ形状は作っていません。旧 `pod_neck` 用の重複候補も追加していません。

## 最新の生成結果

2026-09-08T18:28:51Z〜18:32:24Z（`run_id=d93022334ee143b68ee0d06646e8f40e`）の `tools/generate_print_first.py` と `--validate-only` は、いずれも終了コード0でした。生成結果は **86パーツ・23リンク、visual STL 56枚、collision STL 24枚、URDF参照メッシュ80枚**です。Cabin保管後のruntime台帳は29源部品、合計587.3919603718443 g、追加保管568.7360089849115 g（既保管18.655951386932827 gを二重計上せず）です。生成前後の対象ソース236件はSHA差分0、履歴3MF26件は全件保持・SHA一致でした。これは形状・台帳の生成確認であり、実印刷、実組立、通電、歩行、材料強度の合格ではありません。

生成済み候補URDFのruntime bindingは、Cabin保管後の29源部品・追加保管568.7360089849115 gを含めて確定しました。形状変更の根拠へ自動昇格させず、候補URDF・manifest・参照STLのSHAを公開候補へ記録します。最新の数値境界と公開向けリンクは [公開根拠要約](public-evidence-summary.md) に集約しています。

同じ候補を部品別に分けた診断は144件で、51 PASS、87 FAIL、6 TIMEOUTでした。FAILの主因はVHACDの表面被覆・検証変換であり、87件を機体欠陥87件とは読みません。現行衝突メッシュの `linked-hulls` 後退近似は、stand/T0が `INVALID_INITIAL_CONTACT_MODEL`（4.4秒積分、最大初期自己貫入28.1084 mm、roll 24.9095°、pitch 11.5022°）、別の26秒歩行・自己衝突なし診断が `FAIL` でした。これは近似モデルの切り分け結果であり、機構欠陥や歩行合格の実証ではありません。部品別診断の残件は、現行候補へ再生成後の入力SHAをそろえてから確認します。

## 3MFの復元と保護

`Downloads/TACHIKOMA.3mf` は、旧再現記録の実行時に不在でした。過去のJSONや抽出済みメッシュを、元ファイルの再確認や復元済みの証拠とは扱いません。開始時台帳は、リポジトリ内の元モデル58個と印刷プロジェクト27個を記録し、変更前の保護台帳は124ファイルの不変を記録しています。STL差分は12ファイル（形状10、配置のみ2）で、変更STLは閉形状でした。

今回の保護確認では、`hardware/stl` の履歴3MF 26件について、quarantine経路が現位置を保持すること、欠落時に移動前に拒否することを実際の入口で確認しました。履歴3MF保持・欠落拒否2件と生成器契約49件がPASSです。これは `restore_from_backup` / quarantine の保護契約が通ったという意味で、欠在するDownloads原本の復元、実印刷、実機適合を意味しません。元の変更前バックアップはリポジトリ外のため、公開ファイルにはパスを載せません。

ローカル保存根拠（公開束外）: [3MF保護記録](../../../outputs/audits/20260909-resume/artifact-preservation-20260908T171154Z-3mf/README.md)、[検証JSON](../../../outputs/audits/20260909-resume/artifact-preservation-20260908T171154Z-3mf/verification.json)。公開版は [旧3MF/生成の影響](../20260905-round2/manufacturing.md) と [旧再現の入力境界](../20260905-round2/public-reproduction/baseline-23-summary.md) を参照します。

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

一方、`linked-hulls` 後退近似はstand/T0が `INVALID_INITIAL_CONTACT_MODEL`（初期自己貫入最大28.1084 mm、roll 24.9095°、pitch 11.5022°）、別の26秒歩行・自己衝突なし診断が `FAIL` でした。後退近似の有限積分結果を厳密VHACD契約や歩行合格へ読み替えません。最終freeze2はWIP公開の前提ではなく、同一SHA台帳による全物理ゲートを後続で実施します。

## 旧来検査の集計

現行候補とは別に、`legacy-verify-20260908T184106Z/verify-results.json` の旧来検査23件は **PASS 14件 / FAIL 8件 / UNVERIFIED 1件**でした。FAILは旧static、arm、leg、head-pod、URDF、print-artifacts、toe、strengthの各検査で、powerはUNVERIFIEDです。この旧来集計は、body全1141姿勢のprint-first `DYNAMIC_PASS`とは別の後退境界であり、実機適合・印刷強度・電源能力を確定しません。

CoACDの一部品調査は [公開要約JSON](mouth-cannon-coacd-public-summary.json) にコピーしています。`DIAGNOSTIC_ONLY` かつ `formal_gate_changed=false` であり、全体の衝突近似へ採用していません。

## 3組のソース候補と再現閉包

公開候補として精選した3組（履歴3MF保護、battery VHACD、native trace入口）は、各ソースと回帰試験だけを並べた6件の対です。現行 `tools/print_first_source_closure.py` が定める38件のソースと15件の追加凍結入力に対し、3組の直接列挙だけでは **50件不足**し、そのうち **18件が現行作業ツリーで未追跡**です。この50件は6件の直接列挙との差分を示す台帳値であり、合成treeから消えた依存数ではありません。不足パスと追跡状態、各組の直接依存は [publication-allowlist.json](publication-allowlist.json) の `source_closure_requirements` / `missing_from_three_pairs` に列挙しました。

今回の候補リストでは、既存の公開allowlist候補のソース群に加え、6件をASTでたどったローカルimport閉包 **31件**、生成器が直列に呼ぶ9段階、電池特則試験が読む `hardware/stl/battery_cradle.stl` を個別選択しました。生成器の静的入力である `tools/data/kit_assembly_front.json` / `kit_assembly_rear.json`、`model/`、全`hardware/stl`は個別の公開選択から外していますが、いずれもHEAD追跡物として仮合成treeで利用可能であり、今回の依存欠落ではありません。既存metadataにある絶対home表記は既公開履歴由来として今回の新規停止理由にせず、私的な調達記録だけは引き続き候補から除外しています。出力・衝突cacheは公開束へ含めないため、`outputs/print-first-20260905/final-simulation/cases/motion-quality-cases.json`だけがcanonical 53件の意図的な合成tree欠落です。

一時 `GIT_INDEX_FILE`でHEADと選択中の候補を合成して確認しました（treeハッシュは候補JSONの`virtual_tree_check.tree_hash`に記録）。今回の公開選択では、generator契約49件が **49/49 PASS** になるよう、動的入力台帳が要求する足部候補STLとXIAO保持候補STL 12件を個別に選びます。`outputs/`全体やcacheを公開するのではなく、generatorが直接要求する13件だけを手渡し対象へ加えます。canonical 53件のうち、motion-qualityの生成入力1件は引き続き意図的な公開外です。したがって、ソース閉包の差分50件（未追跡18件）とcanonical出力1件は明示したまま、選択候補の再現検査を進めます。
## Issue 107件のWIP計画

読み取り専用の最新保存値（2026-09-08T18:00Z）は、Issue **#3〜#109の107件**（OPEN102 / CLOSED5）、Project #2 **96項目**、未掲載 **#99〜#109の11件**、ライブ正式依存 **156本**でした。ローカル計画は174本で、追加予定18本がライブ未反映です。今回の追記案は [github-issues-wip-comment-plan.md](github-issues-wip-comment-plan.md) と [JSON](github-issues-wip-comment-plan.json) に保存し、107行それぞれへ既存planの個別受入条件・次作業・ライブ/計画依存を残しています。投稿、Project更新、依存更新はしていません。

投稿する場合の順序は、rootレビュー、公開用READMEの存在確認、WIPであることを明記したIssueへのappend-only追記、Projectの11件追加、全107件と依存の読み戻しです。最終freeze2はWIP公開の前提にしません。外部コメントに、実機完成・実印刷完了・物理完走を記載しません。

## 公開対象の明示リスト

`[publication-allowlist.json](publication-allowlist.json)` と [公開allowlistの説明](publication-allowlist.md) に、今回のREADME、公開根拠要約、CoACDの公開要約JSON、WIPコメント案、既存公開文書、依存閉包のソース/試験、生成済み現行候補を明示しました。候補は選択パスを個別列挙し、`outputs/`全体・cache・私的な発注情報・絶対homeパス・リポジトリ外バックアップ・元`model/`全体・元`hardware/stl`全体・動画・zip・全未追跡の一括取り込みは対象外です。URDF参照STL80件、body/legs/feetの印刷候補37件とassembly記録3件、generator契約の足部共有1件とXIAO候補12件、LD220治具3件＋記録1件を個別に選びます。電池試験用の `hardware/stl/battery_cradle.stl` も必要な原本として個別選択します。公開対象はWIP候補であり、量産認定や実機完了を意味しません。今回の既存doc修正は、状態と根拠の整合を取るために記録しています。

内容走査では、allowlistに実際に選んだテキストとSTLについて、絶対homeパス、秘密らしい値、発注番号・郵便番号・メールアドレスを検出しません。公開根拠要約の相対リンクは、選択文書またはHEAD追跡の公開ファイルを指します。raw監査出力は作業ツリーに保管しますが、公開束へ再帰的に含めません。

## 確認したソースのSHA-256

確認時点のソースSHAは、別担当の更新中に変わり得ます。実行証跡のSHAと現在のSHAが異なる場合は、最終freeze前に再実行します。

| 対象 | 確認時点のSHA-256 | 状態 |
|---|---|---|
| `tools/generate_print_first.py` | `1404d9f4849bc806aea89348c7b498f5b6b4c2ba993334072a18ff31eadfd037` | 履歴3MF保護の更新中 |
| `tools/tests/test_generate_print_first_contracts.py` | `42591c2f305c06efc0879da34d7e4f00155c0d2dc9a0734d64c7a60ec4d9822e` | 保持2件＋契約49件の証跡あり |
| `tools/sim_collision.py` | `8507da13560ed1ea10bc4acd6b2b7c5aa40f5d49c610b7d3842d7eec0bc36b90` | 現在SHA。17:18実行時`ef48a146...`と相違、担当側更新中で再実行要 |
| `tools/make_head_eyecut.py` | `071bfba70b6f287d5b45be4d8753e00b62b6422a6ac8b3985b63cdf6b3db41d8` | 現行頭候補生成器、今回ソース編集なし |
| `hardware/src/make_print_first_body.py` | `87e283b6eb7efbce3ca6b82c644ef6649ff95f6eeee72661fac116f71c467408` | 現在SHA。担当側更新中、再生成後に再確認 |
| `tools/print_first_assembly.py` | `6d1d6254a042d04fff7b0b2c96512d6810f0b1241e8cb7fe1e3e7dad728c6b58` | `A.context()`収集境界を確認、担当側更新中 |
| `tools/print_first_source_closure.py` | `ec525cdc0bfb7a6af9a10f1f64d6589a5d2abe733cf5ec93b6542466d1ae33b3` | 38件のソース＋15件の追加凍結入力を列挙する現行閉包（未追跡） |
| `tools/tests/test_battery_cradle_feature.py` | `4012b3829b714323e951f07802887f21390fb0b90faf2c5c0b9bb08625a700ea` | 現在SHA。17:18実行後に更新中、再回帰要 |
