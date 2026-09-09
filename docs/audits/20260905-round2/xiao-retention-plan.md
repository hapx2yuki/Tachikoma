# XIAO・カメラ・FPCの再利用保持案

確認日: 2026-09-06。これは、既存の XIAO ESP32-S3 Sense 一式とカメラ/FPCを買い替えずに使うための独立した候補である。`config.py`、既存 body、assembly、量産 manifest は変更していない。保存済みの測定要約と候補配置から作った包絡・座標・保持台の案であり、現物の型番・改版・FPC長を確定する資料ではない。

機械側は最終形状を凍結した後、[xiao-retention-plan.json](xiao-retention-plan.json) の `build_holder_spec` に最終メッシュ由来の寸法とフレームを渡す。生成した候補STLは量産数へ入れず、最初はトレー1個と基板・カメラ1式だけを適合確認に使う。

## 座標の扱い

保存済みの OV3660 候補には `camera_frame_chassis` と、XIAO候補の `board_frame_flat_to_chassis` が別々にある。古い基板中心 `(0, 45, 23) mm` を現在の頭部へ直接足し込まず、次の順で変換する。

```text
board_to_camera = inverse(camera_frame_chassis) @ board_frame_flat_to_chassis
board_to_base = current_base_camera_frame @ board_to_camera
```

FPCの始点とコネクタ中心も、基板と同じ `inverse(camera_frame_chassis)` を一度だけ適用して camera ローカルへ移す。その後、最終実行時の `A.context(最終設定).camera_mount_frame` を `base_camera_frame` として適用する。したがって、head を 33 mm 上げた後に旧 chassis 絶対座標をそのまま再利用しない。計画JSONに保存した `export_urdf.camera_mount_frame(通常設定)` の値は A.context 外で取得した通常設定の参照値であり、print-first最終フレームではない。頭部・yaw・脚の凍結後に、最終コンテキストから渡したフレームで再生成する。

保存済み候補のカメラフレームは次の通りである。

```text
camera_frame_chassis =
[[-1.000000, 0.000000, 0.000000, 0.000000],
 [ 0.000000, -0.726436, 0.687234, 49.594400],
 [ 0.000000, 0.687234, 0.726436, 29.652800],
 [ 0.000000, 0.000000, 0.000000, 1.000000]]

board_frame_flat_to_chassis =
[[ 0.000000, 1.000000, 0.000000, 0.000000],
 [-1.000000, 0.000000, 0.000000, 45.000000],
 [ 0.000000, 0.000000, 1.000000, 23.000000],
 [ 0.000000, 0.000000, 0.000000, 1.000000]]
```

この候補から得た基板の camera ローカル相対変換は、計画JSONの各 `holder_spec.relative_frames.board_to_camera` に保存している。現行 base への戻し値、FPC始点、コネクタ中心も同じ記録に保存し、旧座標との混同を避ける。

印刷優先構成の保持台は `hardware/src/config.py` の `holder_z_offset_mm = +4.0 mm` を camera ローカル +Z に適用する。適用対象は基板占有、床、左右リブ、holder3一体化候補だけで、既存カメラ子基板・レンズ占有は camera ローカル位置に固定する。FPCはカメラ端を固定し、基板コネクタ端だけを同じ +Z4 mm 移動後の位置として扱う。

## 保持する占有

XIAO主基板、Sense拡張基板、USB、shield、ヘッダ、はんだの逃げを一つの `xiao_all_boards` 占有として返す。保存済み包絡の初期値は次の二候補である。

| 候補 | 平面化包絡 | 扱い |
|---|---:|---|
| MicroSDなし | `22.78198 × 17.78000 × 8.03000 mm` | 比較候補。拡張基板のサービス側は開ける |
| MicroSDあり | `24.36334 × 17.78000 × 8.50000 mm` | 比較候補。MicroSDは後回しにできるが装着時の逃げを残す |

`camera_child_lens` は既存カメラ子基板・レンズ側を一つの候補包絡として返す。初期値は `8.0 × 9.56709 × 9.10175 mm`、中心 `(0, -7.32913, 13.96086) mm` の OV3660 候補であり、既存候補カメラの現物寸法ではない。基板包絡とカメラ子基板・レンズ包絡を各1式返すが、MicroSDなし/ありの両方を本番数へ足し合わせない。

質量は、XIAO主基板とSense拡張基板を合わせた設計余裕値 `10 g`、カメラ子基板・レンズを `5 g` として引数で渡せる。いずれも実測値ではなく `UNVERIFIED_DESIGN_ALLOWANCE` である。機械側はこの設計余裕値を候補としてメッシュの重心・慣性へ渡し、測定済み総質量へ自動加算しない。配線の残差を固体箱へまとめない。

## 印刷保持台の案

`tools/xiao_retention_plan.py` は `eye_pod_camera` ローカルで、次の候補を作る。

