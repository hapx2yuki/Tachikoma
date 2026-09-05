# AGENTS.md — タチコマ歩行ロボット化プロジェクト

> エージェント (Codex / Claude 等) 向けの運用規約。**初回は必ず `docs/HANDOFF.md` を通読すること**
> (プロジェクトの現在地・ロードマップ・落とし穴 54 項目の完全な引き継ぎ文書)。

## プロジェクト概要と現在地

`TACHIKOMA.3mf` (ユーザー本人が著作権を持つ 3D プリントキット) を 12 サーボ歩行ロボット化する。
設計と物理製作を並行中。2026-09-05 の独立監査で制御・検証の欠陥を修正し、
**頭内のケース収納・頭固定/首接合・足裏支持の機構問題を確認**。数値検査のPASSを実機完成と解釈しない。
最新の根拠・残課題は `docs/audits/20260905-round2/README.md`。第2次ではケース/基板を含む収納、
全部品の自己干渉、実起動と材料別の接触まで検査を拡張。既に購入・印刷した部品は棚卸しして活かす。

## 実行環境

- Python: `.venv/bin/python` (trimesh / manifold3d / mujoco / yourdfpy / scikit-image [2026-08-22 追加, make_head_eyecut.py の marching cubes 用] 等導入済み)
- PlatformIO: `.venv/bin/pio` (**PATH に pio は無い**)。`firmware/platformio.ini` は **`espressif32@6.12.0` に固定**
  (Arduino core 2.0.17, legacy `driver/i2s.h`)。2026-09-04 に未固定のまま同居していた pioarduino 55.x へ解決され
  SCons が TypeError で落ちた。その pioarduino が `tool-esptoolpy` を 5.x に差し替えた副作用で `.venv` に
  `intelhex` が必要 (`.venv/bin/pip install intelhex` 済み)。`firmware/src/idf_component.yml` が生成されたら
  pioarduino 経由のビルドなので削除して platform を確認する
- Git管理あり (`origin: hapx2yuki/Tachikoma`, 2026-09-05確認)。変更前に `git status` を確認し、
  未コミット変更を保持。既存STL/3MFの上書き前にはバックアップを取ること
- キット元データの記録先: `~/Downloads/TACHIKOMA.3mf` (組立座標と完成写真の一次情報)。2026-09-05の実行時には不在。過去JSONの記録を元ファイル再確認と取り違えない

## 検証の掟 (設計変更時は全部回す)

```
.venv/bin/python hardware/src/build_all.py        # STL 再生成
.venv/bin/python tools/make_head_eyecut.py        # 頭部内殻・目穴（別生成）
.venv/bin/python tools/export_urdf.py             # URDF 再生成
.venv/bin/python tools/check_leg_assembly.py
.venv/bin/python tools/check_coxa_sweep.py         # 外向きヨーも含む全ペア
.venv/bin/python tools/check_static_assembly.py --json outputs/static-assembly.json # 固定部品同士も検査
.venv/bin/python tools/check_screw_bosses.py
.venv/bin/python tools/sim_gait.py
.venv/bin/python tools/check_leg_link_strength.py
.venv/bin/python tools/check_arm.py
.venv/bin/python tools/check_eye.py
.venv/bin/python tools/check_audio.py
.venv/bin/python tools/check_mouth_chassis.py
.venv/bin/python tools/check_kit_transforms.py
.venv/bin/python tools/check_camera.py
.venv/bin/python tools/check_shin_arm_leg.py
.venv/bin/python tools/check_head_pod_clearance.py
.venv/bin/python tools/check_pod_neck_strength.py
.venv/bin/python tools/check_urdf.py              # 項目数は実行結果を参照
.venv/bin/pio run -d firmware
```

期待結果: **全て PASS / OK / SUCCESS**。1 つでも落ちたら原因切り分けまでがタスク。
ただし足裏支持、印刷材の異方性・たわみ、現物の電源容量・トルクは別の実機確認事項。
`tools/check_toe_contact.py`、`tools/check_print_strength_sensitivity.py`、
`tools/check_print_artifacts.py` の不合格・要確認事項も報告し、隠さない。
制御変更時は `tools/tests/` の実コード回帰試験と、通常/校正モードの両ビルドを行う。
物理シムは `tools/sim_physics.py --novideo --metrics <新しい保存先>` で条件・ハッシュ付き結果を残す。
まとめて再現する場合は `tools/run_design_audit.py --phase generate --output-dir <空の生成ログ先>`、
次に `--phase verify --output-dir <別の空の検査ログ先>`。生成は直列、検査は2並列。
実C++指令・20ケース占有・材料別接触・条件比較は `tools/sim_stress.py` と第2次監査の保存コマンドを使う。

## 設計の鉄則 (詳細と実例は docs/HANDOFF.md §4)

1. **見える形状は元キット準拠** — 意匠パーツの無断削除・改変禁止。加工は不可視の内部
   (`_Bored`/`_Eyecut`/`_Armcut` 系) のみ。切削の要否が出たらユーザーに確認
2. **0.1mm 精度** — 配置・嵌合は実メッシュ (ブーリアン/レイキャスト) で数値検証。「見た目で合ってる」は不可
3. **数値は `hardware/src/config.py` に一元化** — firmware/検証スクリプトへの literal 複製は必ず drift する。
   検証側は config.h を regex 実読して突合する
4. **検証できないものは UNVERIFIED と明記** — 出典と確認日を付ける。捏造・楽観的既定値は禁止
5. **ソフトリミット/ガードは target + cur_ (スルーレート後) の 2 段適用** — 目標側だけだと遷移で必ず破れる
6. **干渉判定は「firmware 到達可能集合」基準** — gait.h のワークスペース射影が出力できない姿勢の干渉は
   実害なし (`tools/check_shin_arm_leg.py` の `pk_reachable()`)。ただし URDF/Isaac は関節直接駆動なので別
7. **レンダ・検証は新規プロセスで** — 生成・配置更新後に新規起動する。メッシュキャッシュは現在mtime_ns/サイズも確認するが、import済み定数と配置は更新されない

## ドキュメントマップ

| 目的 | ファイル |
|---|---|
| 完全な引き継ぎ (必読) | `docs/HANDOFF.md` |
| 設計全体像 | `README.md` |
| 部品・工具の購入 (Amazon 一括カート) | `docs/shopping.md` |
| 印刷順序 (Go/No-Go ゲート) | `docs/print_manifest.md` / `docs/printing.md` / `docs/filament.md` |
| 組立手順 | `docs/assembly.md` |
| 配線 | `docs/wiring.md` |
| 音声会話 (LLM ボイス) | `docs/voice.md` |
| URDF / Isaac Sim | `docs/urdf.md` |
