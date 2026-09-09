"""全 STL の一括生成。

usage: cd hardware/src && ../../.venv/bin/python build_all.py
出力: hardware/stl/*.stl
腕パーツは右腕を設計し、左腕 (*_L) はミラー出力する。
"""
import arm_shell
import make_arm
import make_audio
import make_camera
import make_chassis
import make_eye
import make_head
import make_leg
from lib import export
from shell_mod import shin_shell, thigh_cap, SHIN_TOP_Z

if __name__ == "__main__":
    make_leg.build_all()
    # v3 放射配置: FR/RL の 2 脚はミラー版 (_m)。45° ペア (FR-RR/FL-RL) の
    # 間で股ピッチ/膝サーボの側方張り出しが向き合わないようにする
    # (check_leg_assembly でミラー無しは中立でも 3.6cm³ 干渉と実測)
    print("[leg parts (FR/RL, mirrored)]")
    for name in ("coxa_bracket", "femur_link", "tibia_link"):
        export(getattr(make_leg, name)().mirror([0, 1, 0]), f"{name}_m")
    print("[chassis]")
    export(make_chassis.chassis(), "chassis")
    export(make_chassis.pod_neck(), "pod_neck")
    export(make_chassis.battery_cradle(), "battery_cradle")
    print("[shells]")
    export(shin_shell().rotate([180, 0, 0]).translate([0, 0, SHIN_TOP_Z]), "shin_shell")
    export(shin_shell().rotate([180, 0, 0]).translate([0, 0, SHIN_TOP_Z])
           .mirror([0, 1, 0]), "shin_shell_m")
    export(thigh_cap(), "thigh_cap")
    arm_parts = make_arm.build_all()
    arm_shell.build_all()
    print("[arm parts (left, mirrored)]")
    for name in ("shoulder_bracket", "upper_arm", "forearm", "claw_mount"):
        m = getattr(make_arm, name)()
        export(m.mirror([0, 1, 0]), f"{name}_L")
    for name in ("arm_pod_upper", "arm_pod_lower", "elbow_shell"):
        m = getattr(arm_shell, name)()
        export(m.mirror([0, 1, 0]).simplify(0.01), f"{name}_L")
    # 爪ハブ/指/指先チップは無加工のキット STL (model/Arm_Left_Claw_Grey,
    # Arm_Left_Finger_Black_x3, Arm_Left_FingerTip_Grey_x3) を両腕共通で
    # 鏡映使用 (印刷は 3MF プレートから直接。docs/printing.md 参照。
    # hardware/src では加工しないため build_all の対象外)
    make_eye.build_all()
    make_audio.build_all()
    make_camera.build_all()
    print("[head] Head_Bottom_Armcut (腕ソケット拡口+マウス配線受け穴, "
          "shoulder_bracket 依存のため腕生成後)")
    export(make_head.head_bottom_armcut(), "Head_Bottom_Armcut")
    print("""
必要数:
  脚: coxa_bracket/femur_link/tibia_link/shin_shell 標準 ×2 (FL,RR) +
      ミラー版 _m ×2 (FR,RL)。leg_foot_bored(PLA灰)/foot_pad(TPU)/thigh_cap
      は対称なので共通 ×4。Leg_Toe_Black_x12 (元キット STL 150%, 無加工)
      ×3/脚=12。現行は根元嵌合と先行接地が未成立 (RV-06)。組立前に改修が必要
  胴: chassis / pod_neck / battery_cradle 各1
  腕: shoulder_bracket/upper_arm/forearm/claw_mount/arm_pod_upper/
      arm_pod_lower/elbow_shell 右用+_L 各1 (固定爪化, 2026-07-29)。
      爪ハブ = 元 Arm_Left_Claw_Grey ×2 (鏡映使用) / 指 = 元
      Arm_Left_Finger_Black_x3 ×6 (3本×2腕, 差込) / 指先 = 元
      Arm_Left_FingerTip_Grey_x3 ×6 (接着) — いずれも 3MF プレートから
      無加工 150% で印刷 (docs/printing.md)
  目: eye_pod (白, 元キット形状) / eye_carrier 各2 (左右キョロキョロ) +
      eye_pod_camera (中央目, 固定カメラ) / camera_carrier (完全内蔵の
      隠しパーツ) 各1
  マウス砲: Mouth_Cannon_Bored/Mouth_Neck_Bored/Mouth_Ball_Bored 各1 (元パーツと
      差し替え) / audio_cradle_mic/audio_cradle_spk 各1 (完全内蔵の隠しパーツ)
  カメラ: 上記 eye_pod_camera/camera_carrier 参照 (2026-07-28 設計変更で
      ポッドのメインアイではなく頭部中央目へ移設。ポッドのメインアイは
      元パーツ Cabin_Eye_White をそのまま無加工で使う)
  頭部: Head_Bottom_Armcut を Head_Bottom_Blue の代わりに1個印刷 (2026-07-30
      追加。腕ソケットを肩ヨー可動域全域で干渉しないよう拡口済み+マウス
      ソケット奥に配線受け穴を焼き込み済み。Head_Top_Eyecutは別途
      tools/make_head_eyecut.pyで生成。頭内収納と頭固定は未解決)
  ポッドネック: pod_neck 先端 NECK_TAPER_LEN 区間を丸ポストへ絞り込み済み
      (2026-09-05監査: TailJoint_Blue/Ballの中実部分と実体交差が残る。
      被せるだけでは組立できないため、首/受け側の設計検証が必要)""")
