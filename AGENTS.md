# AGENTS.md — タチコマ歩行ロボット化プロジェクト

> エージェント (Codex / Claude 等) 向けの運用規約。**初回は必ず `docs/HANDOFF.md` を通読すること**
> (プロジェクトの現在地・ロードマップ・落とし穴 54 項目の完全な引き継ぎ文書)。

## プロジェクト概要と現在地

`TACHIKOMA.3mf` (ユーザー本人が著作権を持つ 3D プリントキット) を 12 サーボ歩行ロボット化する。
**設計フェーズは完了** (2026-07-31 時点: 全検証グリーン、MuJoCo 物理シミュレーションで歩行実証済み)。
現在は**物理製作フェーズの入口** (部品発注 → サーボ実測 → テスト印刷 → 組立)。

## 実行環境

- Python: `.venv/bin/python` (trimesh / manifold3d / mujoco / yourdfpy / scikit-image [2026-08-22 追加, make_head_eyecut.py の marching cubes 用] 等導入済み)
- PlatformIO: `.venv/bin/pio` (**PATH に pio は無い**)。`firmware/platformio.ini` は **`espressif32@6.12.0` に固定**
  (Arduino core 2.0.17, legacy `driver/i2s.h`)。2026-09-04 に未固定のまま同居していた pioarduino 55.x へ解決され
  SCons が TypeError で落ちた。その pioarduino が `tool-esptoolpy` を 5.x に差し替えた副作用で `.venv` に
  `intelhex` が必要 (`.venv/bin/pip install intelhex` 済み)。`firmware/src/idf_component.yml` が生成されたら
  pioarduino 経由のビルドなので削除して platform を確認する
- **このリポジトリは git 管理外** — 破壊的変更の前にはバックアップを取ること
- キット元データ: `~/Downloads/TACHIKOMA.3mf` (組立座標フォレンジクスと完成写真の一次情報)

## 検証の掟 (設計変更時は全部回す)

```
.venv/bin/python hardware/src/build_all.py        # STL 再生成 (make_head_eyecut.py も忘れず)
.venv/bin/python tools/export_urdf.py             # URDF 再生成
.venv/bin/python tools/check_leg_assembly.py
.venv/bin/python tools/check_screw_bosses.py
.venv/bin/python tools/sim_gait.py
.venv/bin/python tools/check_arm.py
.venv/bin/python tools/check_eye.py
.venv/bin/python tools/check_audio.py
.venv/bin/python tools/check_camera.py
.venv/bin/python tools/check_shin_arm_leg.py
.venv/bin/python tools/check_head_pod_clearance.py
.venv/bin/python tools/check_pod_neck_strength.py
.venv/bin/python tools/check_urdf.py              # 380 項目
.venv/bin/pio run -d firmware
```

期待結果: **全て PASS / OK / SUCCESS** (既知 NG はゼロ)。1 つでも落ちたら原因切り分けまでがタスク。

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
7. **レンダ・検証は新規プロセスで** — `tools/make_visuals.py` のメッシュキャッシュは mtime 非対応

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