- 基板包絡の各側に `0.30 mm` の逃げを置いた床。目標壁厚は `1.20 mm`、印刷時は床面を下にする。床のY幅は、辺接触だけで非多様体にならないよう `基板幅 + 2×逃げ + 2×リブ壁厚` とする。
- 床の左右に、基板の長さに合わせた高さ `3.0 mm` の短いリブを2本置く。既定の `wall/3 = 0.4 mm` をリブ下端だけ床へ沈め、上端の高さと基板側の逃げは維持する。リブは `xiao_tray_rib_left_candidate` と `xiao_tray_rib_right_candidate` として別々に返す。現段階ではcarrierへ接続していない候補なので、取付面と結合方法は機械側が統合する。
- `xiao_tray_holder3_union_candidate` は上の床と2本のリブを共通の基板座標で一体化した1個の候補STLである。構成部品3個と同時に印刷・数量計上せず、適合試験または一体印刷案の比較用に扱う。メッシュ役割と +Z4 mm 適用対象は計画JSONの `holder_mesh_policy.roles` に保存する。
- 既購入の結束バンドを通す候補穴は幅 `3.0 mm`・高さ `1.0 mm` を初期値とし、USB、コネクタ、アンテナ、MicroSDの操作側を避ける。追加ねじを前提にしない。
- standalone helper の床・左右リブ・holder3 は `UNSLOTTED_BASELINE_HELPER_OUTPUT` であり、この候補STL自体には実スロットを重ねて数えない。最終carrierへ組み込むときだけ `tools/print_first_assembly.py::_xiao_add_strap_slots` が `config.py:PRINT_FIRST_XIAO` の同じ寸法源から一度だけスロットを切削し、helper候補と最終carrierを二重計上しない。
- 基板の固定は非導電ストラップまたは機械的クリップを主経路とし、接着だけに依存しない。取付面は頭部・yaw・脚の凍結後に機械側が選ぶ。
- カメラは既存の中央カメラ殻/carrierを使い、交換カメラ用の本番部品を作らない。FPCの入口・ロック方向・曲げ始点を実物で確認してから経路を決める。

候補STLは `xiao-retention-candidate/` に出力される。包絡と保持台は寸法から作る候補箱であり、実メッシュ適合、自己干渉、FPCの耐久、量産印刷の合格を示さない。`without_sd_candidate` と `with_sd_candidate` は比較用の二つの組で、採用時はどちらか一組だけを選ぶ。今回の再生成では各組について、holder3のSTL再読が `watertight=true`・`solid_count=1`、基板占有との交差体積が `0.0 mm³` となることを記録した。これは局所候補の幾何検査であり、carrier接続や実物適合の合格ではない。

計画JSONのFPC端点記録では、固定カメラ端と +Z4 mm 後の基板コネクタ端の直線距離を `6.95743 mm`、候補自由長を `9.2 mm`、残りの長さを `2.24257 mm` としている。これは保存済み候補フレームによる名目値であり、既存候補FPCの改版、入口、曲率、ロック方向の確認後まで `UNVERIFIED` のまま扱う。

## 機械側へ渡すAPI

独立した入力は次の六つである。

```text
build_holder_spec(
  board_size_mm,
  board_frame_flat_to_chassis,
  camera_frame_chassis,
  fpc_start_mm,
  connector_center_mm,
  fpc_free_length_mm,
  base_camera_frame=E.camera_mount_frame({}),
  rib_floor_overlap_mm=None, # 既定は wall_mm/3。正の明示値も指定可能
  holder_z_offset_mm=0.0,   # cameraローカル +Z。基板/保持台だけへ適用
)
```

`base_camera_frame` を省略すれば camera ローカルまでを検査でき、最終 `A.context` のフレームを渡せば最終 base の基板・FPC座標も返す。計画JSONに含まれる通常設定の参照フレームは最終値の代わりに使わない。`make_holder_meshes(spec)` は原本を読み込まず `spec` の数値だけから、全基板占有、カメラ子基板・レンズ占有、床、左右リブ、holder3一体化候補を返す。配置側は `holder_mesh_role()` / `holder_mesh_offset_policy()` を使い、カメラ子基板・レンズだけを +Z4 mm の対象から外す。`inspect_holder_meshes` は生成直後、`inspect_serialized_holder_meshes` はSTL再読後に、holder3の閉体・1固体と基板占有との交差体積を確認する。`export_holder_meshes` は相対パスとSHA-256を記録する。共有 `config.py`、body、assemblyを補助内で書き換えない。

公開実行時に必要なのは保存済みの測定要約JSON、候補配置JSON、source-registerの出典情報と数値引数だけである。メーカーのSTEP/STL原本は実行時依存にしない。原本の出典・SHA-256は [primary-sources/source-register.json](primary-sources/source-register.json) に残し、再配布条件が未確認の原本を公開物へ複製しない。

## 実物で閉じる条件

1. XIAO本体、Sense拡張基板、USB、shield、ヘッダ、MicroSD装着時の外形をノギスと写真で記録する。
2. カメラ基板の現物型番、レンズ中心、接点幅、FPCの全長・自由長・最小曲げ半径、ロック爪の向きを記録する。STEPの `Camer_Module` と同一とはみなさない。
3. `board_to_camera` と `board_to_base` を保ったまま、凍結後の頭殻、camera carrier、yaw/脚、基板、配線の実メッシュで自己干渉・挿入・抜差しを検査する。
4. トレー1個と基板・カメラ1式を印刷し、固定力、FPCの引張り、USB/MicroSDの整備性を確認する。合格前に量産数を増やさない。

現在の状態は `CANDIDATE_ONLY_UNVERIFIED_HARDWARE_AND_FREEZE2_GEOMETRY` である。旧カメラ世代の候補寸法、FPC自由長 `9.2 mm`、直線経路、候補最小曲率は参考値に留め、印刷・強度・歩行の合格根拠へ昇格しない。
