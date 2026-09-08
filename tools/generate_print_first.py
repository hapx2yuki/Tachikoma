#!/usr/bin/env python3
"""印刷優先構成を一つの失敗閉鎖・直列入口から生成する。

この入口は、通常部品の再生成と印刷優先候補の生成順を固定する。生成物を
後段の検査が古い組合せで読まないよう、実行中は
``outputs/print-first-20260905/print-first-generation.incomplete`` を残し、全段
成功と構造契約の確認が終わったときだけ削除する。途中で失敗した場合は
マーカーを残したまま終了するため、既存のJSONやSTLを成功結果として扱えない。

直列順（各段は新しいPythonプロセス）:

1. ``hardware/src/build_all.py``（既存部品）
2. ``tools/make_head_eyecut.py``（頭上殻の生成）
3. ``tools/xiao_retention_plan.py``（XIAO候補と比較用メッシュ）
4. ``hardware/src/make_print_first_feet.py``（足裏候補）
5. ``tools/check_print_first_feet.py``（足裏候補の再読検証）
6. ``hardware/src/make_print_first_leg.py``（脚候補）
7. ``hardware/src/make_print_first_body.py``（胴・頭・棚・carrier候補）
8. ``tools/generate_print_first_profile.py``（制御プロファイルヘッダー）
9. ``tools/print_first_assembly.py``（最終印刷優先URDFとメッシュ束）

実機の適合・材料強度・FPCの曲げは、この生成入口の成功条件には含めず、各
台帳の ``UNVERIFIED`` として後段へ渡す。Gitのコミット、push、公開は行わない。
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from print_first_source_closure import PRINT_FIRST_GENERATOR_SOURCE_CLOSURE  # noqa: E402

PYTHON = ROOT / ".venv" / "bin" / "python"
OUTPUT_ROOT = ROOT / "outputs" / "print-first-20260905"
MARKER = OUTPUT_ROOT / "print-first-generation.incomplete"
RECORD = OUTPUT_ROOT / "print-first-generation.json"
RECORD_DIGEST = OUTPUT_ROOT / "print-first-generation.digest.json"
LOCK_PATH = OUTPUT_ROOT / ".print-first-generation.lock"

# These are the only states this entry point is allowed to publish.  A broad
# ``not FAIL`` check is insufficient: a newly introduced ``WARN`` or
# ``UNVERIFIED`` state must stop the serial build until it has been reviewed.
PERMITTED_STATUS_STATES = {
    "xiao": frozenset({"CANDIDATE_ONLY_UNVERIFIED_HARDWARE_AND_FREEZE2_GEOMETRY"}),
    "feet": frozenset({"BENCH_CANDIDATE: CAD検査と実物試験を区別する"}),
    "legs": frozenset({"GEOMETRY_CANDIDATE_PHYSICAL_FIT_UNVERIFIED"}),
    "body": frozenset({"GEOMETRY_GENERATED_NOT_YET_FULL_ASSEMBLY_VALIDATED"}),
    # The profile header carries the design candidate's status from
    # PRINT_FIRST_GAIT.  ``PASS_SOURCE_CONFIG_BOUND`` is the validator's
    # result label, not the status serialized in the header.
    "profile": frozenset({"CANDIDATE_NOT_ADOPTED"}),
    "urdf": frozenset({"SERIALIZED_PARTS_MANIFEST"}),
}
FEET_VERIFICATION_STATUS_ALLOWLIST = frozenset({"PASS_GEOMETRY_BENCH_REQUIRED"})
FEET_EXPECTED_OUTPUT_NAMES = frozenset({
    "tpu_shoe", "pla_spacer_positive_y", "pla_spacer_negative_y",
    "pla_spacer_upper_positive_y", "pla_spacer_upper_negative_y",
    "shin_shell_retained", "shin_shell_retained_print",
    "shin_shell_retained_m", "shin_shell_retained_m_print",
    "tpu_shoe_print", "pla_spacer_print",
})
XIAO_EXPECTED_GEOMETRY_CHECK_NAMES = frozenset({
    "without_sd_pre_serialization",
    "without_sd_serialized_roundtrip",
    "with_sd_pre_serialization",
    "with_sd_serialized_roundtrip",
})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Firmware files opened by the serial generation process (directly or through
# an imported local generator).  ``config.h`` is read as data by
# ``hardware/src/make_head.py``, ``tools/export_urdf.py``,
# ``tools/make_visuals.py`` and the transitively imported ``tools/sim_gait.py``.
# ``print_first_gait.h`` is a same-run output, while ``profile_config.h`` and
# the other firmware headers are compiled/validated by the later simulation
# lane rather than read by this geometry generation lane; they therefore
# belong to that lane's freeze ledger, not this input closure.
FIRMWARE_SOURCE_FREEZE_STATIC_PATHS = (
    "firmware/src/config.h",
)

# Source files which form the immutable source closure for one run.  The
# existing assembly manifests add audit-input files to this set at start-up;
# generated XIAO output is explicitly classified as a same-run generated
# source because the body manifest consumes it after the XIAO stage.
SOURCE_FREEZE_STATIC_PATHS = tuple(dict.fromkeys((
    *PRINT_FIRST_GENERATOR_SOURCE_CLOSURE,
    "hardware/src/config.py",
    "hardware/src/make_print_first_feet.py",
    "hardware/src/make_print_first_leg.py",
    "hardware/src/make_print_first_body.py",
    "hardware/src/make_leg.py",
    "hardware/src/make_ld220_adapter.py",
    "tools/xiao_retention_plan.py",
    "tools/print_first_assembly.py",
    "tools/print_first_components.py",
    "tools/export_urdf.py",
    "tools/generate_print_first_profile.py",
    "tools/make_head_eyecut.py",
    "tools/check_print_first_feet.py",
    "tools/kit_assembly.py",
    "tools/make_visuals.py",
    "tools/mesh_checks.py",
    "tools/sim_gait.py",
    "tools/data/kit_assembly_front.json",
    "tools/data/kit_assembly_rear.json",
    "hardware/src/build_all.py",
    "tools/generate_print_first.py",
    *FIRMWARE_SOURCE_FREEZE_STATIC_PATHS,
    "docs/audits/20260905-round2/primary-sources/xiao-step-measured/camera-removed-summary.json",
    "docs/audits/20260905-round2/primary-sources/xiao-step-measured/assembly-bounds.json",
    "docs/audits/20260905-round2/camera-ov3660-candidate/comparison.json",
    "docs/audits/20260905-round2/camera-ov3660-candidate/xiao-placement-search.json",
    "docs/audits/20260905-round2/camera-ov3660-candidate/xiao-cradle-comparison.json",
    "docs/audits/20260905-round2/primary-sources/source-register.json",
)))
GENERATED_SOURCE_PATHS = frozenset({
    "docs/audits/20260905-round2/xiao-retention-plan.json",
})
# ``build_all.py`` intentionally regenerates the tracked hardware/stl tree
# before the print-first feet stage.  These paths must be recorded and
# hash-checked when consumed, but cannot be immutable inputs for this run.
GENERATED_SOURCE_PREFIXES = (
    "hardware/stl/",
    "outputs/print-first-20260905/",
    "hardware/urdf-print-first/",
    "firmware/src/print_first_gait.h",
    "docs/audits/20260905-round2/xiao-retention-candidate/",
)

# Source directories are scanned from the checkout itself on every run.  The
# selectors deliberately exclude bytecode/cache files while covering future
# Python tools, local data, hardware generators, and the complete model tree.
SOURCE_DIRECTORY_SELECTORS = (
    ("tools", "python"),
    ("tools/data", "all"),
    ("hardware/src", "python"),
    ("model", "all"),
    # XIAO retention reads the measured STEP/STL source directory directly;
    # freeze the complete directory so a newly added or changed input cannot
    # enter the run through a glob that the old manifest did not enumerate.
    ("docs/audits/20260905-round2/primary-sources/xiao-step-measured", "all"),
)
SOURCE_DIRECTORY_ROOTS = frozenset(path for path, _ in SOURCE_DIRECTORY_SELECTORS)

# Runtime files whose literal repository path operands are audited.  The
# broader directory selectors below freeze every local Python/data file; this
# smaller set avoids treating test fixtures and prose strings as executable
# dependencies while still covering each serial stage and its local imports.
SOURCE_LITERAL_AUDIT_PATHS = frozenset({
    "tools/generate_print_first.py",
    "hardware/src/build_all.py", "hardware/src/lib.py",
    "hardware/src/make_leg.py", "hardware/src/make_arm.py",
    "hardware/src/arm_shell.py", "hardware/src/make_eye.py",
    "hardware/src/make_audio.py", "hardware/src/make_camera.py",
    "hardware/src/make_chassis.py", "hardware/src/make_head.py",
    "hardware/src/shell_mod.py",
    "hardware/src/make_print_first_feet.py",
    "hardware/src/make_print_first_leg.py",
    "hardware/src/make_print_first_body.py",
    "hardware/src/make_ld220_adapter.py",
    "tools/make_head_eyecut.py", "tools/xiao_retention_plan.py",
    "tools/check_print_first_feet.py", "tools/generate_print_first_profile.py",
    "tools/print_first_assembly.py", "tools/print_first_components.py",
    "tools/export_urdf.py", "tools/kit_assembly.py", "tools/make_visuals.py",
    "tools/mesh_checks.py", "tools/sim_gait.py",
})

# These are the output roots owned by this orchestrator.  A root is either a
# directory or one exact generated file.  Verified historical 3MF plates stay
# in hardware/stl and are excluded by the dedicated historical ledger before
# backup, quarantine, and final output inventory checks.
OWNED_OUTPUT_ROOTS = (
    "hardware/stl",
    "hardware/urdf-print-first",
    "firmware/src/print_first_gait.h",
    "docs/audits/20260905-round2/xiao-retention-plan.json",
    "docs/audits/20260905-round2/xiao-retention-candidate",
    "outputs/print-first-20260905/feet",
    "outputs/print-first-20260905/legs",
    "outputs/print-first-20260905/body",
    "outputs/print-first-20260905/print-first-generation.json",
    "outputs/print-first-20260905/print-first-generation.digest.json",
    "outputs/print-first-20260905/print-first-generation.incomplete",
)
# Nested ``*.incomplete`` files are never valid final outputs.  The root
# orchestrator marker is intentionally handled separately because it is
# present while the full serial run is active.
OWNED_INCOMPLETE_ROOTS = tuple(
    root for root in OWNED_OUTPUT_ROOTS
    if root != "firmware/src/print_first_gait.h"
)

STAGE_OUTPUT_ROOTS = {
    "legacy_build": ("hardware/stl",),
    "head_eyecut": ("hardware/stl",),
    "xiao_retention": (
        "docs/audits/20260905-round2/xiao-retention-plan.json",
        "docs/audits/20260905-round2/xiao-retention-candidate",
    ),
    "print_first_feet": ("outputs/print-first-20260905/feet",),
    "check_print_first_feet": ("outputs/print-first-20260905/feet",),
    "print_first_legs": ("outputs/print-first-20260905/legs",),
    "print_first_body": ("outputs/print-first-20260905/body",),
    "print_first_profile": ("firmware/src/print_first_gait.h",),
    "print_first_urdf": ("hardware/urdf-print-first",),
}
# Some stages intentionally share a root.  The handoff is append-only: the
# later stage must receive every file emitted by the earlier stage as a
# byte-for-byte input, and it may add files under that root but may not mutate
# an existing path.  Recording this policy makes the root overlap explicit
# instead of relying on an accidental directory snapshot.
STAGE_OUTPUT_HANDOFFS = {
    "legacy_build": {
        "roots": ["hardware/stl"],
        "mode": "append_only",
        "next_stage": "head_eyecut",
    },
    "head_eyecut": {
        "roots": ["hardware/stl"],
        "mode": "sealed",
        "next_stage": None,
    },
    "xiao_retention": {
        "roots": [
            "docs/audits/20260905-round2/xiao-retention-plan.json",
            "docs/audits/20260905-round2/xiao-retention-candidate",
        ],
        "mode": "sealed",
        "next_stage": None,
    },
    "print_first_feet": {
        "roots": ["outputs/print-first-20260905/feet"],
        "mode": "append_only",
        "next_stage": "check_print_first_feet",
    },
    "check_print_first_feet": {
        "roots": ["outputs/print-first-20260905/feet"],
        "mode": "sealed",
        "next_stage": None,
    },
    "print_first_legs": {
        "roots": ["outputs/print-first-20260905/legs"],
        "mode": "sealed",
        "next_stage": None,
    },
    "print_first_body": {
        "roots": ["outputs/print-first-20260905/body"],
        "mode": "sealed",
        "next_stage": None,
    },
    "print_first_profile": {
        "roots": ["firmware/src/print_first_gait.h"],
        "mode": "sealed",
        "next_stage": None,
    },
    "print_first_urdf": {
        "roots": ["hardware/urdf-print-first"],
        "mode": "sealed",
        "next_stage": None,
    },
}

# Every serial stage has a closed output contract.  A broad directory ledger
# is useful for hashing, but it is not an allowlist: a debug STL, a log, or an
# old artifact must never become a successful output merely because a stage
# happened to leave it below an owned root.  Exact files are used for the
# generator stages; the URDF exporter has a deterministic, link-scoped mesh
# filename grammar for its generated bundle.
_LEGACY_BUILD_STL_NAMES = frozenset({
    "coxa_bracket.stl", "femur_link.stl", "tibia_link.stl",
    "leg_foot_bored.stl", "foot_pad.stl",
    "coxa_bracket_m.stl", "femur_link_m.stl", "tibia_link_m.stl",
    "chassis.stl", "pod_neck.stl", "battery_cradle.stl",
    "shin_shell.stl", "shin_shell_m.stl", "thigh_cap.stl",
    "shoulder_bracket.stl", "upper_arm.stl", "forearm.stl", "claw_mount.stl",
    "shoulder_bracket_L.stl", "upper_arm_L.stl", "forearm_L.stl", "claw_mount_L.stl",
    "arm_pod_upper.stl", "arm_pod_lower.stl", "elbow_shell.stl",
    "arm_pod_upper_L.stl", "arm_pod_lower_L.stl", "elbow_shell_L.stl",
    "eye_pod.stl", "eye_carrier.stl",
    "Mouth_Cannon_Bored.stl", "Mouth_Neck_Bored.stl", "Mouth_Ball_Bored.stl",
    "audio_cradle_mic.stl", "audio_cradle_spk.stl",
    "eye_pod_camera.stl", "eye_pod_camera_shell.stl",
    "eye_pod_camera_base.stl", "camera_carrier.stl",
    "Head_Bottom_Armcut.stl",
})

# These are the hand-edited print plates committed with the project.  They are
# historical source artifacts, not outputs of the serial geometry stages.  A
# fixed SHA ledger makes the distinction explicit and prevents a changed plate
# from being silently carried past the generator just because its extension is
# not ``.stl``.
HISTORICAL_3MF_SHA256 = {
    "hardware/stl/PETG_1_Chassis.3mf": "3014dc7593398c05bf74ae1187bace19db7ba2297cf03c88ca6a98b78ed9dc93",
    "hardware/stl/PETG_2_Tibia.3mf": "59fbeeaa9d3e1b0bd9d19366423914572cbe133251daaa3f49f2879a5be6e255",
    "hardware/stl/PETG_3_Femur.3mf": "ce75083b66b135f365191e6683ace4d7a3607cb836712a4be391814ccbaba189",
    "hardware/stl/PETG_4_CoxaArm.3mf": "b13e1d8da3990f873512c63779254874958aa7820d6b712fb410ab3aaaf37c79",
    "hardware/stl/PETG_5_Mic.3mf": "370cb91ce35b633598005aefc5d54b19a9c57e095fd42c27c43d5bf909f12dab",
    "hardware/stl/PETG_Walk_1_Chassis.3mf": "cf22186876f6d17d93b9fcd33668155078a9fdc658de9f960fd0e09f34f398a4",
    "hardware/stl/PETG_Walk_2_CoxaFemur.3mf": "1ca1180ad29bbf072330ac29058a1dc41a61a9b3155c27cdab55a12fdc0e4995",
    "hardware/stl/PETG_Walk_3_Tibia.3mf": "ee5fcfe6d1bca8ac149d5481a1eb7028643d9d6d97ad060b9f70afe00ee2f767",
    "hardware/stl/PETG_Walk_4_Rest.3mf": "48457f0ce06ce0682181fb05296586d1d3d75b31e8af13573d2ae4dae2922ebd",
    "hardware/stl/PLA_Black_1.3mf": "46984e5a16be2c17956f9d163051f34ac7855eaf50175e3a4681a4eeac4501bc",
    "hardware/stl/PLA_Matte_Blue_1.3mf": "a1af6044179670b0e063ac1dceff4e794b8e9f49d1a8e56c6719143af925ef6d",
    "hardware/stl/PLA_Matte_Blue_2.3mf": "9c25d8f49a09389fe54216e30209e4bac28d7b785e1b07a3c14a7a6dd608caa9",
    "hardware/stl/PLA_Matte_Blue_3.3mf": "307d83248a804f096369a75c22aa38456d8c1789c7c8ea10bbb06fb73ff09b8f",
    "hardware/stl/PLA_Matte_Blue_4.3mf": "7efa1282187c8c68f6d3d9dd405dbd216ea7f28718fc314a43dbce4e413390e5",
    "hardware/stl/PLA_Matte_Blue_5_HeadTop.3mf": "0aee04b2079500f5c146d09e523b8d9d9aff56def233b05e0b0e8266bad28bf2",
    "hardware/stl/PLA_Matte_Gray.3mf": "6d104623533c24c5ed4d6eb8e913f47d16dd94d5b674356477903caf862faa69",
    "hardware/stl/PLA_Matte_Gray_2.3mf": "f6b6d68064f3323403ecb5042633e439daf07255d5032fabb0195e883b85aeae",
    "hardware/stl/PLA_Matte_White.3mf": "220a89684cdd99a41d9aff4f6dc46a5c1ad74f7329914e917506c41e8cdb44dd",
    "hardware/stl/PLA_Matte_White_2.3mf": "392332b7ef066ffdc976964a7a22ce65264f6efaf57760164ad80764e9125107",
    "hardware/stl/PLA_Matte_White_3_CamBase.3mf": "2e2192275b1f4fecb12cdc5e5191c58b79cff7a72acee94550c76bef56485697",
    "hardware/stl/PLA_Red_1.3mf": "7cd253802c70896d60edead42a00d2bcb2ed31edc0558ab9682426adbe297e1d",
    "hardware/stl/claw_mount_L.3mf": "15f5eac790e37309193e0463705f070e13403d8ca71cb641fa9714eae5be2178",
    "hardware/stl/elbow_shells_PLA_Matte.3mf": "44e2bba0dd004d5df20c62aadb0e1bc19d570c85d058b8d3c301ebb81f889b12",
    "hardware/stl/eye_pod_camera_base_x2.3mf": "5dde4b948f25f96fae3d4084aa070719e4d65b60585906550cfe08ed23b185f3",
    "hardware/stl/foot_pad.3mf": "c80ff76660708da063249cd6425f135e79a60ff9466e0dc304e2a6463dd0a075",
    "hardware/stl/leg_foot_bored.3mf": "1c9e0705d463817006509ca37fbd77523a91c795feeba20d60d1d676fbcc63a6",
}
HISTORICAL_3MF_PATHS = frozenset(HISTORICAL_3MF_SHA256)
_XIAO_CANDIDATE_STL_NAMES = frozenset(
    f"{variant}_{name}.stl"
    for variant in ("without_sd", "with_sd")
    for name in (
        "xiao_all_boards_occupancy", "camera_child_lens_occupancy",
        "xiao_tray_floor_candidate", "xiao_tray_rib_left_candidate",
        "xiao_tray_rib_right_candidate", "xiao_tray_holder3_union_candidate",
    )
)
_FEET_GENERATED_STL_NAMES = frozenset(
    f"{name}_foot_frame.stl" for name in FEET_EXPECTED_OUTPUT_NAMES
    if not name.endswith("_print")
) | frozenset({
    "shin_shell_retained_print.stl", "shin_shell_retained_m_print.stl",
    "tpu_shoe_print.stl", "pla_spacer_print.stl",
})
_FEET_ASSEMBLY_NAMES = frozenset({"assembly.json"}) | _FEET_GENERATED_STL_NAMES
_LEGS_GENERATED_STL_NAMES = frozenset({
    "pf_coxa_bracket.stl", "pf_coxa_bracket_m.stl",
    "pf_femur_link.stl", "pf_femur_link_m.stl",
    "pf_tibia_link.stl", "pf_tibia_link_m.stl",
    "pf_ld220_coxa_cap.stl", "pf_ld220_coxa_cap_m.stl",
    "pf_ld220_femur_cap.stl", "pf_ld220_femur_cap_m.stl",
})
_BODY_GENERATED_STL_NAMES = frozenset({
    "pf_chassis.stl", "pf_head_top_clearanced.stl",
    "pf_ld220_yaw_cap_fl.stl", "pf_ld220_yaw_cap_fr.stl",
    "pf_ld220_yaw_cap_rl.stl", "pf_ld220_yaw_cap_rr.stl",
    "pf_cabin_rail_l.stl", "pf_cabin_rail_r.stl",
    "pf_electronics_shelf_0.stl", "pf_electronics_shelf_1.stl",
    "pf_electronics_shelf_2.stl", "pf_mouth_key.stl",
    "pf_camera_carrier.stl", "pf_eye_pod_camera_clearanced.stl",
    "pf_fixed_claw_l.stl", "pf_fixed_claw_r.stl",
})
_URDF_MESH_LINKS = frozenset({
    "base_link", "eye_l_pod", "eye_r_pod", "eye_pod_camera",
    *(f"leg_{leg}_{kind}" for leg in ("fl", "fr", "rl", "rr")
      for kind in ("coxa", "femur", "tibia")),
    *(f"arm_{side}_{kind}" for side in ("l", "r")
      for kind in ("shoulder", "upper", "forearm")),
})
_URDF_MESH_COLORS = frozenset({
    "286bb1", "607d8b", "888888", "chassis_grey", "grey", "kit_blue",
    "20252a", "248aca", "555555", "bracket_grey", "shell_blue", "white",
})


def _exact_stage_paths(root: str, names) -> frozenset[str]:
    return frozenset((Path(root) / name).as_posix() for name in names)


STAGE_OUTPUT_FILE_CONTRACTS = {
    "legacy_build": {
        "exact": _exact_stage_paths("hardware/stl", _LEGACY_BUILD_STL_NAMES),
        "patterns": (),
    },
    "head_eyecut": {
        "exact": _exact_stage_paths(
            "hardware/stl", _LEGACY_BUILD_STL_NAMES | {"Head_Top_Eyecut.stl"}),
        "patterns": (),
    },
    "xiao_retention": {
        "exact": frozenset({
            "docs/audits/20260905-round2/xiao-retention-plan.json",
            *(_exact_stage_paths(
                "docs/audits/20260905-round2/xiao-retention-candidate",
                _XIAO_CANDIDATE_STL_NAMES)),
        }),
        "patterns": (),
    },
    "print_first_feet": {
        "exact": _exact_stage_paths(
            "outputs/print-first-20260905/feet", _FEET_ASSEMBLY_NAMES),
        "patterns": (),
    },
    "check_print_first_feet": {
        "exact": _exact_stage_paths(
            "outputs/print-first-20260905/feet", _FEET_ASSEMBLY_NAMES | {
                "verification.json", "assembly-preview.png", "shell-local-changes.png",
            }),
        "patterns": (),
    },
    "print_first_legs": {
        "exact": _exact_stage_paths(
            "outputs/print-first-20260905/legs",
            _LEGS_GENERATED_STL_NAMES | {"assembly.json"}),
        "patterns": (),
    },
    "print_first_body": {
        "exact": _exact_stage_paths(
            "outputs/print-first-20260905/body",
            _BODY_GENERATED_STL_NAMES | {"assembly.json"}),
        "patterns": (),
    },
    "print_first_profile": {
        "exact": frozenset({"firmware/src/print_first_gait.h"}),
        "patterns": (),
    },
    "print_first_urdf": {
        "exact": frozenset({
            "hardware/urdf-print-first/tachikoma.urdf",
            "hardware/urdf-print-first/parts_manifest.json",
        }),
        "patterns": (
            re.compile(
                r"^hardware/urdf-print-first/meshes/"
                r"(?:" + "|".join(sorted(map(re.escape, _URDF_MESH_LINKS))) + r")__"
                r"(?:vis_(?:" + "|".join(sorted(map(re.escape, _URDF_MESH_COLORS))) + r")"
                r"|col_[0-9]+)\.stl$"),
        ),
    },
}
# Requested stage inputs are split into immutable source files, dynamic
# directory handoffs, and exact generated manifests.  The latter two are
# expanded only when the stage starts; final validation uses the saved
# ``input_spec`` and recorded hashes instead of expanding a now-larger root.
STAGE_INPUT_DYNAMIC_ROOTS = {
    "legacy_build": (),
    "head_eyecut": ("hardware/stl",),
    "xiao_retention": (),
    "print_first_feet": ("hardware/stl",),
    "check_print_first_feet": ("hardware/stl",),
    "print_first_legs": ("hardware/stl",),
    "print_first_body": (
        "hardware/stl",
        "docs/audits/20260905-round2/xiao-retention-candidate",
        "outputs/print-first-20260905/feet",
        "outputs/print-first-20260905/legs",
    ),
    "print_first_profile": (),
    "print_first_urdf": (
        "hardware/stl",
        "docs/audits/20260905-round2/xiao-retention-candidate",
    ),
}
STAGE_INPUT_REQUIRED_EXACT = {
    "legacy_build": (),
    "head_eyecut": (),
    "xiao_retention": (),
    "print_first_feet": (),
    "check_print_first_feet": (
        "outputs/print-first-20260905/feet/assembly.json",
    ),
    "print_first_legs": (),
    "print_first_body": (
        "docs/audits/20260905-round2/xiao-retention-plan.json",
    ),
    "print_first_profile": (),
    "print_first_urdf": (
        "outputs/print-first-20260905/feet/assembly.json",
        "outputs/print-first-20260905/legs/assembly.json",
        "outputs/print-first-20260905/body/assembly.json",
        "docs/audits/20260905-round2/xiao-retention-plan.json",
    ),
}

# Control ledgers live below the same run root but are checked separately from
# material outputs: including a record's own SHA in its final-output ledger
# would make the record self-referential.  They are still backup targets and
# must be present in the active/final state contract.
CONTROL_OUTPUT_ROOTS = (
    "outputs/print-first-20260905/print-first-generation.json",
    "outputs/print-first-20260905/print-first-generation.digest.json",
    "outputs/print-first-20260905/print-first-generation.incomplete",
)
MATERIAL_OUTPUT_ROOTS = tuple(
    root for root in OWNED_OUTPUT_ROOTS if root not in CONTROL_OUTPUT_ROOTS
)

# ``_SOURCE_DIRECTORY_FREEZE`` records the selected directory snapshots in
# addition to the per-file source SHA map.  This catches new/deleted local
# tools and data that a list built only from old manifests would miss.
_SOURCE_DIRECTORY_FREEZE: dict[str, dict] = {}

# The destination is outside the publication allowlist.  Objects are keyed by
# content hash, so repeated runs do not copy the same 18 MB source tree again.
BACKUP_ROOT = OUTPUT_ROOT / "source-backups"
BACKUP_MUTABLE_ROOTS = (
    "hardware/stl",
    "hardware/urdf-print-first",
    "firmware/src/print_first_gait.h",
    "docs/audits/20260905-round2/xiao-retention-plan.json",
    "docs/audits/20260905-round2/xiao-retention-candidate",
    "outputs/print-first-20260905/feet",
    "outputs/print-first-20260905/legs",
    "outputs/print-first-20260905/body",
    *CONTROL_OUTPUT_ROOTS,
)

_SOURCE_FREEZE: dict[str, str] = {}


def _is_generated_source_path(relative: str) -> bool:
    if relative in GENERATED_SOURCE_PATHS:
        return True
    for prefix in GENERATED_SOURCE_PREFIXES:
        if prefix.endswith("/"):
            if relative == prefix[:-1] or relative.startswith(prefix):
                return True
        elif relative == prefix:
            return True
    return False


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_repo_ancestors(relative: str, label: str) -> None:
    """Reject symlinks anywhere between the repository and a checked path."""
    cursor = ROOT
    for component in PurePosixPath(relative).parts:
        cursor /= component
        if cursor.is_symlink():
            raise ValueError(f"{label}: symlink path component is not permitted: {cursor}")


def _source_tree_snapshot() -> dict[str, dict]:
    """Snapshot selected source directories, including future file additions."""
    snapshot: dict[str, dict] = {}
    for root_relative, selector in SOURCE_DIRECTORY_SELECTORS:
        _, root = _repo_file(root_relative, "source directory")
        if not root.is_dir():
            raise ValueError(f"source directory is missing: {root_relative}")
        stack = [(root, root_relative)]
        while stack:
            current, current_relative = stack.pop()
            try:
                entries = sorted(os.scandir(current), key=lambda entry: entry.name)
            except OSError as exc:
                raise ValueError(f"cannot scan source directory: {current_relative}") from exc
            for entry in entries:
                relative = f"{current_relative}/{entry.name}"
                _repo_relative(relative, "source directory entry")
                child = Path(entry.path)
                if entry.is_symlink():
                    raise ValueError(f"source directory contains a symlink: {relative}")
                if entry.is_dir(follow_symlinks=False):
                    # Python bytecode is a local execution cache, not a source
                    # input.  Skipping it keeps py_compile from changing the
                    # source snapshot while still detecting new source files.
                    if entry.name == "__pycache__":
                        continue
                    if not _is_generated_source_path(relative):
                        snapshot[relative] = {"kind": "directory"}
                    stack.append((child, relative))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise ValueError(f"source directory contains a non-file entry: {relative}")
                if selector == "python" and child.suffix != ".py":
                    continue
                if _is_generated_source_path(relative):
                    continue
                _, checked = _repo_file(relative, "source directory file")
                if checked.stat().st_size == 0:
                    raise ValueError(f"source directory contains an empty file: {relative}")
                snapshot[relative] = {
                    "kind": "file",
                    "size_bytes": checked.stat().st_size,
                    "sha256": _sha(checked),
                }
    return snapshot


def _collect_existing_source_paths() -> set[str]:
    """Collect source paths already declared by the previous manifests.

    Some audit JSON files and the legacy ``hardware/stl`` outputs are generated
    in an earlier stage and are therefore not part of the immutable source set.
    Their path is still collected so a changed path cannot quietly introduce an
    outside source.
    """
    paths = set(SOURCE_FREEZE_STATIC_PATHS)
    # Build the source closure from the checkout itself instead of relying on
    # stale generated manifests.  This covers future tools/data additions and
    # catches both missing and newly introduced source files between stages.
    paths.update(
        relative for relative, row in _source_tree_snapshot().items()
        if row.get("kind") == "file"
    )
    existing_manifests = (
        "outputs/print-first-20260905/feet/assembly.json",
        "outputs/print-first-20260905/legs/assembly.json",
        "outputs/print-first-20260905/body/assembly.json",
        "docs/audits/20260905-round2/xiao-retention-plan.json",
    )

    def walk(value):
        if isinstance(value, dict):
            source_hashes = value.get("source_sha256")
            if isinstance(source_hashes, dict):
                for key in source_hashes:
                    if isinstance(key, str):
                        paths.add(key)
            rows = value.get("sources")
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and isinstance(row.get("path"), str):
                        paths.add(row["path"])
            rows = value.get("source_files")
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and isinstance(row.get("path"), str):
                        paths.add(row["path"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for relative in existing_manifests:
        path = ROOT / relative
        if path.is_file() and not path.is_symlink():
            try:
                walk(_json(path))
            except (OSError, ValueError, json.JSONDecodeError):
                # The generated artifact will be validated after the stage
                # that owns it.  A malformed old artifact must not weaken the
                # static source freeze.
                pass
    # A serial generator can read a concrete file outside the selected Python
    # and model trees through a literal path.  Include those files in the
    # immutable closure before the first stage.  Generated/owned paths remain
    # same-run outputs and are checked by their stage ledgers instead.
    for relative in sorted(SOURCE_LITERAL_AUDIT_PATHS):
        _, source_path = _repo_file(relative, "source closure audit")
        if not source_path.is_file():
            continue
        literals = _static_source_literal_paths(
            source_path.read_text(encoding="utf-8"), relative)
        for literal in literals:
            if _is_generated_source_path(literal) or _is_under_owned_output(literal):
                continue
            candidate = ROOT / literal
            if candidate.is_file() and not candidate.is_symlink():
                paths.add(literal)
    return {_repo_relative(path, "source freeze path") for path in paths}


def _static_source_literal_paths(source: str, relative: str) -> set[str]:
    """Extract concrete repository path literals from executable Python AST."""
    try:
        tree = ast.parse(source, filename=relative)
    except SyntaxError as exc:
        raise ValueError(f"source closure: cannot parse {relative}") from exc
    roots = tuple(
        f"{prefix}/" for prefix in
        ("firmware", "hardware", "tools", "model", "docs", "outputs")
    )
    result = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        raw = node.value.strip()
        if (not raw or "\n" in raw or "\x00" in raw
                or any(token in raw for token in ("*", "{", "}", "<", ">"))
                or any(token in raw for token in (" ", "\t", "#"))):
            continue
        if not raw.startswith(roots):
            continue
        try:
            candidate = _repo_relative(raw.rstrip("/"),
                                       f"source closure literal in {relative}")
        except ValueError:
            # A path-like string embedded in a regular expression or a
            # descriptive error is not a filesystem operand.  Concrete
            # traversal/absolute strings are checked separately by the normal
            # path validators at their call sites.
            continue
        # AST constants also include docstrings and explanatory error text.
        # A concrete source operand must name an existing file/directory or an
        # explicitly owned/generated path.  This still catches a path that is
        # accidentally outside the frozen closure while avoiding false
        # positives such as ``firmware/src/config.h に``.
        candidate_path = ROOT / candidate
        if (not candidate_path.exists()
                and not _is_generated_source_path(candidate)
                and not _is_under_owned_output(candidate)):
            continue
        result.add(candidate)
    return result


def _assert_static_source_closure() -> dict[str, list[str]]:
    """Check literal repo operands in every serial runtime source file.

    Directory snapshots catch additions/edits, while this check catches a
    newly introduced runtime read of an un-frozen file even when the file was
    outside the old manifest set.  Generated/output paths are allowed only by
    the explicit generated/owned policy.
    """
    missing_sources = sorted(
        path for path in SOURCE_LITERAL_AUDIT_PATHS
        if path not in _SOURCE_FREEZE and not _is_generated_source_path(path)
    )
    if missing_sources:
        raise ValueError(
            f"source closure: runtime audit source is outside freeze: {missing_sources!r}")
    findings: dict[str, list[str]] = {}
    frozen_dirs = {
        path for path, row in _SOURCE_DIRECTORY_FREEZE.items()
        if row.get("kind") == "directory"
    }
    for relative in sorted(SOURCE_LITERAL_AUDIT_PATHS):
        if _is_generated_source_path(relative):
            continue
        _, source_path = _repo_file(relative, "source closure audit")
        if not source_path.is_file():
            raise ValueError(f"source closure: audited runtime file is missing: {relative}")
        literals = _static_source_literal_paths(
            source_path.read_text(encoding="utf-8"), relative)
        uncovered = []
        for literal in sorted(literals):
            if (_is_generated_source_path(literal)
                    or _is_under_owned_output(literal)
                    or literal in _SOURCE_FREEZE
                    or literal in _SOURCE_DIRECTORY_FREEZE
                    or literal in SOURCE_DIRECTORY_ROOTS
                    or any(literal.startswith(directory.rstrip("/") + "/")
                           for directory in frozen_dirs)):
                continue
            # A path literal under a selected source directory must still be
            # represented by the freeze map; this also catches a stale or
            # accidentally excluded source file.
            uncovered.append(literal)
        if uncovered:
            findings[relative] = uncovered
    if findings:
        raise ValueError(f"source closure: uncovered runtime paths: {findings!r}")
    return {relative: sorted(_static_source_literal_paths(
        _repo_file(relative, "source closure audit")[1].read_text(encoding="utf-8"),
        relative)) for relative in sorted(SOURCE_LITERAL_AUDIT_PATHS)}


def _initialize_source_freeze() -> dict[str, str]:
    """Snapshot the source closure once, before any generator can run."""
    _SOURCE_FREEZE.clear()
    _SOURCE_DIRECTORY_FREEZE.clear()
    _SOURCE_DIRECTORY_FREEZE.update(_source_tree_snapshot())
    for relative in sorted(_collect_existing_source_paths()):
        if _is_generated_source_path(relative):
            continue
        _, path = _repo_file(relative, "source freeze")
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"source freeze: required source is missing: {relative}")
        _SOURCE_FREEZE[relative] = _sha(path)
    if _SOURCE_FREEZE.get("hardware/src/config.py") is None:
        raise ValueError("source freeze: hardware/src/config.py is missing")
    _assert_static_source_closure()
    return dict(_SOURCE_FREEZE)


def _assert_config_frozen(config_sha256_at_start: str, *, phase: str) -> str:
    """Stop immediately when config.py changes during a serial generation."""
    _assert_source_freeze(phase=phase)
    _, path = _repo_file("hardware/src/config.py", f"config freeze ({phase})")
    actual = _sha(path)
    if actual != config_sha256_at_start:
        raise RuntimeError(
            f"config.py changed during print-first generation ({phase}): "
            f"expected {config_sha256_at_start}, got {actual}")
    frozen = _SOURCE_FREEZE.get("hardware/src/config.py")
    if frozen != actual:
        raise RuntimeError(
            f"config.py differs from source freeze ({phase}): expected {frozen}, got {actual}")
    return actual


def _assert_source_freeze(*, phase: str) -> dict[str, str]:
    """Verify every immutable source path, not just config.py."""
    if not _SOURCE_FREEZE or not _SOURCE_DIRECTORY_FREEZE:
        raise RuntimeError(f"source freeze is not initialized ({phase})")
    current_tree = _source_tree_snapshot()
    if current_tree != _SOURCE_DIRECTORY_FREEZE:
        expected_paths = set(_SOURCE_DIRECTORY_FREEZE)
        current_paths = set(current_tree)
        added = sorted(current_paths - expected_paths)
        removed = sorted(expected_paths - current_paths)
        changed = sorted(
            path for path in expected_paths & current_paths
            if current_tree[path] != _SOURCE_DIRECTORY_FREEZE[path]
        )
        details = []
        if added:
            details.append(f"added={added[:8]!r}")
        if removed:
            details.append(f"removed={removed[:8]!r}")
        if changed:
            details.append(f"changed={changed[:8]!r}")
        raise RuntimeError(
            f"source directory snapshot changed during print-first generation ({phase}): "
            + ", ".join(details))
    current = {}
    for relative, expected in sorted(_SOURCE_FREEZE.items()):
        _, path = _repo_file(relative, f"source freeze ({phase})")
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"source disappeared during generation ({phase}): {relative}")
        actual = _sha(path)
        current[relative] = actual
        if actual != expected:
            raise RuntimeError(
                f"source changed during print-first generation ({phase}): {relative}: "
                f"expected {expected}, got {actual}")
    return current


def _json(path: Path):
    """JSONをNaNなしで読む。生成台帳へ非有限値を持ち込まない。"""
    def reject_constant(value):
        raise ValueError(f"non-finite JSON constant {value!r} in {path}")

    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    _assert_finite_numbers(value, str(path))
    return value


def _assert_finite_numbers(value, label: str) -> None:
    """Reject JSON numbers which overflow to an IEEE non-finite value."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{label}: non-finite number")
    if isinstance(value, dict):
        for key, child in value.items():
            _assert_finite_numbers(child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_finite_numbers(child, f"{label}[{index}]")


def _assert_nonnegative_mm3(value, label: str) -> None:
    """Require non-negative geometry volume fields, including nested records."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key.endswith("_mm3") and isinstance(child, (int, float)) and child < 0:
                raise ValueError(f"{label}.{key}: negative volume")
            _assert_nonnegative_mm3(child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_nonnegative_mm3(child, f"{label}[{index}]")


def _status_text(value) -> str:
    return value if isinstance(value, str) else ""


def _assert_no_failure_status(status, label: str) -> None:
    text = _status_text(status).upper()
    if not text:
        raise ValueError(f"{label}: missing status")
    if "FAIL" in text or "INCOMPLETE" in text or "ERROR" in text:
        raise ValueError(f"{label}: failure status {status!r}")


def _repo_relative(raw, label: str) -> str:
    """Validate a public path before it is joined to ``ROOT``.

    Joining an absolute ``Path`` to ROOT silently discards ROOT, and resolving
    ``a/../b`` before checking it permits traversal.  Both are rejected before
    any filesystem access.  ``$OUTPUT`` is a public display token, not a local
    input path; callers that emit it keep it opaque and never pass it here.
    """
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ValueError(f"{label}: repository-relative path is required")
    if "\\" in raw:
        raise ValueError(f"{label}: backslash paths are not permitted")
    if re.match(r"^[A-Za-z]:($|/)", raw):
        raise ValueError(f"{label}: drive-absolute path is not permitted: {raw!r}")
    if raw.startswith("$OUTPUT"):
        raise ValueError(f"{label}: $OUTPUT is a public display token, not an input path")
    path = PurePosixPath(raw)
    if path.is_absolute() or raw.startswith("~"):
        raise ValueError(f"{label}: absolute path is not permitted: {raw!r}")
    if ".." in path.parts:
        raise ValueError(f"{label}: parent traversal is not permitted: {raw!r}")
    if "." in path.parts:
        raise ValueError(f"{label}: dot path components are not permitted: {raw!r}")
    normalized = path.as_posix()
    if normalized != raw:
        raise ValueError(f"{label}: non-canonical repository-relative path: {raw!r}")
    return normalized


def _repo_file(raw, label: str) -> tuple[str, Path]:
    """Return a checked repository-relative file path.

    Symlinks are rejected even when they point inside the tree; this keeps the
    source and output ledgers tied to an unambiguous repository object.  The
    resolved containment check is retained as a defence-in-depth guard.
    """
    relative = _repo_relative(raw, label)
    _assert_repo_ancestors(relative, label)
    path = ROOT / relative
    if path.is_symlink():
        raise ValueError(f"{label}: symlink path is not permitted: {relative}")
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(ROOT):
        raise ValueError(f"{label}: resolved path escapes repository: {relative}")
    return relative, path


class RunLockBusy(RuntimeError):
    """Another full or validate-only invocation owns the repository lock."""


def _path_relative(path: Path, label: str) -> str:
    candidate = path if path.is_absolute() else ROOT / path
    try:
        relative = candidate.relative_to(ROOT).as_posix()
    except ValueError as exc:
        raise ValueError(f"{label}: path must stay inside repository") from exc
    return _repo_relative(relative, label)


@contextmanager
def _generation_lock(*, read_only: bool = False):
    """Hold or observe the repository lock without creating it in read mode.

    A validation process must not change the checkout merely to validate it.
    In read-only mode an existing shared lock is opened and acquired
    non-blocking.  A missing lock is a fail-closed error: yielding without a
    lock would allow a concurrent full generation to start during validation.
    The read-only path still never creates the parent or leaf.  A full
    generation keeps the historical create-and-lock behaviour.
    """
    relative = _path_relative(Path(LOCK_PATH), "generation lock")
    _, path = _repo_file(relative, "generation lock")
    if read_only and not path.exists():
        # Do not mkdir/touch anything.  A validation without a lock is unsafe
        # because a full generation could create the lock immediately after
        # this check and mutate the files being inspected.
        raise RunLockBusy(f"generation lock is missing; read-only operation is fail-closed: {relative}")
    if path.exists() and not path.is_file():
        raise RuntimeError(f"generation lock is not a regular file: {relative}")
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Re-check after creating missing parents; a replaced parent must not
        # turn the lock into an outside-repository file.
        _repo_file(relative, "generation lock")
    flags = os.O_RDONLY if read_only else (os.O_RDWR | os.O_CREAT)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            raise RunLockBusy(f"generation lock is unavailable: {relative}") from exc
        raise
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise RunLockBusy(f"generation lock is held: {relative}") from exc
            raise
        try:
            yield relative
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


@contextmanager
def _no_bytecode_writes():
    """Keep import machinery from creating ``__pycache__`` during validation."""
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        yield
    finally:
        sys.dont_write_bytecode = previous


def _atomic_json_write(path: Path, payload: dict, label: str) -> None:
    """Write a JSON ledger atomically after checking its repository path."""
    relative = _path_relative(Path(path), label)
    _, target = _repo_file(relative, label)
    target.parent.mkdir(parents=True, exist_ok=True)
    _repo_file(relative, label)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary_relative = _path_relative(temporary, f"{label} temporary")
    try:
        _, checked_temporary = _repo_file(temporary_relative, f"{label} temporary")
        checked_temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        with checked_temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(checked_temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _require_sha(value, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(f"{label}: a lowercase SHA-256 is required")
    return value


def _assert_historical_3mf_contract(*, phase: str) -> frozenset[str]:
    """Verify the preserved hand-edited 3MF set before touching owned outputs."""
    configured = {}
    for raw_path, raw_sha in HISTORICAL_3MF_SHA256.items():
        relative = _repo_relative(raw_path, "historical 3MF path")
        if (relative != raw_path
                or not relative.startswith("hardware/stl/")
                or not relative.lower().endswith(".3mf")):
            raise RuntimeError(
                f"historical 3MF ledger contains an invalid path ({phase}): {raw_path!r}")
        if relative in configured:
            raise RuntimeError(f"historical 3MF ledger has a duplicate path ({phase}): {relative}")
        configured[relative] = _require_sha(raw_sha, f"historical 3MF SHA ({relative})")
    if not configured:
        raise RuntimeError(f"historical 3MF ledger is empty ({phase})")

    _, root = _repo_file("hardware/stl", "historical 3MF root")
    if not root.is_dir():
        raise RuntimeError(
            f"historical 3MF contract failed ({phase}): "
            f"missing={sorted(configured)!r}")
    rows = _owned_inventory("hardware/stl", require_exists=True)
    actual_rows = {
        row["path"]: row for row in rows
        if row["path"].lower().endswith(".3mf")
    }
    expected_paths = set(configured)
    actual_paths = set(actual_rows)
    missing = sorted(expected_paths - actual_paths)
    extra = sorted(actual_paths - expected_paths)
    changed = []
    for path in sorted(expected_paths & actual_paths):
        actual_sha = actual_rows[path]["sha256"]
        if actual_sha != configured[path]:
            changed.append(f"{path} (expected {configured[path]}, got {actual_sha})")
    if missing or changed or extra:
        details = []
        if missing:
            details.append(f"missing={missing!r}")
        if changed:
            details.append(f"changed={changed!r}")
        if extra:
            details.append(f"extra={extra!r}")
        raise RuntimeError(f"historical 3MF contract failed ({phase}): " + ", ".join(details))
    return frozenset(configured)


def _file_record(raw, label: str, *, expected_sha: str | None = None) -> dict:
    relative, path = _repo_file(raw, label)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"{label}: missing or empty file: {relative}")
    actual = _sha(path)
    if expected_sha is not None:
        expected_sha = _require_sha(expected_sha, f"{label}.sha256")
        if expected_sha != actual:
            raise ValueError(f"{label}: stale SHA-256: {relative}")
    return {"path": relative, "exists": True, "sha256": actual}


def _assert_source_record(raw, expected_sha, label: str) -> dict:
    relative = _repo_relative(raw, f"{label}.path")
    expected = _require_sha(expected_sha, f"{label}.sha256")
    record = _file_record(relative, label, expected_sha=expected)
    if _is_generated_source_path(relative):
        return record
    frozen = _SOURCE_FREEZE.get(relative)
    if frozen is None:
        raise ValueError(f"{label}: source is outside the run freeze set: {relative}")
    if frozen != expected:
        raise ValueError(f"{label}: source differs from config/source freeze: {relative}")
    return record


def _assert_source_rows(rows, label: str) -> list[dict]:
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{label}: source rows are missing or empty")
    result = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{label}[{index}]: malformed source row")
        if row.get("exists") is not True:
            raise ValueError(f"{label}[{index}]: exists must be true")
        result.append(_assert_source_record(
            row.get("path"), row.get("sha256"), f"{label}[{index}]"))
    return result


def _assert_source_hashes(data: dict, label: str) -> list[dict]:
    sources = data.get("source_sha256")
    if not isinstance(sources, dict) or not sources:
        raise ValueError(f"{label}: source_sha256 is missing or empty")
    result = []
    for key, expected in sources.items():
        result.append(_assert_source_record(key, expected, f"{label}.source_sha256"))
    return result


def _assert_source_manifest(data: dict, label: str, rows_key: str) -> tuple[list[dict], list[dict]]:
    """Require source row and hash-map views to describe the same freeze set."""
    hash_records = _assert_source_hashes(data, label)
    row_records = _assert_source_rows(data.get(rows_key), f"{label}.{rows_key}")
    hash_by_path = {row["path"]: row["sha256"] for row in hash_records}
    row_by_path = {}
    for row in row_records:
        if row["path"] in row_by_path:
            raise ValueError(f"{label}.{rows_key}: duplicate source path: {row['path']}")
        row_by_path[row["path"]] = row["sha256"]
    if row_by_path != hash_by_path:
        raise ValueError(f"{label}: source_files and source_sha256 describe different freeze sets")
    return row_records, hash_records


def _assert_status(value, key: str, label: str) -> str:
    status = _status_text(value)
    allowed = PERMITTED_STATUS_STATES[key]
    if status not in allowed:
        raise ValueError(f"{label}: status {status!r} is not one of {sorted(allowed)!r}")
    return status


def _assert_stl(raw, label: str, *, require_solid: bool = True,
                expected_sha: str | None = None, require_sha: bool = False,
                bundle_root: Path | None = None) -> dict:
    if isinstance(raw, Path):
        # Public manifests and validator inputs use repository-relative paths;
        # accepting an absolute Path here would create a second path grammar
        # and could silently bypass the absolute/parent traversal guard.
        raw = raw.as_posix()
        relative = _repo_relative(raw, f"{label}.path")
    else:
        relative = _repo_relative(raw, f"{label}.path")
    relative, path = _repo_file(relative, f"{label}.path")
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"{label}: missing or empty STL: {relative}")
    actual_sha = _sha(path)
    if require_sha:
        expected_sha = _require_sha(expected_sha, f"{label}.sha256")
    if expected_sha is not None and _require_sha(expected_sha, f"{label}.sha256") != actual_sha:
        raise ValueError(f"{label}: stale STL SHA-256: {relative}")
    # Every candidate STL is re-read here.  This is intentionally stricter
    # than a byte/file existence check so an open, negative, or multi-solid
    # file cannot make the serial sequence look complete.
    import numpy as np
    import trimesh

    mesh = trimesh.load(path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"{label}: STL did not load as a mesh")
    if (len(mesh.vertices) < 4 or len(mesh.faces) < 4
            or not np.isfinite(mesh.vertices).all()
            or not np.isfinite(mesh.faces.astype(float)).all()
            or not np.isfinite(float(mesh.volume))
            or (require_solid and (
                not mesh.is_watertight
                or not mesh.is_winding_consistent
                or not mesh.is_volume
                or float(mesh.volume) <= 0.0
                or len(mesh.split(only_watertight=False)) != 1))):
        raise ValueError(f"{label}: STL has invalid finite/topology data")
    result = {
        "path": relative,
        "exists": True,
        "sha256": actual_sha,
        "finite": True,
        "watertight": bool(mesh.is_watertight),
        "winding_consistent": bool(mesh.is_winding_consistent),
        "is_volume": bool(mesh.is_volume),
        "signed_volume_mm3": float(mesh.volume),
        "solid_components": len(mesh.split(only_watertight=False)),
        "solid_contract": "closed_positive_one_component" if require_solid else "finite_mesh",
    }
    if require_solid:
        result["positive_volume_mm3"] = float(mesh.volume)
    return result


def _assert_geometry_claim(raw: dict, actual: dict, label: str) -> None:
    """Bind optional manifest topology claims to the reloaded STL.

    The mesh is always checked independently, but accepting a hand-edited
    ``watertight``/component flag would leave a misleading provenance record.
    Older rows may omit these fields, so only fields that are present are
    compared; required path/existence/SHA checks remain unconditional.
    """
    for key in ("finite", "watertight", "winding_consistent", "is_volume"):
        if key in raw and raw[key] != actual[key]:
            raise ValueError(f"{label}: recorded {key} does not match STL")
    for key in ("solid_components", "positive_components", "components", "solid_count"):
        if key in raw:
            value = raw[key]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{label}: recorded {key} is not an integer")
            if value != actual["solid_components"]:
                raise ValueError(f"{label}: recorded {key} does not match STL")
    for key in ("signed_volume_mm3", "volume_mm3", "positive_volume_mm3"):
        if key not in raw:
            continue
        value = raw[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) \
                or not math.isfinite(float(value)):
            raise ValueError(f"{label}: recorded {key} is not a finite number")
        expected = actual["signed_volume_mm3"]
        if key == "positive_volume_mm3":
            expected = actual["signed_volume_mm3"]
            if expected <= 0.0:
                raise ValueError(f"{label}: positive volume claim is not positive")
        if not math.isclose(float(value), expected, rel_tol=0.0, abs_tol=1.0e-3):
            raise ValueError(f"{label}: recorded {key} does not match STL")


def _backup_files_under(root: Path, *, exclude_historical: bool = False) -> list[Path]:
    """List backup targets without following symlinks."""
    if root.is_symlink():
        raise ValueError(f"backup target is a symlink: {root}")
    if not root.exists():
        return []
    if root.is_file():
        return [root]
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"backup target contains a symlink: {path}")
        if path.is_file():
            if (exclude_historical
                    and path.relative_to(ROOT).as_posix() in HISTORICAL_3MF_SHA256):
                continue
            files.append(path)
    return files


def _copy_backup_object(source: Path, object_path: Path, digest: str) -> None:
    """Copy one content-addressed backup object and verify it before use."""
    object_path.parent.mkdir(parents=True, exist_ok=True)
    if object_path.is_symlink():
        raise ValueError(f"backup object may not be a symlink: {object_path}")
    if object_path.is_file():
        if _sha(object_path) != digest:
            raise ValueError(f"backup object hash collision/corruption: {object_path}")
        return
    if object_path.exists():
        raise ValueError(f"backup object is not a regular file: {object_path}")
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{digest}.", suffix=".tmp", dir=object_path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as destination, source.open("rb") as origin:
            shutil.copyfileobj(origin, destination, length=1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        if _sha(temporary) != digest:
            raise ValueError(f"backup object verification failed: {source}")
        temporary.replace(object_path)
    finally:
        temporary.unlink(missing_ok=True)


def _create_source_backup(run_id: str | None = None) -> dict:
    """Back up every potentially overwritten file before the first stage.

    The manifest contains the original relative path, size, mtime and SHA,
    while the object store deduplicates identical bytes across runs.  No
    backup path is included in the publication allowlist.
    """
    _assert_historical_3mf_contract(phase="before-backup")
    run_id = run_id or uuid.uuid4().hex
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("backup run_id must be a 32-character lowercase hex value")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_relative = BACKUP_ROOT.relative_to(ROOT).as_posix()
    _, backup_root = _repo_file(backup_relative, "backup root")
    if backup_root.is_symlink():
        raise ValueError(f"backup root is a symlink: {backup_root}")
    if backup_root.exists() and not backup_root.is_dir():
        raise ValueError(f"backup root is not a directory: {backup_root}")
    objects_root = backup_root / "objects"
    if objects_root.is_symlink():
        raise ValueError(f"backup object root is a symlink: {objects_root}")
    if objects_root.exists() and not objects_root.is_dir():
        raise ValueError(f"backup object root is not a directory: {objects_root}")
    runs_root = backup_root / "runs"
    if runs_root.is_symlink():
        raise ValueError(f"backup run root is a symlink: {runs_root}")
    if runs_root.exists() and not runs_root.is_dir():
        raise ValueError(f"backup run root is not a directory: {runs_root}")
    run_root = runs_root / f"{stamp}-{os.getpid()}-{run_id}"
    if run_root.exists():
        raise RuntimeError(f"backup run already exists: {run_root}")
    entries = []
    root_records = []
    try:
        for root_relative in BACKUP_MUTABLE_ROOTS:
            relative, root = _repo_file(root_relative, "backup target")
            root_exists = root.exists()
            if root_exists and root.is_symlink():
                raise ValueError(f"backup target is a symlink: {relative}")
            root_records.append({
                "run_id": run_id,
                "path": relative,
                "exists": bool(root_exists),
                "kind": "file" if root.is_file() else ("directory" if root.is_dir() else "missing"),
            })
            for source in _backup_files_under(root, exclude_historical=True):
                source_relative = source.relative_to(ROOT).as_posix()
                digest = _sha(source)
                object_relative = (Path(backup_relative) / "objects" / digest).as_posix()
                _copy_backup_object(source, objects_root / digest, digest)
                object_path = objects_root / digest
                if not object_path.is_file() or _sha(object_path) != digest:
                    raise ValueError(f"backup object is incomplete: {object_relative}")
                entries.append({
                    "run_id": run_id,
                    "original_path": source_relative,
                    "original_exists": True,
                    "size_bytes": source.stat().st_size,
                    "mtime_ns": source.stat().st_mtime_ns,
                    "sha256": digest,
                    "backup_object": object_relative,
                    "backup_exists": True,
                    "backup_size_bytes": object_path.stat().st_size,
                    "backup_sha256": _sha(object_path),
                    "verified": True,
                })
        if not entries and not root_records:
            raise ValueError("backup target set is empty")
        if any(not row["verified"] for row in entries):
            raise ValueError("backup completeness check failed")
        for root_record in root_records:
            root_name = root_record["path"].rstrip("/")
            root_record["original_file_paths"] = [
                row["original_path"] for row in entries
                if row["original_path"] == root_name
                or row["original_path"].startswith(root_name + "/")
            ]
        manifest = {
            "schema_version": 2,
            "run_id": run_id,
            "status": "BACKUP_COMPLETE",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "backup_root": backup_relative,
            "object_store": f"{backup_relative}/objects/<sha256>",
            "control_roots": list(CONTROL_OUTPUT_ROOTS),
            "target_roots": root_records,
            "files": entries,
            "original_file_count": len(entries),
            "backup_file_count": sum(1 for row in entries if row["backup_exists"]),
            "completeness": {
                "status": "PASS",
                "all_original_files_have_objects": all(row["backup_exists"] for row in entries),
                "all_object_hashes_match": all(row["sha256"] == row["backup_sha256"] for row in entries),
                "duplicate_content_reused": len({row["sha256"] for row in entries}) < len(entries),
            },
            "restore_procedure": {
                "status": "RESTORED_UNVERIFIED_REQUIRED",
                "required_post_restore_status": "RESTORED_UNVERIFIED",
                "steps": [
                    "停止した生成実行の incomplete マーカーを確認する",
                    "files[].backup_object を files[].original_path へコピーする（既存ファイルはSHA確認後に置換）",
                    "control_roots（RECORD/marker）も files[] の台帳どおり復元し、旧成功RECORDとmarkerを同居させない",
                    "target_roots[].original_file_paths を基準に、各ディレクトリ対象root内で一覧にない生成ファイルを削除し、単一ファイル対象rootも一覧外なら削除する",
                    "復元後に files[].sha256 と実ファイルのSHA-256を全件照合する",
                    "復元結果の台帳を status=RESTORED_UNVERIFIED として新しいrun_idで記録し、GENERATED/VALIDATEDへ昇格させない",
                    "復元確認後に incomplete マーカーを削除し、必要なら生成を最初から再実行する",
                ],
                "working_directory": "repository root",
                "verification_command": ".venv/bin/python -c 'import hashlib,json,pathlib; m=json.load(open(\"<manifest>\")); assert all(hashlib.sha256(pathlib.Path(r[\"original_path\"]).read_bytes()).hexdigest()==r[\"sha256\"] and hashlib.sha256(pathlib.Path(r[\"backup_object\"]).read_bytes()).hexdigest()==r[\"backup_sha256\"] for r in m[\"files\"])'",
                "new_file_cleanup": "target_roots[].original_file_paths is the allowlist; remove any current file below a target root that is absent from that list",
            },
        }
        run_root.mkdir(parents=True, exist_ok=False)
        manifest_path = run_root / "manifest.json"
        _atomic_json_write(manifest_path, manifest, "backup manifest")
        return {
            "status": manifest["status"],
            "run_id": manifest["run_id"],
            "manifest": manifest_path.relative_to(ROOT).as_posix(),
            "backup_root": manifest["backup_root"],
            "original_file_count": manifest["original_file_count"],
            "backup_file_count": manifest["backup_file_count"],
            "completeness": manifest["completeness"],
            "restore_procedure": manifest["restore_procedure"],
        }
    except Exception:
        # An incomplete run directory is evidence of a failed preflight, not a
        # usable backup.  Keep content-addressed objects for later verification
        # but remove only this run's temporary ledger.
        shutil.rmtree(run_root, ignore_errors=True)
        raise


def _ledger_row(relative: str, path: Path, *, run_id: str | None = None) -> dict:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"ledger path is not a regular file: {relative}")
    row = {
        "path": relative,
        "exists": True,
        "size_bytes": path.stat().st_size,
        "sha256": _sha(path),
    }
    if run_id is not None:
        row["run_id"] = run_id
    return row


def _owned_inventory(root_relative: str, *, require_exists: bool = False,
                     run_id: str | None = None) -> list[dict]:
    """Return a complete regular-file inventory for one owned root."""
    relative, root = _repo_file(root_relative, "owned output root")
    if not root.exists():
        if require_exists:
            raise RuntimeError(f"owned output root is missing: {relative}")
        return []
    if root.is_file():
        return [_ledger_row(relative, root, run_id=run_id)]
    if not root.is_dir():
        raise RuntimeError(f"owned output root is not a file/directory: {relative}")
    rows = []
    stack = [(root, relative)]
    while stack:
        current, current_relative = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda entry: entry.name)
        except OSError as exc:
            raise RuntimeError(f"cannot scan owned output root: {current_relative}") from exc
        for entry in entries:
            child_relative = f"{current_relative}/{entry.name}"
            _repo_relative(child_relative, "owned output path")
            child = Path(entry.path)
            if entry.is_symlink():
                raise RuntimeError(f"owned output contains a symlink: {child_relative}")
            if entry.is_dir(follow_symlinks=False):
                if entry.name.endswith(".incomplete"):
                    raise RuntimeError(
                        f"owned output contains an incomplete path: {child_relative}")
                stack.append((child, child_relative))
            elif entry.is_file(follow_symlinks=False):
                _, checked = _repo_file(child_relative, "owned output path")
                rows.append(_ledger_row(child_relative, checked, run_id=run_id))
            else:
                raise RuntimeError(f"owned output contains a non-file entry: {child_relative}")
    return sorted(rows, key=lambda row: row["path"])


def _owned_inventory_for_roots(roots, *, require_exists=False,
                               run_id: str | None = None) -> list[dict]:
    roots = tuple(roots)
    historical_paths = frozenset()
    if any((root.as_posix() if isinstance(root, Path) else str(root)).rstrip("/")
           == "hardware/stl" for root in roots):
        historical_paths = _assert_historical_3mf_contract(phase="owned-inventory")
    rows = []
    for root in roots:
        rows.extend(_owned_inventory(root, require_exists=require_exists, run_id=run_id))
    by_path = {}
    for row in rows:
        if row["path"] in by_path:
            raise RuntimeError(f"owned output roots overlap: {row['path']}")
        by_path[row["path"]] = row
    return [by_path[path] for path in sorted(by_path)
            if path not in historical_paths]


def _ledger_for_paths(paths, label: str, *, run_id: str | None = None,
                      require_exists: bool = True) -> list[dict]:
    """Expand exact files/directories into a deterministic SHA ledger."""
    rows = {}
    for raw in paths:
        if not isinstance(raw, str):
            raise ValueError(f"{label}: ledger path must be a string")
        relative, path = _repo_file(raw, label)
        if not path.exists():
            if require_exists:
                raise RuntimeError(f"{label}: input is missing: {relative}")
            continue
        if path.is_dir():
            for row in _owned_inventory(relative, require_exists=True, run_id=run_id):
                rows[row["path"]] = row
        else:
            rows[relative] = _ledger_row(relative, path, run_id=run_id)
    if require_exists and not rows:
        raise RuntimeError(f"{label}: ledger is empty")
    return [rows[path] for path in sorted(rows)]


def _verify_ledger(rows, label: str, *, run_id: str | None = None) -> None:
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"{label}: ledger is missing or empty")
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise RuntimeError(f"{label}[{index}]: malformed ledger row")
        relative = _repo_relative(row["path"], f"{label}[{index}].path")
        if relative != row["path"]:
            raise RuntimeError(f"{label}[{index}]: path is not canonical: {row['path']!r}")
        if relative in seen:
            raise RuntimeError(f"{label}: duplicate path: {relative}")
        seen.add(relative)
        if row.get("exists") is not True:
            raise RuntimeError(f"{label}[{index}]: exists must be true")
        if run_id is not None and row.get("run_id") != run_id:
            raise RuntimeError(f"{label}[{index}]: run_id does not match")
        expected = _require_sha(row.get("sha256"), f"{label}[{index}].sha256")
        _, path = _repo_file(relative, f"{label}[{index}]")
        if not path.is_file() or path.stat().st_size != row.get("size_bytes"):
            raise RuntimeError(f"{label}[{index}]: file disappeared or size changed: {relative}")
        actual = _sha(path)
        if actual != expected:
            raise RuntimeError(f"{label}[{index}]: SHA-256 changed: {relative}")


def _manifest_stl_paths(relative: str, label: str) -> list[str]:
    """Collect all STL path fields from an already-generated manifest."""
    _, path = _repo_file(relative, label)
    data = _json(path)
    result = set()

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"path", "stl", "filename", "mesh_path"} and isinstance(child, str):
                    if child.lower().endswith(".stl"):
                        result.add(_repo_relative(child, f"{label}.{key}"))
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)
    return sorted(result)


def _stage_input_paths(label: str) -> list[str]:
    """Declare every file a stage may consume before it starts."""
    paths = sorted(_SOURCE_FREEZE)
    if label == "head_eyecut":
        # This stage shares hardware/stl with legacy_build.  It only appends
        # Head_Top_Eyecut.stl, so the complete legacy output is an input
        # handoff whose bytes must remain unchanged.
        paths.append("hardware/stl")
    if label in {
        "print_first_feet", "check_print_first_feet", "print_first_legs",
        "print_first_body",
    }:
        paths.append("hardware/stl")
    if label == "check_print_first_feet":
        # The checker reloads the assembly manifest and every candidate STL
        # named by it.  It also reloads the legacy shin-shell files, already
        # covered by hardware/stl above.  Do not record the whole output root:
        # verification.json and render PNGs are produced by this same stage
        # and therefore cannot be inputs captured before it starts.
        assembly = "outputs/print-first-20260905/feet/assembly.json"
        paths.append(assembly)
        if (ROOT / assembly).is_file():
            paths.extend(_manifest_stl_paths(assembly, f"{label} input manifest"))
    if label == "print_first_body":
        paths.extend([
            "docs/audits/20260905-round2/xiao-retention-plan.json",
            "docs/audits/20260905-round2/xiao-retention-candidate",
            "outputs/print-first-20260905/feet",
            "outputs/print-first-20260905/legs",
        ])
    if label == "print_first_urdf":
        manifest_paths = [
            "outputs/print-first-20260905/feet/assembly.json",
            "outputs/print-first-20260905/legs/assembly.json",
            "outputs/print-first-20260905/body/assembly.json",
        ]
        # print_first_assembly imports export_urdf/make_visuals for the
        # remaining legacy parts as well as consuming the three candidate
        # manifests.  Freeze the complete regenerated STL tree plus every
        # path explicitly named by those manifests.
        paths.extend(["hardware/stl", *manifest_paths])
        for manifest in manifest_paths:
            paths.extend(_manifest_stl_paths(manifest, f"{label} input manifest"))
        paths.extend([
            "docs/audits/20260905-round2/xiao-retention-plan.json",
            "docs/audits/20260905-round2/xiao-retention-candidate",
        ])
    return sorted(set(paths))


def _stage_output_policy(label: str) -> dict:
    """Return the immutable output/handoff policy for one serial stage."""
    policy = STAGE_OUTPUT_HANDOFFS.get(label)
    if not isinstance(policy, dict):
        raise RuntimeError(f"stage has no output handoff policy: {label}")
    expected_roots = list(STAGE_OUTPUT_ROOTS.get(label, ()))
    if policy.get("roots") != expected_roots:
        raise RuntimeError(f"stage output handoff roots disagree: {label}")
    mode = policy.get("mode")
    if mode not in {"append_only", "sealed"}:
        raise RuntimeError(f"stage output handoff mode is invalid: {label}")
    next_stage = policy.get("next_stage")
    if next_stage is not None and next_stage not in STAGE_OUTPUT_ROOTS:
        raise RuntimeError(f"stage output handoff next stage is invalid: {label}")
    return {
        "roots": expected_roots,
        "mode": mode,
        "next_stage": next_stage,
        "policy": (
            "later stage may append new paths under this root and must consume "
            "all previous paths unchanged; existing paths may not be mutated"
            if mode == "append_only" else
            "no later stage may modify or append under this root"
        ),
    }


def _stage_output_ledger(label: str, run_id: str) -> list[dict]:
    roots = STAGE_OUTPUT_ROOTS.get(label)
    if roots is None:
        raise RuntimeError(f"stage has no owned output roots: {label}")
    rows = _owned_inventory_for_roots(roots, require_exists=True, run_id=run_id)
    _assert_stage_output_contract(label, rows, phase="stage-output-capture")
    return rows


def _urdf_stage_expected_paths() -> set[str]:
    """Derive the complete URDF bundle file set from the emitted URDF.

    The filename grammar is only a shape check.  The final allowlist must be
    the exact mesh reference closure, otherwise an unreferenced debug STL
    could survive below ``meshes/`` and still look like a valid pattern match.
    """
    import xml.etree.ElementTree as ET

    urdf_relative = "hardware/urdf-print-first/tachikoma.urdf"
    _, urdf = _repo_file(urdf_relative, "print-first URDF stage contract")
    if not urdf.is_file():
        raise RuntimeError("print-first URDF is missing before mesh allowlist validation")
    try:
        root = ET.parse(urdf).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RuntimeError("print-first URDF cannot be parsed before mesh allowlist validation") from exc
    refs = []
    for mesh in root.findall(".//mesh"):
        filename = mesh.get("filename")
        if not isinstance(filename, str) or not filename:
            raise RuntimeError("print-first URDF contains a malformed mesh reference")
        ref = PurePosixPath(filename)
        if (ref.is_absolute() or ".." in ref.parts or "." in ref.parts
                or "\\" in filename or ref.as_posix() != filename
                or not filename.startswith("meshes/")):
            raise RuntimeError(
                f"print-first URDF contains an unsafe mesh reference: {filename!r}")
        mesh_relative = (Path("hardware/urdf-print-first") / ref).as_posix()
        _repo_relative(mesh_relative, "print-first URDF mesh output")
        refs.append(mesh_relative)
    if not refs or len(refs) != len(set(refs)):
        raise RuntimeError("print-first URDF mesh reference closure is empty or duplicated")
    return {
        urdf_relative,
        "hardware/urdf-print-first/parts_manifest.json",
        *refs,
    }


def _assert_stage_output_contract(label: str, rows: list[dict], *, phase: str) -> None:
    """Reject any stage file outside its fixed filename contract.

    The check is intentionally independent of the generated manifest.  The
    manifest is a consumer of the stage output and therefore cannot be allowed
    to enlarge its own allowlist by mentioning a debug or stale artifact.
    """
    contract = STAGE_OUTPUT_FILE_CONTRACTS.get(label)
    if not isinstance(contract, dict):
        raise RuntimeError(f"{label}: stage output filename contract is missing ({phase})")
    exact = contract.get("exact")
    patterns = contract.get("patterns")
    if not isinstance(exact, (set, frozenset)) or not isinstance(patterns, tuple):
        raise RuntimeError(f"{label}: stage output filename contract is malformed ({phase})")
    paths = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise RuntimeError(f"{label}: malformed output ledger row {index} ({phase})")
        relative = _repo_relative(row["path"], f"{label}.outputs[{index}]")
        paths.append(relative)
    if len(paths) != len(set(paths)):
        raise RuntimeError(f"{label}: duplicate output path in stage ledger ({phase})")
    actual = set(paths)
    # The URDF writer determines the exact mesh closure.  Keep the static
    # link/color grammar as a sanity check, then replace its broad pattern
    # allowance with the current URDF's referenced paths so an unreferenced
    # debug STL cannot pass.
    required_exact = set(exact)
    if label == "print_first_urdf":
        required_exact = _urdf_stage_expected_paths()
        for path in required_exact - set(exact):
            if path.startswith("hardware/urdf-print-first/meshes/"):
                if not any(pattern.fullmatch(path) for pattern in patterns):
                    raise RuntimeError(
                        f"{label}: URDF mesh reference violates filename grammar: {path}")
    allowed = set(required_exact)
    if label != "print_first_urdf":
        for pattern in patterns:
            allowed.update(path for path in actual if pattern.fullmatch(path))
    invalid = sorted(actual - allowed)
    missing = sorted(required_exact - actual)
    if invalid or missing:
        details = []
        if invalid:
            details.append(f"unexpected={invalid[:16]!r}")
        if missing:
            details.append(f"missing={missing[:16]!r}")
        raise RuntimeError(
            f"{label}: stage output filename contract mismatch ({phase}): "
            + ", ".join(details))


def _assert_stage_output_roots_safe(label: str, *, phase: str) -> None:
    """Check stage output roots before a subprocess can write to them."""
    roots = STAGE_OUTPUT_ROOTS.get(label)
    if roots is None:
        raise RuntimeError(f"stage has no owned output roots: {label}")
    for root_relative in roots:
        relative, path = _repo_file(root_relative, f"{label} output root")
        if not path.exists():
            # _repo_file has already checked every existing parent for
            # symlinks.  A missing leaf is a valid first-run output target.
            continue
        if not (path.is_file() or path.is_dir()):
            raise RuntimeError(
                f"{label} output root is not a regular file/directory ({phase}): {relative}")


def _is_under_owned_output(relative: str) -> bool:
    return any(relative == root or relative.startswith(root.rstrip("/") + "/")
               for root in OWNED_OUTPUT_ROOTS)


def _paths_from_value(value) -> set[str]:
    result = set()
    if isinstance(value, dict):
        for child in value.values():
            result.update(_paths_from_value(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_paths_from_value(child))
    elif isinstance(value, str):
        try:
            relative = _repo_relative(value, "owned output ledger path")
        except ValueError:
            return result
        if _is_under_owned_output(relative):
            result.add(relative)
    return result


def _expected_owned_output_paths(checks: dict, record: dict) -> set[str]:
    expected = _paths_from_value(checks)
    # Every stage ledger is also an explicit registration of the files it
    # created or retained.  This includes preview/check artifacts which are
    # intentionally outside the semantic provenance returned by validators.
    for stage in record.get("stages", []):
        expected.update(
            row["path"] for row in stage.get("outputs", [])
            if isinstance(row, dict) and isinstance(row.get("path"), str)
        )
    # XIAO's nested check records include candidate STL paths which are not
    # necessarily repeated in the compact provenance result.
    plan = "docs/audits/20260905-round2/xiao-retention-plan.json"
    if (ROOT / plan).is_file():
        expected.add(plan)
        expected.update(path for path in _manifest_stl_paths(plan, "XIAO output ledger")
                        if _is_under_owned_output(path))
    expected.add("firmware/src/print_first_gait.h")
    return expected


def _assert_no_owned_incomplete(*, phase: str) -> None:
    found = []
    # The orchestrator marker is the one allowed incomplete control file while
    # a full run is active.  Material roots are always scanned, including
    # nested body/feet/legs/URDF candidates.
    for root in MATERIAL_OUTPUT_ROOTS:
        for row in _owned_inventory(root, require_exists=False):
            if row["path"].endswith(".incomplete"):
                found.append(row["path"])
    if found:
        raise RuntimeError(
            f"owned incomplete markers are not allowed at {phase}: {sorted(found)!r}")


def _assert_owned_outputs_registered(expected_paths: set[str], *, phase: str,
                                     run_id: str | None = None) -> list[dict]:
    _assert_no_owned_incomplete(phase=phase)
    expected_paths = {
        path for path in expected_paths
        if any(path == root or path.startswith(root.rstrip("/") + "/")
               for root in MATERIAL_OUTPUT_ROOTS)
    }
    actual_rows = _owned_inventory_for_roots(
        MATERIAL_OUTPUT_ROOTS, require_exists=False, run_id=run_id)
    actual = {row["path"] for row in actual_rows}
    missing = sorted(set(expected_paths) - actual)
    extra = sorted(actual - set(expected_paths))
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing={missing[:12]!r}")
        if extra:
            details.append(f"extra={extra[:12]!r}")
        raise RuntimeError(f"owned output ledger mismatch at {phase}: " + ", ".join(details))
    return actual_rows


def _quarantine_previous_owned_outputs(run_id: str, backup_report: dict | None = None) -> dict:
    """Move previous outputs only after rechecking the completed backup ledger.

    The initial inventory is not enough: a file can change between backup and
    quarantine.  Every source is therefore compared with the backup object
    SHA again immediately before its atomic move, and the complete expected
    material inventory must be present before the first move starts.
    """
    _assert_historical_3mf_contract(phase="before-quarantine")
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("quarantine run_id must be a 32-character lowercase hex value")
    if not isinstance(backup_report, dict):
        raise RuntimeError("quarantine requires the completed source backup report")
    _assert_backup_ledger({"backup": backup_report}, run_id,
                          phase="before-quarantine")
    backup_manifest_relative = _repo_relative(
        backup_report.get("manifest"), "quarantine backup manifest")
    _, backup_manifest_path = _repo_file(
        backup_manifest_relative, "quarantine backup manifest")
    backup_manifest = _json(backup_manifest_path)
    backup_rows = backup_manifest.get("files")
    if not isinstance(backup_rows, list):
        raise RuntimeError("quarantine backup file ledger is missing")
    backup_sha_by_path = {}
    for row in backup_rows:
        if not isinstance(row, dict):
            raise RuntimeError("quarantine backup file ledger is malformed")
        original = _repo_relative(row.get("original_path"),
                                  "quarantine backup original path")
        digest = _require_sha(row.get("sha256"),
                              "quarantine backup original SHA")
        if original in backup_sha_by_path:
            raise RuntimeError(f"quarantine backup has duplicate path: {original}")
        backup_sha_by_path[original] = digest
    quarantine_root = OUTPUT_ROOT / "quarantine" / run_id
    quarantine_relative = _path_relative(quarantine_root, "quarantine root")
    _, checked_root = _repo_file(quarantine_relative, "quarantine root")
    if checked_root.exists():
        raise RuntimeError(f"quarantine run already exists: {quarantine_relative}")
    entries = []
    inventory_rows = _owned_inventory_for_roots(
        MATERIAL_OUTPUT_ROOTS, require_exists=False, run_id=run_id)
    inventory_by_path = {row["path"]: row for row in inventory_rows}
    expected_material_paths = {
        path for path in backup_sha_by_path
        if any(path == root or path.startswith(root.rstrip("/") + "/")
               for root in MATERIAL_OUTPUT_ROOTS)
    }
    if set(inventory_by_path) != expected_material_paths:
        missing = sorted(expected_material_paths - set(inventory_by_path))
        unexpected = sorted(set(inventory_by_path) - expected_material_paths)
        raise RuntimeError(
            "quarantine source inventory differs from the completed backup: "
            f"missing={missing[:12]!r}, unexpected={unexpected[:12]!r}")
    # The current record and marker are replaced atomically after the backup
    # succeeds; moving them here would move the new run's control files as
    # well.  Their previous bytes are already covered by BACKUP_MUTABLE_ROOTS.
    for row in inventory_rows:
        source = ROOT / row["path"]
        expected_sha = backup_sha_by_path.get(row["path"])
        if expected_sha is None:
            raise RuntimeError(
                f"quarantine source was not present in backup ledger: {row['path']}")
        if row["sha256"] != expected_sha:
            raise RuntimeError(
                f"quarantine source differs from backup inventory: {row['path']}")
        destination = checked_root / row["path"]
        destination_relative = destination.relative_to(ROOT).as_posix()
        _repo_file(destination_relative, "quarantine destination")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            raise RuntimeError(f"quarantine destination already exists: {destination_relative}")
        # Keep this check adjacent to the move.  The earlier inventory and
        # the backup manifest alone do not cover a post-backup edit.
        if (source.is_symlink() or not source.is_file()
                or source.stat().st_size != row["size_bytes"]
                or source.stat().st_size != _repo_file(
                    row["path"], "quarantine source")[1].stat().st_size
                or _sha(source) != expected_sha):
            raise RuntimeError(
                f"quarantine source changed after backup: {row['path']}")
        shutil.move(str(source), str(destination))
        if not destination.is_file() or _sha(destination) != expected_sha:
            raise RuntimeError(f"quarantine verification failed: {row['path']}")
        entries.append({
            "original_path": row["path"],
            "quarantine_path": destination_relative,
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
            "verified": True,
            "run_id": run_id,
        })
    manifest = {
        "schema_version": 1,
        "status": "QUARANTINE_COMPLETE",
        "run_id": run_id,
        "root": quarantine_relative,
        "files": sorted(entries, key=lambda row: row["original_path"]),
        "file_count": len(entries),
        "policy": "historical owned outputs are preserved and excluded from the current run ledger",
    }
    manifest_path = checked_root / "manifest.json"
    _atomic_json_write(manifest_path, manifest, "quarantine manifest")
    return {
        "status": manifest["status"],
        "run_id": run_id,
        "manifest": manifest_path.relative_to(ROOT).as_posix(),
        "file_count": len(entries),
        "backup_manifest": backup_manifest_relative,
    }


def _assert_xiao_plan() -> dict:
    plan_relative = "docs/audits/20260905-round2/xiao-retention-plan.json"
    plan_relative, path = _repo_file(plan_relative, "XIAO plan")
    data = _json(path)
    _assert_nonnegative_mm3(data, "XIAO plan")
    status = _assert_status(data.get("status"), "xiao", "XIAO plan")
    source_records = _assert_source_rows(data.get("source_files"), "XIAO plan.source_files")
    checks = data.get("holder_meshes", {}).get("geometry_checks")
    if (not isinstance(checks, dict)
            or set(checks) != XIAO_EXPECTED_GEOMETRY_CHECK_NAMES
            or len(checks) != len(XIAO_EXPECTED_GEOMETRY_CHECK_NAMES)):
        raise ValueError(
            "XIAO plan: holder geometry checks must contain the exact four checks")
    _assert_nonnegative_mm3(checks, "XIAO plan.holder_meshes.geometry_checks")
    candidate_rows_by_path = {}
    for variant in ("without_sd_candidate", "with_sd_candidate"):
        rows = data["holder_meshes"].get(variant)
        if not isinstance(rows, dict):
            raise ValueError(f"XIAO plan: missing {variant}")
        for mesh_name, row in rows.items():
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                raise ValueError(f"XIAO plan: malformed {variant}/{mesh_name}")
            candidate_relative = _repo_relative(
                row["path"], f"XIAO {variant}/{mesh_name}.path")
            if candidate_relative in candidate_rows_by_path:
                raise ValueError(f"XIAO plan: duplicate candidate path: {candidate_relative}")
            candidate_rows_by_path[candidate_relative] = row
    for name, row in checks.items():
        if not isinstance(row, dict):
            raise ValueError(f"XIAO plan: malformed geometry check {name}")
        check_status = _status_text(row.get("status"))
        if check_status not in {
            "GEOMETRY_CHECK_PASS_CANDIDATE_ONLY",
            "SERIALIZE_ROUNDTRIP_PASS_CANDIDATE_ONLY",
        }:
            raise ValueError(f"XIAO plan: geometry check {name} is not PASS: {check_status!r}")
        for contract_key in ("holder3_union", "board_occupancy_x_holder3"):
            contract = row.get(contract_key)
            if not isinstance(contract, dict) or contract.get("pass") is not True:
                raise ValueError(f"XIAO plan: {name}.{contract_key} is not PASS")
        variant = "with_sd_candidate" if name.startswith("with_sd_") else "without_sd_candidate"
        holder_candidate = data["holder_meshes"][variant]["xiao_tray_holder3_union_candidate"]
        holder_candidate_actual = _assert_stl(
            holder_candidate["path"], f"XIAO {variant}/xiao_tray_holder3_union_candidate",
            expected_sha=holder_candidate.get("sha256"), require_sha=True)
        _assert_geometry_claim(
            row["holder3_union"], holder_candidate_actual,
            f"XIAO {name}.holder3_union")
        if check_status.startswith("SERIALIZE"):
            holder = row["holder3_union"]
            if (holder.get("watertight") is not True
                    or holder.get("winding_consistent") is not True
                    or holder.get("solid_count") != 1):
                raise ValueError(f"XIAO plan: {name}.holder3_union topology is invalid")
            holder_relative = _repo_relative(
                holder.get("path"), f"XIAO {name}.holder3_union.path")
            holder_row = candidate_rows_by_path.get(holder_relative)
            if (not isinstance(holder_row, dict)
                    or holder_row.get("role") != "holder_union"
                    or holder_row.get("sha256") != holder.get("sha256")):
                raise ValueError(f"XIAO plan: {name}.holder3_union path/SHA is not from candidate")
            holder_actual = _assert_stl(
                holder_relative, f"XIAO {name}.holder3_union",
                expected_sha=holder.get("sha256"), require_sha=True)
            _assert_geometry_claim(holder, holder_actual, f"XIAO {name}.holder3_union")
            occupancy = row["board_occupancy_x_holder3"]
            for key in ("board_path", "holder_path"):
                occupancy_relative = _repo_relative(
                    occupancy.get(key), f"XIAO {name}.board_occupancy_x_holder3.{key}")
                occupancy_row = candidate_rows_by_path.get(occupancy_relative)
                if not isinstance(occupancy_row, dict):
                    raise ValueError(
                        f"XIAO plan: {name}.board_occupancy_x_holder3 path is not from candidate")
                if key == "board_path" and occupancy_row.get("role") != "board_occupancy":
                    raise ValueError(f"XIAO plan: {name}.board_path role is not board_occupancy")
                if key == "holder_path" and occupancy_row.get("role") != "holder_union":
                    raise ValueError(f"XIAO plan: {name}.holder_path role is not holder_union")
                occupancy_actual = _assert_stl(
                    occupancy_relative, f"XIAO {name}.board_occupancy_x_holder3.{key}",
                    require_solid=key == "holder_path",
                    expected_sha=occupancy_row.get("sha256"), require_sha=True)
                _assert_geometry_claim(
                    occupancy_row, occupancy_actual,
                    f"XIAO {variant}/{Path(occupancy_relative).name}")
    mesh_records = []
    for variant in ("without_sd_candidate", "with_sd_candidate"):
        rows = data["holder_meshes"].get(variant)
        if not isinstance(rows, dict):
            raise ValueError(f"XIAO plan: missing {variant}")
        for mesh_name, row in rows.items():
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                raise ValueError(f"XIAO plan: malformed {variant}/{mesh_name}")
            if row.get("exists") is not True:
                raise ValueError(f"XIAO plan: {variant}/{mesh_name} exists must be true")
            actual = _assert_stl(
                row["path"], f"XIAO {variant}/{mesh_name}",
                expected_sha=row.get("sha256"), require_sha=True)
            _assert_geometry_claim(row, actual, f"XIAO {variant}/{mesh_name}")
            mesh_records.append({
                "variant": variant,
                "name": mesh_name,
                **actual,
            })
    return {
        "manifest": _file_record(plan_relative, "XIAO plan manifest"),
        "status": status,
        "sources": source_records,
        "parts": mesh_records,
    }


def _expected_feet_check_names(source_paths, output_names) -> set[str]:
    """Return the complete checker contract, independent of its JSON claim."""
    names = {f"入力の鮮度:{path}" for path in source_paths}
    names.update(f"STL:{name}" for name in output_names)
    assembled = (
        "foot", "tibia", "shoe", "spacer_positive_y", "spacer_negative_y",
        "pla_spacer_upper_positive_y", "pla_spacer_upper_negative_y",
    )
    for index, first in enumerate(assembled):
        for second in assembled[index + 1:]:
            names.add(f"実体交差:{first}/{second}")
    names.update({
        "甲の上からの連続挿入", "脛の上からの連続挿入", "φ10プラグの脛挿入",
        "スペーサー外側から挿入:positive", "スペーサー外側から挿入:negative",
        "M3×40の突出2山以上",
        "連続角度域でTPUが先行接地",
        "形状による保持:下向き抜け", "形状による保持:横ずれX",
        "形状による保持:横ずれY",
    })
    fasteners = ("shank", "head", "nut", "shank_upper", "head_upper", "nut_upper")
    names.update(f"ねじ包絡:{fastener}/{part}"
                 for fastener in fasteners for part in assembled)
    for suffix in ("", "_m"):
        variant = suffix or "standard"
        names.update(f"保持した脛殻:{variant}/{part}" for part in assembled)
        names.update(f"脛殻とねじ:{suffix}/{fastener}" for fastener in fasteners)
        names.update({
            f"殻差分の数値残片:{suffix}",
            f"脛殻の膝側を保存:{suffix}",
            f"耳を閉じる経路:{suffix}",
        })
        names.update(
            f"上側スペーサーの殻支持:{suffix}/{part}"
            for part in ("pla_spacer_upper_positive_y", "pla_spacer_upper_negative_y")
        )
        names.update(f"殻を下ろす経路:{suffix}/ear{index}" for index in (0, 1))
    return names


def _assert_feet() -> dict:
    assembly_relative = "outputs/print-first-20260905/feet/assembly.json"
    verification_relative = "outputs/print-first-20260905/feet/verification.json"
    _, assembly_path = _repo_file(assembly_relative, "feet assembly")
    _, verification_path = _repo_file(verification_relative, "feet verification")
    data = _json(assembly_path)
    _assert_nonnegative_mm3(data, "feet assembly")
    status = _assert_status(data.get("status"), "feet", "feet assembly")
    source_records, source_hash_records = _assert_source_manifest(
        data, "feet assembly", "sources")
    declared_source_paths = {row["path"] for row in source_hash_records}
    parts = data.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("feet assembly: parts are missing")
    required = {
        "tpu_shoe", "pla_spacer_positive_y", "pla_spacer_negative_y",
        "pla_spacer_upper_positive_y", "pla_spacer_upper_negative_y",
        "shin_shell_retained", "shin_shell_retained_m",
    }
    names = [row.get("name") for row in parts if isinstance(row, dict)]
    if set(names) != required or len(names) != len(required):
        raise ValueError(f"feet assembly: unexpected part inventory {names!r}")
    part_actual_by_name = {}
    part_actual_by_name = {}
    for row in parts:
        if row.get("name") == "tpu_shoe" and row.get("material") != "TPU":
            raise ValueError("feet assembly: tpu_shoe material is not TPU")
        if row.get("exists") is not True:
            raise ValueError(f"feet/{row.get('name')}: exists must be true")
        source = row.get("source")
        if not isinstance(source, str):
            raise ValueError(f"feet/{row.get('name')}: source is missing")
        source_relative = _repo_relative(source, f"feet/{row.get('name')}.source.path")
        if source_relative not in declared_source_paths:
            raise ValueError(f"feet/{row.get('name')}: source is absent from source manifest")
        _assert_source_record(source, row.get("source_sha256"),
                              f"feet/{row.get('name')}.source")
        actual = _assert_stl(
            row.get("stl"), f"feet/{row.get('name')}",
            expected_sha=row.get("sha256"), require_sha=True)
        _assert_geometry_claim(row, actual, f"feet/{row.get('name')}")
        part_actual_by_name[row.get("name")] = actual
    output_records = []
    output_actual_by_name = {}
    for name, row in data.get("outputs", {}).items():
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError(f"feet output/{name}: malformed output row")
        if row.get("exists") is not True:
            raise ValueError(f"feet output/{name}: exists must be true")
        actual = _assert_stl(
            row["path"], f"feet output/{name}",
            expected_sha=row.get("sha256"), require_sha=True)
        _assert_geometry_claim(row, actual, f"feet output/{name}")
        output_actual_by_name[name] = actual
        output_records.append({
            "name": name,
            **actual,
        })
    if set(data.get("outputs", {})) != FEET_EXPECTED_OUTPUT_NAMES:
        raise ValueError("feet assembly: output inventory differs from the expected set")
    if not output_records:
        raise ValueError("feet assembly: outputs are missing")
    verification = _json(verification_path)
    verification_status = _status_text(verification.get("status"))
    if verification_status not in FEET_VERIFICATION_STATUS_ALLOWLIST:
        raise ValueError(
            f"feet verification: status {verification_status!r} is not explicitly permitted")
    if verification.get("permitted_status_states") != sorted(FEET_VERIFICATION_STATUS_ALLOWLIST):
        raise ValueError("feet verification: permitted status allowlist provenance is invalid")
    checks = verification.get("checks")
    if (not isinstance(checks, list) or not checks
            or any(not isinstance(row, dict) or row.get("pass_geometry") is not True
                   for row in checks)):
        raise ValueError("feet verification: every recorded check must pass_geometry=true")
    if verification.get("failed"):
        raise ValueError("feet verification: failed checks are present")
    source_paths = [row["path"] for row in source_hash_records]
    output_names = sorted(FEET_EXPECTED_OUTPUT_NAMES)
    expected_check_names = _expected_feet_check_names(source_paths, output_names)
    actual_check_names = [row.get("name") for row in checks]
    if (len(actual_check_names) != len(set(actual_check_names))
            or set(actual_check_names) != expected_check_names):
        raise ValueError("feet verification: expected check-name set is incomplete or altered")
    # Bind the checker’s numeric/component claims to the same reloaded STL
    # records above.  A forged ``pass_geometry=true`` row cannot hide a changed
    # volume or component count behind an otherwise valid check-name set.
    checks_by_name = {row["name"]: row for row in checks}
    for name, actual in output_actual_by_name.items():
        check = checks_by_name.get(f"STL:{name}")
        if not isinstance(check, dict):
            raise ValueError(f"feet verification: STL check is missing: {name}")
        components = check.get("components")
        if (isinstance(components, bool) or not isinstance(components, int)
                or components != actual["solid_components"]):
            raise ValueError(f"feet verification: component claim is stale: {name}")
        volume = check.get("volume_mm3")
        if (not isinstance(volume, (int, float)) or isinstance(volume, bool)
                or not math.isfinite(float(volume))
                or not math.isclose(float(volume), actual["signed_volume_mm3"],
                                     rel_tol=0.0, abs_tol=1.0e-3)):
            raise ValueError(f"feet verification: volume claim is stale: {name}")
    recorded_names = verification.get("expected_check_names")
    if (not isinstance(recorded_names, list)
            or set(recorded_names) != expected_check_names
            or len(recorded_names) != len(expected_check_names)):
        raise ValueError("feet verification: expected_check_names provenance is invalid")
    checker_path = "tools/check_print_first_feet.py"
    checker_sha = _SOURCE_FREEZE.get(checker_path)
    if checker_sha is None or verification.get("checker_path") != checker_path:
        raise ValueError("feet verification: checker provenance is missing")
    if verification.get("checker_sha256") != checker_sha:
        raise ValueError("feet verification: checker SHA-256 is stale")
    _file_record(checker_path, "feet verification checker", expected_sha=checker_sha)
    if verification.get("assembly_path") != assembly_relative:
        raise ValueError("feet verification: assembly provenance path is invalid")
    if verification.get("assembly_sha256") != _sha(assembly_path):
        raise ValueError("feet verification: assembly SHA-256 is stale")
    if verification.get("config_path") != "hardware/src/config.py":
        raise ValueError("feet verification: config provenance path is invalid")
    config_sha = _SOURCE_FREEZE.get("hardware/src/config.py")
    if verification.get("config_sha256") != config_sha:
        raise ValueError("feet verification: config SHA-256 is stale")
    recorded_inputs = verification.get("input_sha256")
    if recorded_inputs != {row["path"]: row["sha256"] for row in source_hash_records}:
        raise ValueError("feet verification: input SHA ledger differs from assembly")
    for source_path, expected_sha in recorded_inputs.items():
        _, source_file = _repo_file(source_path, "feet verification input")
        if _sha(source_file) != expected_sha:
            raise ValueError(f"feet verification: input SHA is stale: {source_path}")
    return {
        "manifest": _file_record(assembly_relative, "feet assembly manifest"),
        "verification": _file_record(verification_relative, "feet verification manifest"),
        "status": status,
        "sources": source_records,
        "parts": [part_actual_by_name[row.get("name")] for row in parts],
        "outputs": output_records,
        "verification_status": verification_status,
        "expected_check_names": sorted(expected_check_names),
    }


def _assert_legs() -> dict:
    relative = "outputs/print-first-20260905/legs/assembly.json"
    _, path = _repo_file(relative, "legs assembly")
    data = _json(path)
    _assert_nonnegative_mm3(data, "legs assembly")
    status = _assert_status(data.get("status"), "legs", "legs assembly")
    source_file_records, source_hash_records = _assert_source_manifest(
        data, "legs assembly", "source_files")
    declared_source_paths = {row["path"] for row in source_hash_records}
    parts = data.get("parts")
    if not isinstance(parts, list) or len(parts) != 10:
        raise ValueError("legs assembly: expected ten generated rows")
    structural = [row for row in parts if row.get("role") == "link_structure"]
    caps = [row for row in parts if row.get("role") == "removable_case_cap"]
    if len(structural) != 6 or len(caps) != 4:
        raise ValueError("legs assembly: structural/cap inventory is incomplete")
    part_actual_by_name = {}
    for row in parts:
        if row.get("exists") is not True:
            raise ValueError(f"legs/{row.get('name')}: exists must be true")
        if not isinstance(row.get("source"), str):
            raise ValueError(f"legs/{row.get('name')}: source is missing")
        source_relative = _repo_relative(row.get("source"), f"legs/{row.get('name')}.source.path")
        if source_relative not in declared_source_paths:
            raise ValueError(f"legs/{row.get('name')}: source is absent from source manifest")
        _assert_source_record(row.get("source"), row.get("source_sha256"),
                              f"legs/{row.get('name')}.source")
        actual = _assert_stl(
            row.get("stl", row.get("path")), f"legs/{row.get('name')}",
            expected_sha=row.get("sha256"), require_sha=True)
        _assert_geometry_claim(row, actual, f"legs/{row.get('name')}")
        part_actual_by_name[row.get("name")] = actual
    for row in structural:
        kind = row.get("link_kind")
        if kind == "coxa":
            contract = row.get("yaw_riser_connection", {})
            if contract.get("connection_status") != "PASS":
                raise ValueError(f"legs/{row.get('name')}: yaw connection is not PASS")
        if kind == "tibia":
            contract = row.get("tibia_cap_escape_validation", {})
            if contract.get("status") != "PASS":
                raise ValueError(f"legs/{row.get('name')}: tibia cap sweep is not PASS")
    return {
        "manifest": _file_record(relative, "legs assembly manifest"),
        "status": status,
        "sources": source_hash_records,
        "source_files": source_file_records,
        "parts": [part_actual_by_name[row.get("name")] for row in parts],
    }


def _assert_body() -> dict:
    relative = "outputs/print-first-20260905/body/assembly.json"
    _, path = _repo_file(relative, "body assembly")
    data = _json(path)
    _assert_nonnegative_mm3(data, "body assembly")
    status = _assert_status(data.get("status"), "body", "body assembly")
    source_file_records, source_hash_records = _assert_source_manifest(
        data, "body assembly", "source_files")
    declared_source_paths = {row["path"] for row in source_hash_records}
    parts = data.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("body assembly: parts are missing")
    names = [row.get("name") for row in parts if isinstance(row, dict)]
    if len(names) != len(set(names)):
        raise ValueError("body assembly: duplicate part names")
    required = {
        "pf_head_top_clearanced", "pf_eye_pod_camera_clearanced",
        "pf_camera_carrier",
    }
    if any(names.count(name) != 1 for name in required):
        raise ValueError(f"body assembly: replacement inventory is incomplete: {names!r}")
    forbidden = {
        "Head_Top_Eyecut", "Head_Top_Blue", "eye_pod_camera",
        "camera_carrier", "pf_head_top", "foot_pad", "Leg_Toe_Black_x12",
    }
    if forbidden.intersection(names):
        raise ValueError("body assembly: legacy geometry remains in final rows")
    part_actual_by_name = {}
    for row in parts:
        if row.get("exists") is not True:
            raise ValueError(f"body/{row.get('name')}: exists must be true")
        if not isinstance(row.get("source"), str):
            raise ValueError(f"body/{row.get('name')}: source is missing")
        source_relative = _repo_relative(row.get("source"), f"body/{row.get('name')}.source.path")
        if source_relative not in declared_source_paths:
            raise ValueError(f"body/{row.get('name')}: source is absent from source manifest")
        _assert_source_record(row.get("source"), row.get("source_sha256"),
                              f"body/{row.get('name')}.source")
        actual = _assert_stl(
            row.get("stl"), f"body/{row.get('name')}",
            expected_sha=row.get("sha256"), require_sha=True)
        _assert_geometry_claim(row, actual, f"body/{row.get('name')}")
        part_actual_by_name[row.get("name")] = actual
    for key in ("head_clearance", "eye_pod_camera_clearance",
                "pf_camera_carrier_clearance"):
        if data.get(key, {}).get("status") != "PASS":
            raise ValueError(f"body assembly: {key} is not PASS")
    return {
        "manifest": _file_record(relative, "body assembly manifest"),
        "status": status,
        "sources": source_hash_records,
        "source_files": source_file_records,
        "parts": [part_actual_by_name[row.get("name")] for row in parts],
    }


def _load_frozen_config_module():
    """Load the frozen config source without using an import-cache copy."""
    relative, path = _repo_file("hardware/src/config.py", "profile config source")
    spec = importlib.util.spec_from_file_location(
        f"print_first_config_{_SOURCE_FREEZE.get(relative, 'current')[:12]}", path)
    if spec is None or spec.loader is None:
        raise ValueError("print-first profile source cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _profile_float_values(text: str, name: str, *, array_size: int | None = None) -> list[float]:
    literal = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    if array_size is None:
        match = re.fullmatch(
            rf"\s*constexpr float {re.escape(name)}\s*=\s*({literal})f;\s*",
            next((line for line in text.splitlines() if name in line), ""),
        )
        if match is None:
            raise ValueError(f"print-first profile missing scalar: {name}")
        return [float(match.group(1))]
    match = re.search(
        rf"constexpr float {re.escape(name)}\[{array_size}\]\s*=\s*\{{([^}}]+)\}};",
        text,
    )
    if match is None:
        raise ValueError(f"print-first profile missing array: {name}")
    values = [float(value) for value in re.findall(literal, match.group(1))]
    if len(values) != array_size:
        raise ValueError(f"print-first profile array has wrong length: {name}")
    return values


def _assert_profile_numeric_contract(text: str) -> None:
    """Compare every generated profile constant with the frozen config dict."""
    config = _load_frozen_config_module()
    cfg = getattr(config, "PRINT_FIRST_GAIT", None)
    if not isinstance(cfg, dict):
        raise ValueError("PRINT_FIRST_GAIT is missing from frozen config")
    scalar_expected = {
        "PRINT_FIRST_BODY_H": cfg["body_h"],
        "PRINT_FIRST_STANCE_R": cfg["stance_r"],
        "PRINT_FIRST_STANCE_OFF_X": cfg["stance_off_xy"][0],
        "PRINT_FIRST_STANCE_OFF_Y": cfg["stance_off_xy"][1],
        "PRINT_FIRST_STEP_H": cfg["step_h"],
        "PRINT_FIRST_MAX_STEP": cfg["max_step"],
        "PRINT_FIRST_MAX_TURN_DEG": cfg["max_turn_deg"],
        "PRINT_FIRST_CYCLE_T": cfg["cycle_t"],
        "PRINT_FIRST_DUTY": cfg["duty"],
        "PRINT_FIRST_SWAY_LEAD": cfg["sway_lead"],
        "PRINT_FIRST_ARM_SWING_DEG": cfg["arm_swing_deg"],
        "PRINT_FIRST_HIP_R": cfg["hip_r"],
    }
    array_expected = {
        "PRINT_FIRST_SWAY_MM": list(cfg["sway_mm"]),
        "PRINT_FIRST_PHASE_OFF": list(cfg["phase_off"]),
    }
    expected_declared = set(scalar_expected) | set(array_expected) | {
        "PRINT_FIRST_LEG_ORIGIN",
    }
    # Match every numeric constexpr declaration, rather than only float lines.
    # This rejects a newly introduced integer/double profile constant which
    # would otherwise evade the complete numeric contract below.
    numeric_declared = set(re.findall(
        r"^\s*constexpr\s+(?:float|double|long double|"
        r"(?:unsigned\s+)?(?:char|short|int|long|long long))\s+"
        r"(PRINT_FIRST_[A-Z0-9_]+)", text, re.MULTILINE))
    if numeric_declared != expected_declared:
        raise ValueError(
            f"print-first profile numeric declaration set changed: "
            f"expected={sorted(expected_declared)!r}, got={sorted(numeric_declared)!r}")
    for name, expected in scalar_expected.items():
        actual = _profile_float_values(text, name)[0]
        if not math.isclose(actual, float(expected), rel_tol=0.0, abs_tol=5e-7):
            raise ValueError(f"print-first profile constant differs from config: {name}")
    for name, expected_values in array_expected.items():
        actual_values = _profile_float_values(text, name, array_size=4)
        if any(not math.isclose(actual, float(expected), rel_tol=0.0, abs_tol=5e-7)
               for actual, expected in zip(actual_values, expected_values)):
            raise ValueError(f"print-first profile array differs from config: {name}")
    origin_match = re.search(
        r"constexpr float PRINT_FIRST_LEG_ORIGIN\[4\]\[2\]\s*=\s*\{(.*?)\n\};",
        text,
        re.DOTALL,
    )
    if origin_match is None:
        raise ValueError("print-first profile origin array is missing")
    literal = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    actual_origins = [float(value) for value in re.findall(literal, origin_match.group(1))]
    legs = ("FR", "FL", "RL", "RR")
    angles = getattr(config, "LEG_ANGLES", None)
    if not isinstance(angles, dict) or len(angles) != 4:
        raise ValueError("LEG_ANGLES is missing from frozen config")
    # The header stores x/y pairs row by row, so flatten the expected matrix in
    # that order instead of accepting a transposed or truncated array.
    expected_origin_values = []
    for leg in legs:
        angle = math.radians(float(angles[leg]))
        expected_origin_values.extend((float(cfg["hip_r"]) * math.cos(angle),
                                       float(cfg["hip_r"]) * math.sin(angle)))
    if len(actual_origins) != 8 or any(
        not math.isclose(actual, expected, rel_tol=0.0, abs_tol=5e-7)
        for actual, expected in zip(actual_origins, expected_origin_values)
    ):
        raise ValueError("print-first profile origin array differs from config")
    bool_match = re.search(
        r"constexpr bool PRINT_FIRST_PROFILE_ADOPTED\s*=\s*(true|false);", text)
    status_match = re.search(
        r'constexpr const char\* PRINT_FIRST_PROFILE_STATUS\s*=\s*"([^"]+)";', text)
    if bool_match is None or status_match is None:
        raise ValueError("print-first profile adoption/status declarations are missing")
    if (bool_match.group(1) == "true") != bool(cfg.get("adopted")):
        raise ValueError("print-first profile adopted value differs from config")
    if status_match.group(1) != cfg.get("status"):
        raise ValueError("print-first profile status differs from config")


def _assert_profile() -> dict:
    relative = "firmware/src/print_first_gait.h"
    _, path = _repo_file(relative, "print-first profile header")
    if not path.is_file():
        raise ValueError("print-first profile header is missing")
    text = path.read_text(encoding="utf-8")
    source_relative, source = _repo_file("hardware/src/config.py", "print-first profile source")
    expected = _SOURCE_FREEZE.get(source_relative)
    if expected is None:
        raise ValueError("print-first profile source is outside the run freeze set")
    if f"source: hardware/src/config.py#PRINT_FIRST_GAIT sha256={expected}" not in text:
        raise ValueError("print-first profile header is stale")
    status_match = re.search(
        r'constexpr const char\* PRINT_FIRST_PROFILE_STATUS = "([^"]+)";', text)
    if status_match is None:
        raise ValueError("print-first profile header lacks an explicit status")
    status = _assert_status(status_match.group(1), "profile", "print-first profile")
    if "TACHIKOMA_PRINT_FIRST_PROFILE=1" not in text:
        raise ValueError("print-first profile header lacks profile contract")
    _assert_profile_numeric_contract(text)
    return {
        "manifest": _file_record(relative, "print-first profile header"),
        "status": status,
        "sources": [_file_record(source_relative, "print-first profile source",
                                   expected_sha=expected)],
    }


def _assert_urdf() -> dict:
    directory = ROOT / "hardware/urdf-print-first"
    urdf_relative = "hardware/urdf-print-first/tachikoma.urdf"
    manifest_relative = "hardware/urdf-print-first/parts_manifest.json"
    _, urdf = _repo_file(urdf_relative, "print-first URDF")
    _, manifest = _repo_file(manifest_relative, "print-first parts manifest")
    if not urdf.is_file() or urdf.stat().st_size == 0 or not manifest.is_file():
        raise ValueError("print-first URDF bundle is incomplete")
    manifest_data = _json(manifest)
    _assert_nonnegative_mm3(manifest_data, "print-first parts manifest")
    status = _assert_status(manifest_data.get("status"), "urdf",
                            "print-first parts manifest")
    links = manifest_data.get("links")
    total_parts = manifest_data.get("total_parts")
    if (not isinstance(links, dict) or not isinstance(total_parts, int)
            or total_parts != sum(len(items) for items in links.values()
                                  if isinstance(items, list))):
        raise ValueError("print-first parts manifest has an invalid part count")
    source_records, _ = _assert_source_manifest(
        manifest_data, "print-first parts manifest", "source_files")
    # export_urdf already performs the strict round-trip checks.  Re-read the
    # complete referenced mesh closure here so the sequence cannot report
    # success when a single emitted STL is missing or malformed.
    import xml.etree.ElementTree as ET
    root = ET.parse(urdf).getroot()
    refs = sorted({row.get("filename") for row in root.findall(".//mesh")
                   if row.get("filename")})
    if not refs:
        raise ValueError("print-first URDF has no referenced mesh")
    for ref in refs:
        ref_posix = PurePosixPath(ref)
        if ("\\" in ref or ref.startswith("~") or ref.startswith("$OUTPUT")
                or ref_posix.is_absolute() or ".." in ref_posix.parts
                or "." in ref_posix.parts or ref_posix.as_posix() != ref):
            raise ValueError(f"print-first URDF has an invalid mesh reference: {ref!r}")
    if manifest_data.get("mesh_reference_count") != len(refs):
        raise ValueError("print-first parts manifest mesh reference count disagrees with URDF")
    visual_refs = [ref for ref in refs if "__col_" not in ref]
    collision_refs = [ref for ref in refs if "__col_" in ref]
    if (manifest_data.get("visual_mesh_count") != len(visual_refs)
            or manifest_data.get("collision_mesh_count") != len(collision_refs)):
        raise ValueError("print-first parts manifest visual/collision reference counts disagree")
    mesh_rows = manifest_data.get("mesh_files")
    if not isinstance(mesh_rows, list) or len(mesh_rows) != len(refs):
        raise ValueError("print-first parts manifest mesh_files is incomplete")
    mesh_rows_by_path = {}
    for row in mesh_rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("print-first parts manifest has a malformed mesh row")
        mesh_path_relative = _repo_relative(
            row["path"], "print-first parts manifest mesh path")
        if mesh_path_relative in mesh_rows_by_path:
            raise ValueError(f"print-first parts manifest has duplicate mesh path: {mesh_path_relative}")
        if row.get("exists") is not True:
            raise ValueError(f"print-first parts manifest mesh is missing: {mesh_path_relative}")
        expected_kind = "collision" if "__col_" in Path(mesh_path_relative).name else "visual"
        if row.get("kind") != expected_kind:
            raise ValueError(f"print-first parts manifest mesh kind disagrees: {mesh_path_relative}")
        mesh_rows_by_path[mesh_path_relative] = row
    expected_mesh_paths = {
        (Path("hardware/urdf-print-first") / Path(ref)).as_posix() for ref in refs
    }
    if set(mesh_rows_by_path) != expected_mesh_paths:
        raise ValueError("print-first parts manifest mesh_files disagree with URDF references")
    mesh_records = []
    for ref in refs:
        ref_path = Path(ref)
        if ref_path.is_absolute() or ".." in ref_path.parts:
            raise ValueError(f"print-first URDF mesh escapes bundle: {ref}")
        mesh_relative = (Path("hardware/urdf-print-first") / ref_path).as_posix()
        row = mesh_rows_by_path[mesh_relative]
        actual = _assert_stl(
            mesh_relative, f"URDF/{ref}", require_solid="__col_" in ref,
            expected_sha=row.get("sha256"), require_sha=True)
        _assert_geometry_claim(row, actual, f"URDF/{ref}")
        mesh_records.append(actual)
    if any(not (directory / ref).is_file() for ref in refs):
        raise ValueError("print-first URDF has missing referenced mesh")
    return {
        "manifest": _file_record(manifest_relative, "print-first parts manifest"),
        "output": _file_record(urdf_relative, "print-first URDF"),
        "status": status,
        "sources": source_records,
        "parts": mesh_records,
    }


def _control_relative(path: Path, label: str) -> str:
    """Return a canonical repository path for a control ledger target."""
    return _path_relative(path, label)


def _read_control_json(path: Path, label: str) -> dict:
    relative = _control_relative(path, label)
    _, checked = _repo_file(relative, label)
    if not checked.is_file():
        raise RuntimeError(f"{label}: file is missing: {relative}")
    value = _json(checked)
    if not isinstance(value, dict):
        raise RuntimeError(f"{label}: a JSON object is required")
    return value


def _assert_run_id(value, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise RuntimeError(f"{label}: a 32-character lowercase hexadecimal run_id is required")
    return value


def _marker_present() -> bool:
    relative = _control_relative(MARKER, "incomplete marker")
    _, marker = _repo_file(relative, "incomplete marker")
    return marker.exists()


def _record_present() -> bool:
    relative = _control_relative(RECORD, "generation record")
    _, record = _repo_file(relative, "generation record")
    return record.exists()


def _load_generation_record() -> dict:
    return _read_control_json(RECORD, "generation record")


def _record_digest_target() -> Path:
    """Return the sidecar digest path for the current record target."""
    default_record = OUTPUT_ROOT / "print-first-generation.json"
    if Path(RECORD) == default_record:
        return RECORD_DIGEST
    # Tests and recovery callers may redirect RECORD to an isolated checkout
    # root.  Keep the sidecar beside that record instead of touching the live
    # output root.
    return Path(RECORD).with_name("print-first-generation.digest.json")


def _record_digest_path_relative() -> str:
    return _control_relative(_record_digest_target(), "generation record digest")


def _write_record_digest(payload: dict) -> None:
    """Write a non-self-referential digest for the bytes of ``RECORD``."""
    record_relative = _control_relative(RECORD, "generation record")
    _, record_path = _repo_file(record_relative, "generation record")
    if not record_path.is_file():
        raise RuntimeError("generation record digest cannot precede its record")
    run_id = _assert_run_id(payload.get("run_id"), "generation record digest.run_id")
    target = _record_digest_target()
    digest_relative = _control_relative(target, "generation record digest")
    _atomic_json_write(target, {
        "schema_version": 1,
        "record_path": record_relative,
        "record_sha256": _sha(record_path),
        "record_size_bytes": record_path.stat().st_size,
        "run_id": run_id,
        "record_status": payload.get("status"),
        "algorithm": "sha256(record file bytes)",
        "self_referential": False,
    }, "generation record digest")


def _assert_record_digest(record: dict, run_id: str, *, phase: str) -> None:
    """Require the sidecar digest to match the exact on-disk record bytes."""
    record_relative = _control_relative(RECORD, "generation record")
    _, record_path = _repo_file(record_relative, "generation record")
    if not record_path.is_file():
        raise RuntimeError(f"generation record is missing ({phase})")
    digest_relative = _record_digest_path_relative()
    _, digest_path = _repo_file(digest_relative, "generation record digest")
    if not digest_path.is_file():
        raise RuntimeError(f"generation record digest is missing ({phase})")
    digest = _read_control_json(digest_path, "generation record digest")
    if (digest.get("schema_version") != 1
            or digest.get("record_path") != record_relative
            or digest.get("algorithm") != "sha256(record file bytes)"
            or digest.get("self_referential") is not False):
        raise RuntimeError(f"generation record digest contract is invalid ({phase})")
    record_digest = record.get("record_digest")
    if (not isinstance(record_digest, dict)
            or record_digest.get("path") != digest_relative
            or record_digest.get("algorithm") != "sha256(record file bytes)"
            or record_digest.get("self_referential") is not False):
        raise RuntimeError(f"generation record digest metadata is missing or stale ({phase})")
    expected_run_id = _assert_run_id(run_id, "generation record digest run_id")
    if (digest.get("run_id") != expected_run_id
            or digest.get("record_status") != record.get("status")):
        raise RuntimeError(f"generation record digest state is stale ({phase})")
    expected_sha = _require_sha(digest.get("record_sha256"),
                                "generation record digest.record_sha256")
    expected_size = digest.get("record_size_bytes")
    if (isinstance(expected_size, bool) or not isinstance(expected_size, int)
            or expected_size < 0 or expected_size != record_path.stat().st_size):
        raise RuntimeError(f"generation record digest size is stale ({phase})")
    actual_sha = _sha(record_path)
    if actual_sha != expected_sha:
        raise RuntimeError(f"generation record digest does not match record bytes ({phase})")


def _assert_record_matches_disk(record: dict, *, phase: str) -> None:
    """Prevent a caller from validating an in-memory record different from disk."""
    disk = _load_generation_record()
    if disk != record:
        raise RuntimeError(f"supplied generation record differs from on-disk record ({phase})")


def _assert_record_checks(record: dict, checks: dict, *, phase: str) -> None:
    """Require recomputed semantic checks to equal the recorded checks exactly."""
    recorded = record.get("checks")
    if not isinstance(recorded, dict):
        raise RuntimeError(f"generation record checks are missing ({phase})")
    if recorded != checks:
        raise RuntimeError(f"generation record checks differ from recomputation ({phase})")


def _assert_backup_ledger(record: dict, run_id: str, *, phase: str) -> None:
    backup = record.get("backup")
    backup_completeness = backup.get("completeness") if isinstance(backup, dict) else None
    if (not isinstance(backup, dict)
            or backup.get("status") != "BACKUP_COMPLETE"
            or backup.get("run_id") != run_id
            or not isinstance(backup_completeness, dict)
            or backup_completeness.get("status") != "PASS"):
        raise RuntimeError(f"generation backup ledger is missing or stale ({phase})")
    manifest_path = _repo_relative(backup.get("manifest"), "backup manifest path")
    backup_root_relative = _path_relative(BACKUP_ROOT, "backup root")
    if not manifest_path.startswith(backup_root_relative.rstrip("/") + "/"):
        raise RuntimeError(f"backup manifest is outside its backup root ({phase})")
    _, manifest_file = _repo_file(manifest_path, "backup manifest")
    if not manifest_file.is_file():
        raise RuntimeError(f"backup manifest is missing ({phase})")
    manifest = _json(manifest_file)
    manifest_completeness = manifest.get("completeness")
    if (manifest.get("status") != "BACKUP_COMPLETE"
            or manifest.get("run_id") != run_id
            or not isinstance(manifest_completeness, dict)
            or manifest_completeness.get("status") != "PASS"):
        raise RuntimeError(f"backup manifest state is invalid ({phase})")
    if (manifest.get("backup_root") != backup_root_relative
            or manifest.get("object_store")
            != f"{backup_root_relative}/objects/<sha256>"):
        raise RuntimeError(f"backup manifest object store is invalid ({phase})")
    if manifest.get("control_roots") != list(CONTROL_OUTPUT_ROOTS):
        raise RuntimeError(f"backup manifest control roots are incomplete ({phase})")
    files = manifest.get("files")
    target_roots = manifest.get("target_roots")
    if (not isinstance(target_roots, list)
            or len(target_roots) != len(BACKUP_MUTABLE_ROOTS)):
        raise RuntimeError(f"backup manifest target roots are incomplete ({phase})")
    expected_root_paths = set(BACKUP_MUTABLE_ROOTS)
    root_rows_by_path = {}
    for index, root_row in enumerate(target_roots):
        if (not isinstance(root_row, dict)
                or root_row.get("run_id") != run_id
                or not isinstance(root_row.get("original_file_paths"), list)):
            raise RuntimeError(f"backup manifest target root[{index}] is malformed ({phase})")
        root_relative = root_row.get("path")
        if (not isinstance(root_relative, str)
                or root_relative in root_rows_by_path
                or root_relative not in expected_root_paths):
            raise RuntimeError(f"backup manifest target root[{index}] path is invalid ({phase})")
        _repo_relative(root_relative, "backup target root path")
        if (not isinstance(root_row.get("exists"), bool)
                or root_row.get("kind") not in {"file", "directory", "missing"}):
            raise RuntimeError(f"backup manifest target root[{index}] metadata is invalid ({phase})")
        root_rows_by_path[root_relative] = root_row
        listed_paths = root_row["original_file_paths"]
        if (any(not isinstance(path, str) for path in listed_paths)
                or listed_paths != sorted(set(listed_paths))):
            raise RuntimeError(
                f"backup manifest target root[{index}] file inventory is invalid ({phase})")
    if set(root_rows_by_path) != expected_root_paths:
        raise RuntimeError(f"backup manifest target roots are incomplete ({phase})")
    completeness = manifest.get("completeness")
    if (not isinstance(completeness, dict)
            or completeness.get("all_original_files_have_objects") is not True
            or completeness.get("all_object_hashes_match") is not True
            or manifest.get("backup_file_count") != manifest.get("original_file_count")):
        raise RuntimeError(f"backup manifest completeness is invalid ({phase})")
    if (not isinstance(files, list)
            or not isinstance(manifest.get("original_file_count"), int)
            or manifest.get("original_file_count") != len(files)
            or not isinstance(manifest.get("backup_file_count"), int)
            or manifest.get("backup_file_count") != len(files)):
        raise RuntimeError(f"backup manifest file ledger is incomplete ({phase})")
    file_paths = set()
    for index, row in enumerate(files):
        if not isinstance(row, dict):
            raise RuntimeError(f"backup manifest file[{index}] is malformed ({phase})")
        if row.get("run_id") != run_id:
            raise RuntimeError(f"backup manifest file[{index}] run_id is stale ({phase})")
        original = _repo_relative(row.get("original_path"), "backup original path")
        object_relative = _repo_relative(row.get("backup_object"), "backup object path")
        digest = _require_sha(row.get("sha256"), "backup original SHA")
        object_digest = _require_sha(row.get("backup_sha256"), "backup object SHA")
        if (original in file_paths
                or not isinstance(row.get("size_bytes"), int)
                or row["size_bytes"] < 0
                or not isinstance(row.get("mtime_ns"), int)
                or row["mtime_ns"] < 0
                or not isinstance(row.get("backup_size_bytes"), int)
                or row["backup_size_bytes"] < 0):
            raise RuntimeError(f"backup manifest file[{index}] metadata is invalid ({phase})")
        file_paths.add(original)
        if (row.get("original_exists") is not True
                or row.get("backup_exists") is not True
                or row.get("verified") is not True
                or digest != object_digest):
            raise RuntimeError(f"backup manifest file[{index}] is not verified ({phase})")
        if not any(original == root or original.startswith(root.rstrip("/") + "/")
                   for root in BACKUP_MUTABLE_ROOTS):
            raise RuntimeError(f"backup original path is outside target roots: {original}")
        expected_object_relative = (
            Path(backup_root_relative) / "objects" / digest).as_posix()
        if object_relative != expected_object_relative:
            raise RuntimeError(f"backup object path is outside the object store: {object_relative}")
        _, object_file = _repo_file(object_relative, "backup object")
        if (not object_file.is_file() or object_file.stat().st_size != row.get("backup_size_bytes")
                or _sha(object_file) != object_digest):
            raise RuntimeError(f"backup object is missing or changed: {object_relative}")
        if row["size_bytes"] != row["backup_size_bytes"]:
            raise RuntimeError(f"backup manifest file[{index}] size differs from object ({phase})")
        # The original path is validated even though a successful run may
        # have moved it into quarantine after the backup was taken.
        if not original:
            raise RuntimeError(f"backup original path is empty ({phase})")
    for root_relative, root_row in root_rows_by_path.items():
        expected_originals = sorted(
            path for path in file_paths
            if path == root_relative or path.startswith(root_relative.rstrip("/") + "/")
        )
        if root_row["original_file_paths"] != expected_originals:
            raise RuntimeError(
                f"backup manifest target root inventory differs: {root_relative} ({phase})")
    restore = manifest.get("restore_procedure")
    if (not isinstance(restore, dict)
            or restore.get("status") != "RESTORED_UNVERIFIED_REQUIRED"
            or restore.get("required_post_restore_status") != "RESTORED_UNVERIFIED"):
        raise RuntimeError(f"backup restore procedure is not fail-closed ({phase})")


def _restore_object_to_path(object_path: Path, target_path: Path, expected_sha: str) -> None:
    """Restore one content-addressed object atomically and verify its bytes."""
    expected_sha = _require_sha(expected_sha, "restore object SHA")
    if object_path.is_symlink() or not object_path.is_file():
        raise RuntimeError(f"restore object is missing or not a regular file: {object_path}")
    if _sha(object_path) != expected_sha:
        raise RuntimeError(f"restore object SHA is stale: {object_path}")
    target_relative = _path_relative(target_path, "restore target")
    _, checked_target = _repo_file(target_relative, "restore target")
    if checked_target.is_symlink():
        raise RuntimeError(f"restore target is a symlink: {target_relative}")
    checked_target.parent.mkdir(parents=True, exist_ok=True)
    _repo_file(target_relative, "restore target")
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{checked_target.name}.restore.", suffix=".tmp",
        dir=checked_target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as destination, object_path.open("rb") as origin:
            shutil.copyfileobj(origin, destination, length=1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        if _sha(temporary) != expected_sha:
            raise RuntimeError(f"restored bytes failed SHA verification: {target_relative}")
        temporary.replace(checked_target)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_control_paths() -> frozenset[str]:
    """Return and validate the three control paths used by restoration.

    The backup manifest deliberately includes the old control objects so their
    bytes remain auditable.  Restoration never copies those objects over the
    active record, digest, or marker; it verifies them and writes a new,
    explicitly non-consumable control state instead.
    """
    configured = tuple(_repo_relative(path, "restore control root")
                       for path in CONTROL_OUTPUT_ROOTS)
    if len(configured) != 3 or len(set(configured)) != 3:
        raise RuntimeError("restore control roots must contain exactly three unique paths")
    expected = frozenset({
        _control_relative(RECORD, "generation record"),
        _record_digest_path_relative(),
        _control_relative(MARKER, "incomplete marker"),
    })
    if frozenset(configured) != expected:
        raise RuntimeError(
            "restore control roots do not match RECORD/digest/marker targets")
    if not expected.issubset(set(BACKUP_MUTABLE_ROOTS)):
        raise RuntimeError("restore control roots are absent from backup targets")
    return expected


def _verify_restore_backup_object(row: dict, label: str) -> str:
    """Verify one manifest object without changing its original target."""
    original_sha = _require_sha(row.get("sha256"), f"{label}.sha256")
    backup_sha = _require_sha(row.get("backup_sha256"), f"{label}.backup_sha256")
    if original_sha != backup_sha:
        raise RuntimeError(f"{label}: original and backup SHA differ")
    object_relative = _repo_relative(row.get("backup_object"),
                                    f"{label}.backup_object")
    _, object_path = _repo_file(object_relative, f"{label}.backup_object")
    expected_size = row.get("backup_size_bytes")
    if (not isinstance(expected_size, int) or isinstance(expected_size, bool)
            or expected_size < 0 or not object_path.is_file()
            or object_path.is_symlink() or object_path.stat().st_size != expected_size
            or _sha(object_path) != backup_sha):
        raise RuntimeError(f"{label}: backup object is missing or changed")
    return original_sha


def _restore_manifest_paths(manifest: dict, manifest_relative: str) -> tuple[dict[str, dict], dict[str, dict]]:
    """Validate a backup manifest and index its original paths."""
    run_id = _assert_run_id(manifest.get("run_id"), "backup manifest.run_id")
    report = {
        "status": manifest.get("status"),
        "run_id": run_id,
        "manifest": manifest_relative,
        "completeness": manifest.get("completeness"),
    }
    _assert_backup_ledger({"backup": report}, run_id, phase="restore-preflight")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise RuntimeError("restore backup manifest files are missing")
    by_path: dict[str, dict] = {}
    for row in files:
        if not isinstance(row, dict):
            raise RuntimeError("restore backup manifest file row is malformed")
        original = _repo_relative(row.get("original_path"), "restore original path")
        if original in by_path:
            raise RuntimeError(f"restore backup manifest has duplicate path: {original}")
        by_path[original] = row
    roots = manifest.get("target_roots")
    if not isinstance(roots, list):
        raise RuntimeError("restore backup manifest target roots are missing")
    root_by_path: dict[str, dict] = {}
    for row in roots:
        if not isinstance(row, dict):
            raise RuntimeError("restore backup manifest target root is malformed")
        root = _repo_relative(row.get("path"), "restore target root")
        if root in root_by_path:
            raise RuntimeError(f"restore backup manifest has duplicate target root: {root}")
        root_by_path[root] = row
    if set(root_by_path) != set(BACKUP_MUTABLE_ROOTS):
        raise RuntimeError("restore backup manifest target roots are incomplete")
    return by_path, root_by_path


def _assert_restore_ledger(ledger_path: str | Path, *, expected_status: str = "RESTORED_UNVERIFIED") -> dict:
    """Verify a restoration ledger and keep it permanently non-consumable."""
    relative = _repo_relative(
        ledger_path if isinstance(ledger_path, str) else _path_relative(
            Path(ledger_path), "restore ledger"),
        "restore ledger")
    _, path = _repo_file(relative, "restore ledger")
    if not path.is_file():
        raise RuntimeError(f"restore ledger is missing: {relative}")
    data = _json(path)
    if (data.get("schema_version") != 1
            or data.get("status") != expected_status
            or data.get("valid_for_consumption") is not False
            or data.get("success_promotion_forbidden") is not True
            or data.get("requires_full_revalidation") is not True):
        raise RuntimeError("restore ledger is not a fail-closed RESTORED_UNVERIFIED record")
    _assert_run_id(data.get("restore_id"), "restore ledger.restore_id")
    manifest_relative = _repo_relative(
        data.get("backup_manifest"), "restore ledger.backup_manifest")
    _, manifest_path = _repo_file(manifest_relative, "restore ledger backup manifest")
    if not manifest_path.is_file():
        raise RuntimeError("restore ledger backup manifest is missing")
    manifest = _json(manifest_path)
    by_path, _ = _restore_manifest_paths(manifest, manifest_relative)
    control_paths = _restore_control_paths()
    backup_paths = set(by_path)
    expected_control_paths = backup_paths & control_paths
    expected_material_paths = backup_paths - control_paths
    restored = data.get("files")
    if not isinstance(restored, list):
        raise RuntimeError("restore ledger files are missing")
    restored_by_path = {}
    for row in restored:
        if not isinstance(row, dict):
            raise RuntimeError("restore ledger file row is malformed")
        original = _repo_relative(row.get("original_path"), "restore ledger original path")
        if original in restored_by_path:
            raise RuntimeError(f"restore ledger has duplicate path: {original}")
        source = by_path.get(original)
        if source is None:
            raise RuntimeError(f"restore ledger path is absent from backup: {original}")
        if original in control_paths:
            raise RuntimeError(f"restore ledger control path is not a material restore: {original}")
        if row.get("classification") != "non_control_restored":
            raise RuntimeError(f"restore ledger material classification is invalid: {original}")
        _verify_restore_backup_object(source, "backup material object")
        digest = _require_sha(row.get("sha256"), "restore ledger SHA")
        if digest != _require_sha(source.get("sha256"), "backup original SHA"):
            raise RuntimeError(f"restore ledger SHA differs from backup: {original}")
        _, target = _repo_file(original, "restore ledger restored file")
        if (not target.is_file() or target.is_symlink()
                or target.stat().st_size != row.get("size_bytes")
                or _sha(target) != digest):
            raise RuntimeError(f"restored file is missing or changed: {original}")
        restored_by_path[original] = row
    if set(restored_by_path) != expected_material_paths:
        raise RuntimeError(
            "restore ledger does not cover the complete backup: "
            f"missing={sorted(expected_material_paths - set(restored_by_path))[:12]!r}")
    control_rows = data.get("control_files")
    if not isinstance(control_rows, list):
        raise RuntimeError("restore ledger control files are missing")
    control_by_path = {}
    for row in control_rows:
        if not isinstance(row, dict):
            raise RuntimeError("restore ledger control row is malformed")
        original = _repo_relative(row.get("original_path"),
                                  "restore ledger control original path")
        if original in control_by_path:
            raise RuntimeError(f"restore ledger control path is duplicated: {original}")
        if original not in expected_control_paths:
            raise RuntimeError(f"restore ledger control path is not in backup: {original}")
        if row.get("classification") != "control_replaced":
            raise RuntimeError(f"restore ledger control classification is invalid: {original}")
        source = by_path[original]
        digest = _verify_restore_backup_object(source, "backup control object")
        if (row.get("sha256") != digest
                or row.get("backup_sha256") != source.get("backup_sha256")
                or row.get("backup_object") != source.get("backup_object")
                or row.get("size_bytes") != source.get("size_bytes")
                or row.get("verified") is not True
                or row.get("replaced_by_restored_unverified") is not True):
            raise RuntimeError(f"restore ledger control object record is stale: {original}")
        control_by_path[original] = row
    if set(control_by_path) != expected_control_paths:
        raise RuntimeError(
            "restore ledger control classification is incomplete: "
            f"missing={sorted(expected_control_paths - set(control_by_path))[:12]!r}")
    if (set(restored_by_path) & set(control_by_path)
            or set(restored_by_path) | set(control_by_path) != backup_paths
            or data.get("file_count") != len(restored_by_path)
            or data.get("control_file_count") != len(control_by_path)
            or data.get("file_count", 0) + data.get("control_file_count", 0) != len(backup_paths)):
        raise RuntimeError("restore ledger backup paths are not uniquely classified")

    # The three active control files are intentionally new bytes.  They must
    # agree with one another and with the record sidecar before the restore can
    # be considered an auditable RESTORED_UNVERIFIED state.
    record = _read_control_json(RECORD, "restored generation record")
    marker = _read_control_json(MARKER, "restored incomplete marker")
    digest = _read_control_json(_record_digest_target(), "restored generation digest")
    if record != marker:
        raise RuntimeError("restored record and marker are not mutually consistent")
    if (record.get("status") != expected_status
            or record.get("valid_for_consumption") is not False
            or record.get("success_promotion_forbidden") is not True
            or record.get("requires_full_revalidation") is not True
            or record.get("restore_id") != data.get("restore_id")
            or record.get("restore_ledger") != relative
            or record.get("restored_from_backup_manifest") != manifest_relative):
        raise RuntimeError("restored control state is not RESTORED_UNVERIFIED")
    _assert_record_digest(record, data.get("restore_id"), phase="restore-control-state")
    if (digest.get("run_id") != data.get("restore_id")
            or digest.get("record_status") != expected_status):
        raise RuntimeError("restored generation digest state is stale")
    extras = data.get("extra_quarantined")
    if not isinstance(extras, list):
        raise RuntimeError("restore ledger extra quarantine is missing")
    extra_paths = set()
    restore_root_relative = _path_relative(path.parent, "restore root")
    for row in extras:
        if not isinstance(row, dict):
            raise RuntimeError("restore ledger extra row is malformed")
        extra_path = _repo_relative(row.get("quarantine_path"),
                                    "restore ledger extra path")
        if (not extra_path.startswith(restore_root_relative.rstrip("/") + "/")
                or extra_path in extra_paths):
            raise RuntimeError("restore ledger extra path is invalid or duplicated")
        digest = _require_sha(row.get("sha256"), "restore ledger extra SHA")
        _, extra_file = _repo_file(extra_path, "restore ledger extra file")
        if (not extra_file.is_file() or extra_file.is_symlink()
                or extra_file.stat().st_size != row.get("size_bytes")
                or _sha(extra_file) != digest):
            raise RuntimeError(f"restore extra file is missing or changed: {extra_path}")
        extra_paths.add(extra_path)
    if data.get("extra_file_count") != len(extras):
        raise RuntimeError("restore ledger extra file count is stale")
    return data


def restore_from_backup(manifest_path: str | Path, *, restore_id: str | None = None) -> dict:
    """Restore a verified backup into an explicitly unverified state."""
    manifest_relative = _repo_relative(
        manifest_path if isinstance(manifest_path, str)
        else _path_relative(Path(manifest_path), "backup manifest"),
        "backup manifest")
    backup_root_relative = _path_relative(BACKUP_ROOT, "backup root")
    if not manifest_relative.startswith(backup_root_relative.rstrip("/") + "/"):
        raise RuntimeError("backup manifest must stay inside source-backups")
    _, manifest_file = _repo_file(manifest_relative, "backup manifest")
    if not manifest_file.is_file():
        raise RuntimeError(f"backup manifest is missing: {manifest_relative}")
    manifest = _json(manifest_file)
    by_path, _ = _restore_manifest_paths(manifest, manifest_relative)
    historical_paths = (
        _assert_historical_3mf_contract(phase="before-restore")
        if "hardware/stl" in BACKUP_MUTABLE_ROOTS else frozenset()
    )
    control_paths = _restore_control_paths()
    control_by_path = {
        path: row for path, row in by_path.items() if path in control_paths
    }
    material_by_path = {
        path: row for path, row in by_path.items() if path not in control_paths
    }
    if set(control_by_path) | set(material_by_path) != set(by_path) \
            or set(control_by_path) & set(material_by_path):
        raise RuntimeError("restore backup paths cannot be uniquely classified")
    # Older backup manifests may still list the historical plates.  They may
    # only be replaced by an object whose SHA is the dedicated historical SHA;
    # otherwise a restore could overwrite a protected plate with unrelated
    # bytes even though the current inventory was verified before the move.
    for original, row in sorted(material_by_path.items()):
        if original not in historical_paths:
            continue
        digest = _require_sha(row.get("sha256"), "restore historical 3MF SHA")
        if digest != HISTORICAL_3MF_SHA256[original]:
            raise RuntimeError(
                f"restore historical 3MF backup SHA differs: {original}")
        _verify_restore_backup_object(row, "restore historical 3MF object")
    restore_id = restore_id or uuid.uuid4().hex
    restore_id = _assert_run_id(restore_id, "restore_id")
    run_root = manifest_file.parent
    restore_root = run_root / f"restore-{restore_id}"
    restore_relative = _path_relative(restore_root, "restore root")
    _, checked_restore_root = _repo_file(restore_relative, "restore root")
    if checked_restore_root.exists():
        raise RuntimeError(f"restore run already exists: {restore_relative}")
    checked_restore_root.mkdir(parents=True, exist_ok=False)
    extra_root = checked_restore_root / "extra"
    entries = []
    extra_entries = []
    try:
        # Inventory first, before any move, so a concurrent edit/addition is
        # detected rather than silently becoming part of the restored state.
        current_rows = _owned_inventory_for_roots(
            BACKUP_MUTABLE_ROOTS, require_exists=False)
        current_by_path = {row["path"]: row for row in current_rows}
        expected_paths = set(by_path)
        for original, row in sorted(current_by_path.items()):
            if original not in expected_paths:
                source = ROOT / original
                destination = extra_root / original
                destination_relative = _path_relative(destination, "restore extra path")
                _repo_file(destination_relative, "restore extra path")
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.is_symlink():
                    raise RuntimeError(f"restore source is a symlink: {original}")
                shutil.move(str(source), str(destination))
                if not destination.is_file() or _sha(destination) != row["sha256"]:
                    raise RuntimeError(f"restore extra quarantine verification failed: {original}")
                extra_entries.append({
                    "original_path": original,
                    "quarantine_path": destination_relative,
                    "sha256": row["sha256"],
                    "size_bytes": row["size_bytes"],
                    "verified": True,
                })
        objects_root = manifest_file.parent.parent.parent / "objects"
        objects_relative = _path_relative(objects_root, "restore object root")
        _, checked_objects_root = _repo_file(objects_relative, "restore object root")
        # Control objects are intentionally not copied to the active control
        # paths.  Verify their immutable object bytes before any new control
        # state is written, then record the replacement explicitly below.
        for original, row in sorted(control_by_path.items()):
            _verify_restore_backup_object(row, "restore control object")

        for original, row in sorted(material_by_path.items()):
            digest = _require_sha(row.get("sha256"), "restore original SHA")
            object_path = checked_objects_root / digest
            _restore_object_to_path(object_path, ROOT / original, digest)
            target = ROOT / original
            entries.append({
                "original_path": original,
                "backup_object": (Path(objects_relative) / digest).as_posix(),
                "sha256": digest,
                "size_bytes": target.stat().st_size,
                "restored": True,
                "verified": True,
                "classification": "non_control_restored",
            })
        if historical_paths:
            _assert_historical_3mf_contract(phase="after-restore")
        control_entries = []
        for original, row in sorted(control_by_path.items()):
            control_entries.append({
                "original_path": original,
                "backup_object": row["backup_object"],
                "sha256": row["sha256"],
                "backup_sha256": row["backup_sha256"],
                "size_bytes": row["size_bytes"],
                "verified": True,
                "replaced_by_restored_unverified": True,
                "classification": "control_replaced",
            })
        ledger_path = checked_restore_root / "restore-ledger.json"
        ledger_relative = _path_relative(ledger_path, "restore ledger")
        ledger = {
            "schema_version": 1,
            "status": "RESTORED_UNVERIFIED",
            "valid_for_consumption": False,
            "success_promotion_forbidden": True,
            "requires_full_revalidation": True,
            "restore_id": restore_id,
            "backup_manifest": manifest_relative,
            "files": sorted(entries, key=lambda row: row["original_path"]),
            "control_files": control_entries,
            "extra_quarantined": sorted(extra_entries,
                                         key=lambda row: row["original_path"]),
            "file_count": len(entries),
            "control_file_count": len(control_entries),
            "extra_file_count": len(extra_entries),
            "post_restore_status": "RESTORED_UNVERIFIED",
            "policy": "復旧後は全生成・全検証を最初から行うまで成功扱いにしない",
        }
        # The ledger is written before the control record so a record can never
        # point at a missing restoration audit trail.
        _atomic_json_write(ledger_path, ledger, "restore ledger")
        old_record = None
        record_target = ROOT / _control_relative(RECORD, "generation record")
        if record_target.is_file() and not record_target.is_symlink():
            try:
                old_record = _json(record_target)
            except (OSError, ValueError, json.JSONDecodeError):
                old_record = None
        restored_record = dict(old_record) if isinstance(old_record, dict) else {}
        restored_record.update({
            "schema_version": max(3, int(restored_record.get("schema_version", 3))),
            "run_id": restore_id,
            "restore_id": restore_id,
            "status": "RESTORED_UNVERIFIED",
            "valid_for_consumption": False,
            "success_promotion_forbidden": True,
            "requires_full_revalidation": True,
            "record_digest": {
                "path": _record_digest_path_relative(),
                "algorithm": "sha256(record file bytes)",
                "self_referential": False,
                "policy": "sidecar is written only after the record bytes are atomically replaced",
            },
            "restore_ledger": ledger_relative,
            "restored_from_backup_manifest": manifest_relative,
            "error": "restored bytes require a fresh generation and full validation",
        })
        _write_record(restored_record)
        _write_marker(restored_record)
        verified = _assert_restore_ledger(ledger_relative)
        if (restored_record.get("status") == "GENERATED"
                or restored_record.get("valid_for_consumption") is True):
            raise RuntimeError("restore unexpectedly produced a consumable record")
        return verified
    except Exception as exc:
        # A restore can fail after moving some current files or replacing some
        # targets.  Keep that evidence and make the checkout visibly
        # non-consumable; deleting the restore root would erase the only audit
        # trail needed to recover from a partial restore.
        failure = {
            "schema_version": 1,
            "status": "RESTORE_INCOMPLETE",
            "valid_for_consumption": False,
            "success_promotion_forbidden": True,
            "requires_full_revalidation": True,
            "restore_id": restore_id,
            "backup_manifest": manifest_relative,
            "error": str(exc),
            "policy": "partial restore is retained for manual recovery and cannot be promoted",
        }
        try:
            failure_path = checked_restore_root / "restore.incomplete.json"
            _atomic_json_write(failure_path, failure, "restore failure ledger")
            failure_record = {
                "schema_version": 3,
                "run_id": restore_id,
                "status": "RESTORE_INCOMPLETE",
                "valid_for_consumption": False,
                "restore_ledger": _path_relative(
                    failure_path, "restore failure ledger"),
                "restored_from_backup_manifest": manifest_relative,
                "error": str(exc),
                "stages": [],
            }
            _write_record(failure_record)
            _write_marker(failure_record)
        except Exception:
            # Preserve the original restore failure; the partial root itself
            # remains on disk even if the secondary fail-closed marker write
            # is unavailable.
            pass
        raise


def _assert_quarantine_ledger(record: dict, run_id: str, *, phase: str) -> None:
    quarantine = record.get("quarantine")
    if (not isinstance(quarantine, dict)
            or quarantine.get("status") != "QUARANTINE_COMPLETE"
            or quarantine.get("run_id") != run_id):
        raise RuntimeError(f"generation quarantine ledger is missing or stale ({phase})")
    manifest_path = _repo_relative(quarantine.get("manifest"), "quarantine manifest path")
    quarantine_root_relative = _path_relative(
        OUTPUT_ROOT / "quarantine" / run_id, "quarantine root")
    if not manifest_path.startswith(quarantine_root_relative.rstrip("/") + "/"):
        raise RuntimeError(f"quarantine manifest is outside its run root ({phase})")
    _, manifest_file = _repo_file(manifest_path, "quarantine manifest")
    if not manifest_file.is_file():
        raise RuntimeError(f"quarantine manifest is missing ({phase})")
    manifest = _json(manifest_file)
    files = manifest.get("files")
    if (manifest.get("status") != "QUARANTINE_COMPLETE"
            or manifest.get("run_id") != run_id
            or manifest.get("root") != quarantine_root_relative
            or not isinstance(files, list)
            or manifest.get("file_count") != len(files)):
        raise RuntimeError(f"quarantine manifest state is invalid ({phase})")
    listed_quarantine_paths = set()
    for index, row in enumerate(files):
        if (not isinstance(row, dict) or row.get("run_id") != run_id
                or row.get("verified") is not True):
            raise RuntimeError(f"quarantine manifest file[{index}] is malformed ({phase})")
        original = _repo_relative(row.get("original_path"), "quarantine original path")
        if not any(original == root or original.startswith(root.rstrip("/") + "/")
                   for root in MATERIAL_OUTPUT_ROOTS):
            raise RuntimeError(f"quarantine original path is outside material roots: {original}")
        path = _repo_relative(row.get("quarantine_path"), "quarantine file path")
        if not path.startswith(quarantine_root_relative.rstrip("/") + "/"):
            raise RuntimeError(f"quarantine file is outside its run root: {path}")
        expected = _require_sha(row.get("sha256"), "quarantine file SHA")
        _, file = _repo_file(path, "quarantine file")
        if (not file.is_file() or file.stat().st_size != row.get("size_bytes")
                or _sha(file) != expected):
            raise RuntimeError(f"quarantined file is missing or changed: {path}")
        if path in listed_quarantine_paths:
            raise RuntimeError(f"quarantine manifest has duplicate file: {path}")
        listed_quarantine_paths.add(path)
    actual_quarantine_paths = {
        row["path"] for row in _owned_inventory(
            quarantine_root_relative, require_exists=True)
    }
    if actual_quarantine_paths != listed_quarantine_paths | {manifest_path}:
        raise RuntimeError(f"quarantine root contains unregistered files ({phase})")


def _assert_record_source_closure(record: dict, *, phase: str) -> None:
    """Require the on-disk record to describe this exact source snapshot."""
    freeze = record.get("config_freeze")
    if not isinstance(freeze, dict):
        raise RuntimeError(f"generation record has no config_freeze ({phase})")
    paths = freeze.get("paths")
    if not isinstance(paths, dict) or paths != _SOURCE_FREEZE:
        raise RuntimeError(f"generation record source SHA closure differs ({phase})")
    if record.get("source_freeze_sha256_at_start") != _SOURCE_FREEZE:
        raise RuntimeError(f"generation record start source closure differs ({phase})")
    if record.get("source_directory_snapshot_at_start") != _SOURCE_DIRECTORY_FREEZE:
        raise RuntimeError(f"generation record start source snapshot differs ({phase})")
    config_sha = _require_sha(paths.get("hardware/src/config.py"),
                              f"generation record config SHA ({phase})")
    if freeze.get("sha256") != config_sha:
        raise RuntimeError(f"generation record config_freeze SHA differs ({phase})")
    if record.get("config_sha256_at_start") != config_sha:
        raise RuntimeError(f"generation record start config SHA differs ({phase})")
    finish_sha = record.get("config_sha256_at_finish")
    if (record.get("status") in {"GENERATED", "FINALIZING"}
            and finish_sha != config_sha):
        raise RuntimeError(f"generation record finish config SHA differs ({phase})")
    if finish_sha is not None and finish_sha != config_sha:
        raise RuntimeError(f"generation record finish config SHA differs ({phase})")
    finish_paths = record.get("source_freeze_sha256_at_finish")
    finish_snapshot = record.get("source_directory_snapshot_at_finish")
    if (finish_paths is not None and finish_paths != _SOURCE_FREEZE
            or finish_snapshot is not None and finish_snapshot != _SOURCE_DIRECTORY_FREEZE):
        raise RuntimeError(f"generation record finish source closure differs ({phase})")
    if record.get("status") in {"GENERATED", "FINALIZING"}:
        if finish_paths != _SOURCE_FREEZE or finish_snapshot != _SOURCE_DIRECTORY_FREEZE:
            raise RuntimeError(f"generation record finish source closure is missing ({phase})")
    snapshot = freeze.get("directory_snapshot")
    if not isinstance(snapshot, dict) or snapshot != _SOURCE_DIRECTORY_FREEZE:
        raise RuntimeError(f"generation record source directory snapshot differs ({phase})")
    selectors = freeze.get("directory_selectors")
    expected_selectors = [
        {"path": path, "kind": kind} for path, kind in SOURCE_DIRECTORY_SELECTORS
    ]
    if selectors != expected_selectors:
        raise RuntimeError(f"generation record source directory selectors differ ({phase})")
    generated = freeze.get("generated_source_paths")
    expected_generated = {
        "exact": sorted(GENERATED_SOURCE_PATHS),
        "prefixes": list(GENERATED_SOURCE_PREFIXES),
    }
    if (not isinstance(generated, dict)
            or generated.get("exact") != expected_generated["exact"]
            or generated.get("prefixes") != expected_generated["prefixes"]):
        raise RuntimeError(f"generation record generated-source policy differs ({phase})")
    literal_audit = freeze.get("static_literal_audit")
    expected_literal_audit = {
        "enabled": True,
        "paths": sorted(SOURCE_LITERAL_AUDIT_PATHS),
        "policy": "concrete repository path literals in serial runtime sources must be frozen or explicitly generated/owned",
    }
    if literal_audit != expected_literal_audit:
        raise RuntimeError(f"generation record static source literal audit differs ({phase})")


def _assert_run_state_contract(record: dict, run_id: str | None, *, mode: str) -> str:
    """Check marker/record state before any output can be accepted.

    ``mode=active`` is used by the full serial run while its marker is present.
    ``mode=validate-only`` is a read-only check of a completed GENERATED run;
    it deliberately cannot repair, rewrite, or promote an old record.
    """
    if mode not in {"active", "validate-only"}:
        raise ValueError(f"unknown generation state mode: {mode}")
    if not isinstance(record, dict):
        raise RuntimeError("generation record must be a JSON object")
    schema = record.get("schema_version")
    if not isinstance(schema, int) or schema < 3:
        raise RuntimeError("generation record schema is too old for state validation")
    record_run_id = _assert_run_id(record.get("run_id"), "generation record.run_id")
    if run_id is not None and record_run_id != _assert_run_id(run_id, "run_id"):
        raise RuntimeError("generation record run_id does not match the current run")

    marker_exists = _marker_present()
    if marker_exists:
        marker = _read_control_json(MARKER, "incomplete marker")
        marker_schema = marker.get("schema_version")
        if not isinstance(marker_schema, int) or marker_schema < 3:
            raise RuntimeError("incomplete marker schema is too old for state validation")
        marker_run_id = _assert_run_id(marker.get("run_id"), "incomplete marker.run_id")
        if marker_run_id != record_run_id:
            raise RuntimeError("generation record and incomplete marker belong to different runs")
        if marker.get("valid_for_consumption") is not False:
            raise RuntimeError("incomplete marker must remain invalid for consumption")
        marker_status = marker.get("status")
        if marker_status not in {
            "INCOMPLETE", "RUNNING", "FINALIZING", "FAIL",
            "RESTORED_UNVERIFIED", "RESTORE_INCOMPLETE",
        }:
            raise RuntimeError(f"incomplete marker has an invalid state: {marker_status!r}")

    status = record.get("status")
    if status == "GENERATED" and marker_exists:
        raise RuntimeError("a success generation record cannot coexist with an incomplete marker")
    if mode == "validate-only":
        if status != "GENERATED":
            raise RuntimeError(
                f"validate-only requires current GENERATED record, got {status!r}")
        if record.get("valid_for_consumption") is not True:
            raise RuntimeError("validate-only requires valid_for_consumption=true")
        if marker_exists:
            raise RuntimeError("validate-only requires no incomplete marker")
    else:
        if status not in {"INCOMPLETE", "FINALIZING"}:
            raise RuntimeError(f"active generation requires an incomplete state, got {status!r}")
        if record.get("valid_for_consumption") is not False:
            raise RuntimeError("active generation record must be invalid for consumption")
        if not marker_exists:
            raise RuntimeError("active generation requires its incomplete marker")
    _assert_record_matches_disk(record, phase=f"state:{mode}")
    _assert_record_digest(record, record_run_id, phase=f"state:{mode}")
    _assert_backup_ledger(record, record_run_id, phase="state")
    if status in {"GENERATED", "FINALIZING"}:
        _assert_quarantine_ledger(record, record_run_id, phase="state")
    return record_run_id


def _expected_stage_names() -> tuple[str, ...]:
    return tuple(label for label, _ in _stage_commands())


def _assert_stage_ledger_alias(stage: dict, key: str, alias: str, label: str) -> list[dict]:
    value = stage.get(key)
    alias_value = stage.get(alias)
    if value != alias_value:
        raise RuntimeError(f"{label}: {key}/{alias} ledgers differ")
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"{label}: {key} ledger is missing or empty")
    return value


def _assert_stage_output_policy(stage: dict, name: str, label: str) -> dict:
    """Require a recorded stage to use the current root handoff contract."""
    expected = _stage_output_policy(name)
    if stage.get("output_policy") != expected:
        raise RuntimeError(f"{label}: output handoff policy differs")
    return expected


def _ledger_hash_map(rows: list[dict]) -> dict[str, str]:
    return {
        row["path"]: row["sha256"] for row in rows
        if isinstance(row, dict) and isinstance(row.get("path"), str)
    }


def _stage_output_delta(input_rows: list[dict], output_rows: list[dict],
                        label: str) -> dict:
    """Record which output files were new at a stage boundary.

    Shared roots are allowed to carry retained files into a later stage.  A
    separate delta makes that fact explicit: files in ``new_paths`` did not
    exist in the stage-start input ledger, while ``retained_input_paths`` are
    the byte-for-byte handoff files.  Keeping this relation in the run record
    prevents a final directory re-expansion from making a stage's own output
    look like a historical input.
    """
    input_hashes = _ledger_hash_map(input_rows)
    output_hashes = _ledger_hash_map(output_rows)
    input_paths = set(input_hashes)
    output_paths = set(output_hashes)
    retained = output_paths & input_paths
    changed_retained = sorted(
        path for path in retained if output_hashes[path] != input_hashes[path]
    )
    if changed_retained:
        raise RuntimeError(
            f"{label}: stage changed an input path before output ledger capture: "
            f"{changed_retained[:12]!r}")
    return {
        "input_paths_at_stage_start": sorted(input_paths),
        "new_paths": sorted(output_paths - input_paths),
        "new_sha256": {
            path: output_hashes[path] for path in sorted(output_paths - input_paths)
        },
        "retained_input_paths": sorted(retained),
        "retained_input_sha256": {
            path: output_hashes[path] for path in sorted(retained)
        },
        "policy": (
            "new output paths are excluded from this stage's historical input; "
            "retained paths must match the stage-start input SHA"
        ),
    }


def _stage_handoff_records(previous_stages: list[dict], input_rows: list[dict]) -> list[dict]:
    """Describe generated files inherited from completed earlier stages."""
    input_hashes = _ledger_hash_map(input_rows)
    records = []
    for previous in previous_stages:
        if not isinstance(previous, dict):
            continue
        previous_name = previous.get("name")
        previous_hashes = _ledger_hash_map(previous.get("outputs"))
        shared = {
            path: input_hashes[path]
            for path in sorted(input_hashes)
            if path in previous_hashes
        }
        if not shared:
            continue
        records.append({
            "from_stage": previous_name,
            "paths": sorted(shared),
            "sha256": shared,
            "relation": "input SHA equals the earlier stage output SHA",
        })
    return records


def _assert_stage_handoff_records(stage: dict, previous_stages: list[dict],
                                  inputs: list[dict], label: str) -> None:
    spec = stage.get("input_spec")
    if not isinstance(spec, dict) or not isinstance(spec.get("handoff_sources"), list):
        raise RuntimeError(f"{label}: handoff input provenance is missing")
    expected = _stage_handoff_records(previous_stages, inputs)
    if spec["handoff_sources"] != expected:
        raise RuntimeError(f"{label}: handoff input provenance differs")
    previous_by_name = {
        previous.get("name"): previous for previous in previous_stages
        if isinstance(previous, dict)
    }
    input_hashes = _ledger_hash_map(inputs)
    for handoff in expected:
        previous_name = handoff["from_stage"]
        previous = previous_by_name.get(previous_name)
        if previous is None:
            raise RuntimeError(f"{label}: handoff source stage is missing: {previous_name}")
        previous_hashes = _ledger_hash_map(previous.get("outputs"))
        if any(input_hashes.get(path) != previous_hashes.get(path)
               for path in handoff["paths"]):
            raise RuntimeError(f"{label}: handoff input SHA differs from {previous_name}")


def _assert_stage_output_delta(stage: dict, inputs: list[dict], outputs: list[dict],
                               label: str) -> None:
    delta = stage.get("output_delta")
    if not isinstance(delta, dict):
        raise RuntimeError(f"{label}: stage output delta is missing")
    expected = _stage_output_delta(inputs, outputs, label)
    for key in (
        "input_paths_at_stage_start", "new_paths", "new_sha256",
        "retained_input_paths", "retained_input_sha256",
    ):
        if delta.get(key) != expected[key]:
            raise RuntimeError(f"{label}: stage output delta differs: {key}")
    if delta.get("policy") != expected["policy"]:
        raise RuntimeError(f"{label}: stage output delta policy is missing")
    input_paths = set(expected["input_paths_at_stage_start"])
    new_paths = set(expected["new_paths"])
    if input_paths.intersection(new_paths):
        raise RuntimeError(f"{label}: stage-owned output was mixed into historical input")


def _assert_stage_handoff(previous_stage: dict, next_stage: dict,
                          previous_name: str, next_name: str, *, phase: str) -> None:
    """Require every prior-stage output to survive in B inputs and outputs."""
    previous_outputs = _ledger_hash_map(previous_stage.get("outputs"))
    next_inputs = _ledger_hash_map(next_stage.get("inputs"))
    next_outputs = _ledger_hash_map(next_stage.get("outputs"))
    missing_inputs = sorted(
        path for path, digest in previous_outputs.items()
        if next_inputs.get(path) != digest
    )
    missing_outputs = sorted(
        path for path, digest in previous_outputs.items()
        if next_outputs.get(path) != digest
    )
    if missing_inputs:
        raise RuntimeError(
            f"{previous_name}->{next_name}: handoff input is missing or stale: "
            f"{missing_inputs[:12]!r} ({phase})")
    if missing_outputs:
        raise RuntimeError(
            f"{previous_name}->{next_name}: prior output was deleted or mutated in B: "
            f"{missing_outputs[:12]!r} ({phase})")


def _assert_stage_input_spec(stage: dict, name: str, inputs: list[dict],
                             label: str) -> None:
    """Validate the stage-start input expansion without re-expanding roots.

    A directory such as ``hardware/stl`` or the feet output root is a dynamic
    handoff.  Re-expanding it at finalization would incorrectly include files
    created by that stage or a later stage.  The run therefore records both
    the requested path specification and the exact file paths expanded at
    stage start; immutable source changes are handled by the global source
    snapshot and all recorded file hashes are checked independently.
    """
    spec = stage.get("input_spec")
    if not isinstance(spec, dict):
        raise RuntimeError(f"{label}: input specification is missing")
    requested = spec.get("requested_paths")
    expanded = spec.get("expanded_paths")
    source_paths = spec.get("source_freeze_paths")
    if (not isinstance(requested, list) or not requested
            or not isinstance(expanded, list) or not expanded
            or not isinstance(source_paths, list) or not source_paths):
        raise RuntimeError(f"{label}: input specification is incomplete")
    try:
        requested = [_repo_relative(path, f"{label}.input_spec.requested_paths")
                     for path in requested]
        expanded = [_repo_relative(path, f"{label}.input_spec.expanded_paths")
                    for path in expanded]
        source_paths = [_repo_relative(path, f"{label}.input_spec.source_freeze_paths")
                        for path in source_paths]
    except ValueError as exc:
        raise RuntimeError(f"{label}: input specification has an invalid path") from exc
    if requested != spec.get("requested_paths") or expanded != spec.get("expanded_paths") \
            or source_paths != spec.get("source_freeze_paths"):
        raise RuntimeError(f"{label}: input specification paths are not canonical")
    if requested != sorted(requested) or len(requested) != len(set(requested)):
        raise RuntimeError(f"{label}: requested input paths are not a unique sorted set")
    if expanded != sorted(expanded) or len(expanded) != len(set(expanded)):
        raise RuntimeError(f"{label}: expanded input paths are not a unique sorted set")
    dynamic_roots = STAGE_INPUT_DYNAMIC_ROOTS.get(name)
    required_exact = STAGE_INPUT_REQUIRED_EXACT.get(name)
    if dynamic_roots is None or required_exact is None:
        raise RuntimeError(f"{label}: stage input contract is missing")
    dynamic_roots = tuple(
        _repo_relative(path, f"{label}.input_spec.dynamic_roots")
        for path in dynamic_roots
    )
    required_exact = tuple(
        _repo_relative(path, f"{label}.input_spec.required_exact_paths")
        for path in required_exact
    )
    if spec.get("dynamic_roots") != list(dynamic_roots):
        raise RuntimeError(f"{label}: dynamic input root contract changed")
    if spec.get("required_exact_paths") != list(required_exact):
        raise RuntimeError(f"{label}: exact input path contract changed")
    if spec.get("dynamic_roots_are_stage_start_only") is not True:
        raise RuntimeError(f"{label}: dynamic roots are not marked stage-start-only")
    source_set = set(source_paths)
    if source_paths != sorted(_SOURCE_FREEZE) or source_set != set(_SOURCE_FREEZE):
        raise RuntimeError(f"{label}: source freeze input set is incomplete")
    requested_set = set(requested)
    if not source_set.issubset(requested_set):
        raise RuntimeError(f"{label}: immutable source is absent from requested input paths")
    missing_dynamic = sorted(set(dynamic_roots) - requested_set)
    if missing_dynamic:
        raise RuntimeError(
            f"{label}: dynamic input root is absent from requested paths: {missing_dynamic!r}")
    missing_exact = sorted(set(required_exact) - requested_set)
    if missing_exact:
        raise RuntimeError(
            f"{label}: required exact input is absent from requested paths: {missing_exact!r}")
    recorded_paths = [row.get("path") for row in inputs]
    if expanded != recorded_paths:
        raise RuntimeError(f"{label}: expanded input paths differ from input ledger")
    if spec.get("expanded_sha256") != _ledger_hash_map(inputs):
        raise RuntimeError(f"{label}: expanded input SHA map differs from ledger")
    missing_sources = sorted(set(source_paths) - set(expanded))
    if missing_sources:
        raise RuntimeError(
            f"{label}: immutable source is absent from stage input ledger: "
            f"{missing_sources[:12]!r}")
    # A manifest's referenced STL paths are exact generated inputs.  They are
    # deliberately recorded individually instead of requesting the whole
    # output directory, because the checker itself appends verification JSON
    # and render files to that directory.  The manifest bytes are already
    # covered by ``inputs`` and its SHA ledger before this closure is read.
    manifest_closure = set(required_exact)
    if name == "check_print_first_feet":
        manifests = ("outputs/print-first-20260905/feet/assembly.json",)
    elif name == "print_first_urdf":
        manifests = (
            "outputs/print-first-20260905/feet/assembly.json",
            "outputs/print-first-20260905/legs/assembly.json",
            "outputs/print-first-20260905/body/assembly.json",
            "docs/audits/20260905-round2/xiao-retention-plan.json",
        )
    else:
        manifests = ()
    for manifest in manifests:
        if manifest not in requested_set or manifest not in set(expanded):
            raise RuntimeError(f"{label}: manifest input is absent from stage ledger: {manifest}")
        try:
            manifest_closure.update(_manifest_stl_paths(manifest, f"{label} input manifest"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"{label}: manifest input closure cannot be read: {manifest}") from exc
    missing_manifest_inputs = sorted(manifest_closure - set(expanded))
    if missing_manifest_inputs:
        raise RuntimeError(
            f"{label}: manifest-referenced input is absent from stage ledger: "
            f"{missing_manifest_inputs[:12]!r}")
    allowed_requested = source_set | set(dynamic_roots) | set(required_exact) | manifest_closure
    unexpected_requested = sorted(requested_set - allowed_requested)
    if unexpected_requested:
        raise RuntimeError(
            f"{label}: input path is outside the declared allowlist: "
            f"{unexpected_requested[:12]!r}")
    allowed_roots = tuple(dynamic_roots)
    for path in expanded:
        if path in source_set or path in manifest_closure:
            continue
        if not any(path == root or path.startswith(root.rstrip("/") + "/")
                   for root in allowed_roots):
            raise RuntimeError(f"{label}: expanded input is outside declared input contract: {path}")


def _assert_record_stage_ledgers(record: dict, run_id: str, *, phase: str,
                                 require_complete: bool = True) -> None:
    """Re-read every stage input/output ledger and reject any later mutation."""
    run_id = _assert_run_id(run_id, "stage ledger run_id")
    stages = record.get("stages")
    if not isinstance(stages, list):
        raise RuntimeError(f"generation record stages are missing ({phase})")
    expected_names = _expected_stage_names()
    names = []
    stage_by_name = {}
    for index, stage in enumerate(stages):
        label = f"stage[{index}]"
        if not isinstance(stage, dict):
            raise RuntimeError(f"{label}: malformed stage record")
        if stage.get("run_id") != run_id:
            raise RuntimeError(f"{label}: run_id does not match ({phase})")
        name = stage.get("name")
        if name not in expected_names:
            raise RuntimeError(f"{label}: unknown stage name {name!r}")
        names.append(name)
        if index >= len(expected_names) or name != expected_names[index]:
            raise RuntimeError(f"{label}: stage order differs ({phase})")
        if name in stage_by_name:
            raise RuntimeError(f"{label}: duplicate stage name ({phase})")
        stage_by_name[name] = stage
        _assert_stage_output_policy(stage, name, label)
        status = stage.get("status")
        if status == "RUNNING":
            if require_complete:
                raise RuntimeError(f"{label}: running stage remains at {phase}")
            continue
        if status != "COMPLETED":
            raise RuntimeError(f"{label}: stage status is not COMPLETED: {status!r}")
        if stage.get("returncode") != 0:
            raise RuntimeError(f"{label}: nonzero return code is recorded")
        inputs = _assert_stage_ledger_alias(stage, "inputs", "input_ledger", label)
        outputs = _assert_stage_ledger_alias(stage, "outputs", "output_ledger", label)
        if stage.get("input_sha256") != _ledger_hash_map(inputs):
            raise RuntimeError(f"{label}: input_sha256 map differs ({phase})")
        if stage.get("output_sha256") != _ledger_hash_map(outputs):
            raise RuntimeError(f"{label}: output_sha256 map differs ({phase})")
        _verify_ledger(inputs, f"{label}.inputs", run_id=run_id)
        _verify_ledger(outputs, f"{label}.outputs", run_id=run_id)
        _assert_stage_output_contract(name, outputs, phase=phase)
        _assert_stage_input_spec(stage, name, inputs, label)
        _assert_stage_handoff_records(stage, stages[:index], inputs, label)
        _assert_stage_output_delta(stage, inputs, outputs, label)
        stage_roots = STAGE_OUTPUT_ROOTS[name]
        allowed_output_paths = set(
            row["path"] for row in _owned_inventory_for_roots(
                stage_roots, require_exists=False, run_id=run_id)
        )
        if any(row["path"] not in allowed_output_paths for row in outputs):
            raise RuntimeError(f"{label}: output ledger contains a path outside stage roots")
        config_sha = stage.get("config_sha256")
        if config_sha != _SOURCE_FREEZE.get("hardware/src/config.py"):
            raise RuntimeError(f"{label}: config SHA is stale ({phase})")
    # Shared output roots are explicit append-only handoffs.  The next stage
    # must consume every previous output path with the same SHA; if it writes
    # over one of those paths, the earlier ledger remains stale and this check
    # fails as well.  This relation makes the permitted root overlap auditable
    # and prevents a broad directory root from hiding an untracked mutation.
    for name, stage in stage_by_name.items():
        policy = _stage_output_policy(name)
        next_name = policy["next_stage"]
        if policy["mode"] != "append_only" or next_name not in stage_by_name:
            continue
        next_stage = stage_by_name[next_name]
        if stage.get("status") != "COMPLETED" or next_stage.get("status") != "COMPLETED":
            if require_complete:
                raise RuntimeError(f"{name}: append-only handoff is incomplete ({phase})")
            continue
        _assert_stage_handoff(stage, next_stage, name, next_name, phase=phase)
    if require_complete and tuple(names) != expected_names:
        raise RuntimeError(f"generation record stage set is incomplete ({phase})")


def _assert_final_output_ledger(record: dict, run_id: str, *, phase: str) -> list[dict]:
    rows = record.get("final_outputs")
    _verify_ledger(rows, f"final_outputs ({phase})", run_id=run_id)
    if record.get("final_output_sha256") != _ledger_hash_map(rows):
        raise RuntimeError(f"final output SHA map differs ({phase})")
    actual = _owned_inventory_for_roots(MATERIAL_OUTPUT_ROOTS, require_exists=False,
                                        run_id=run_id)
    expected_paths = {row["path"] for row in rows}
    actual_paths = {row["path"] for row in actual}
    if expected_paths != actual_paths:
        raise RuntimeError(
            f"final output path set changed ({phase}): "
            f"missing={sorted(expected_paths - actual_paths)[:12]!r}, "
            f"extra={sorted(actual_paths - expected_paths)[:12]!r}")
    return actual


def validate_outputs(*, run_id: str | None = None, mode: str | None = None,
                     record: dict | None = None) -> dict:
    """生成段の出力と、必要なら実行状態台帳を確認する。

    引数なしの呼び出しも、制御台帳が存在する場合は状態契約を自動適用
    する。したがって成功RECORDとマーカーの同居を、利用者向け検証経路
    が見逃すことはない。
    """
    if record is not None and mode is None:
        raise ValueError(
            "validate_outputs requires an explicit mode when record is supplied")
    if mode is None and record is None:
        if _record_present():
            record = _load_generation_record()
            # A public validation call must consume only a completed current
            # run.  INCOMPLETE/FINALIZING/FAIL records are evidence of an
            # interrupted or failed run and cannot be reinterpreted as an
            # active generation by silently selecting ``mode=active``.
            if record.get("status") != "GENERATED":
                raise RuntimeError(
                    "current generation record is not GENERATED; outputs are not consumable")
            mode = "validate-only"
        elif _marker_present():
            raise RuntimeError("incomplete marker exists without a generation record")
        else:
            raise RuntimeError("generation record is required for output validation")
    if mode is not None:
        if record is None:
            record = _load_generation_record()
        run_id = _assert_run_state_contract(record, run_id, mode=mode)
        _assert_record_source_closure(record, phase=f"validate:{mode}")
        _assert_record_stage_ledgers(record, run_id, phase=f"validate:{mode}",
                                     require_complete=mode == "validate-only")
    results = {
        "xiao": _assert_xiao_plan(),
        "feet": _assert_feet(),
        "legs": _assert_legs(),
        "body": _assert_body(),
        "profile": _assert_profile(),
        "urdf": _assert_urdf(),
    }
    if mode == "validate-only":
        # Direct callers of validate_outputs must receive the same completed
        # run contract as the CLI, including the final owned-file inventory.
        # The full generator uses mode=active before finalization and performs
        # these checks after it writes FINALIZING, so this is intentionally
        # limited to read-only validation of a GENERATED record.
        expected = _expected_owned_output_paths(results, record)
        _assert_owned_outputs_registered(
            expected, phase="validate:validate-only", run_id=run_id)
        _assert_final_output_ledger(record, run_id, phase="validate:validate-only")
    result = {
        "xiao": "PASS_CANDIDATE_ONLY",
        "feet": "PASS_CAD_CHECKS_WITH_PHYSICAL_UNVERIFIED",
        "legs": "PASS_GEOMETRY_WITH_PHYSICAL_UNVERIFIED",
        "body": "PASS_GEOMETRY_WITH_FULL_ASSEMBLY_RECHECK_REQUIRED",
        "profile": "PASS_SOURCE_CONFIG_BOUND",
        "urdf": "PASS_SERIALIZED_BUNDLE",
        "permitted_status_states": {
            key: sorted(values) for key, values in PERMITTED_STATUS_STATES.items()
        },
        "provenance": results,
    }
    if mode == "validate-only":
        _assert_record_checks(record, result, phase="validate:validate-only")
    return result


def _stage_commands() -> list[tuple[str, list[str]]]:
    if not PYTHON.is_file():
        raise FileNotFoundError(f"Python runtime is missing: {PYTHON}")
    def cmd(label: str, script: str, *args: str):
        return label, [str(PYTHON), str(ROOT / script), *args]

    feet_dir = OUTPUT_ROOT / "feet"
    return [
        cmd("legacy_build", "hardware/src/build_all.py"),
        cmd("head_eyecut", "tools/make_head_eyecut.py"),
        cmd("xiao_retention", "tools/xiao_retention_plan.py"),
        cmd("print_first_feet", "hardware/src/make_print_first_feet.py"),
        cmd("check_print_first_feet", "tools/check_print_first_feet.py",
            "--directory", str(feet_dir)),
        cmd("print_first_legs", "hardware/src/make_print_first_leg.py"),
        cmd("print_first_body", "hardware/src/make_print_first_body.py"),
        cmd("print_first_profile", "tools/generate_print_first_profile.py"),
        cmd("print_first_urdf", "tools/print_first_assembly.py"),
    ]


def _stage_artifacts(label: str, run_id: str | None = None) -> list[dict]:
    """各段終了時に所有出力全体のSHA台帳を返す。"""
    if run_id is None:
        # Compatibility for callers which only need the current inventory.
        roots = STAGE_OUTPUT_ROOTS.get(label)
        if roots is None:
            raise RuntimeError(f"stage has no owned output roots: {label}")
        return _owned_inventory_for_roots(roots, require_exists=True)
    return _stage_output_ledger(label, run_id)


def _write_record(payload: dict) -> None:
    """Atomically replace the record and its independent digest sidecar."""
    _assert_run_id(payload.get("run_id"), "generation record.run_id")
    _atomic_json_write(RECORD, payload, "generation record")
    _write_record_digest(payload)


def _write_marker(payload: dict) -> None:
    """Atomically replace the active-run marker after path checks."""
    _assert_run_id(payload.get("run_id"), "incomplete marker.run_id")
    _atomic_json_write(MARKER, payload, "incomplete marker")


def _command_record(command: list[str]) -> list[str]:
    """Persist command paths as repository-relative public values."""
    result = []
    for value in command:
        text = str(value)
        candidate = Path(text)
        if candidate.is_absolute():
            result.append(_path_relative(candidate, "stage command path"))
        else:
            result.append(text)
    return result


def _current_config_sha() -> str | None:
    try:
        relative, path = _repo_file("hardware/src/config.py", "config failure record")
        return _sha(path) if path.is_file() else None
    except (OSError, ValueError):
        return None


def _new_generation_record(run_id: str, started: str,
                           source_freeze: dict[str, str]) -> dict:
    run_id = _assert_run_id(run_id, "generation run_id")
    config_sha = _require_sha(source_freeze.get("hardware/src/config.py"),
                              "config freeze SHA")
    selectors = [{"path": path, "kind": kind}
                 for path, kind in SOURCE_DIRECTORY_SELECTORS]
    return {
        "schema_version": 3,
        "run_id": run_id,
        "status": "INCOMPLETE",
        "valid_for_consumption": False,
        "started_at": started,
        "config_sha256_at_start": config_sha,
        "record_digest": {
            "path": _record_digest_path_relative(),
            "algorithm": "sha256(record file bytes)",
            "self_referential": False,
            "policy": "sidecar is written only after the record bytes are atomically replaced",
        },
        "source_freeze_sha256_at_start": dict(source_freeze),
        "source_directory_snapshot_at_start": {
            path: dict(row) for path, row in _SOURCE_DIRECTORY_FREEZE.items()
        },
        "config_freeze": {
            "status": "FROZEN",
            "path": "hardware/src/config.py",
            "sha256": config_sha,
            "paths": dict(source_freeze),
            "directory_selectors": selectors,
            "directory_snapshot": {
                path: dict(row) for path, row in _SOURCE_DIRECTORY_FREEZE.items()
            },
            "generated_source_paths": {
                "exact": sorted(GENERATED_SOURCE_PATHS),
                "prefixes": list(GENERATED_SOURCE_PREFIXES),
                "policy": "generated outputs are hash-checked when consumed and are not immutable inputs",
            },
            "static_literal_audit": {
                "enabled": True,
                "paths": sorted(SOURCE_LITERAL_AUDIT_PATHS),
                "policy": "concrete repository path literals in serial runtime sources must be frozen or explicitly generated/owned",
            },
            "change_policy": "any source/config change during the serial run is a failure",
        },
        "stages": [],
    }


def _run_validate_only_inner() -> int:
    """Read-only validation of the current GENERATED run."""
    # This check is deliberately before source discovery and before every
    # write-capable helper.  A prior failed run remains the evidence of failure.
    if _marker_present():
        print(json.dumps({
            "status": "FAIL",
            "error": "existing incomplete marker blocks validate-only",
            "marker": _control_relative(MARKER, "incomplete marker"),
        }, ensure_ascii=False), file=sys.stderr)
        return 1
    try:
        _assert_historical_3mf_contract(phase="validate-only:start")
        current = _load_generation_record()
        run_id = _assert_run_state_contract(current, None, mode="validate-only")
        source_freeze = _initialize_source_freeze()
        _assert_record_source_closure(current, phase="validate-only:start")
        config_sha = source_freeze["hardware/src/config.py"]
        _assert_config_frozen(config_sha, phase="validate-only:start")
        checks = validate_outputs(run_id=run_id, mode="validate-only", record=current)
        _assert_record_stage_ledgers(current, run_id, phase="validate-only:finish")
        expected = _expected_owned_output_paths(checks, current)
        _assert_owned_outputs_registered(expected, phase="validate-only:finish")
        _assert_final_output_ledger(current, run_id, phase="validate-only:finish")
        config_sha_finish = _assert_config_frozen(config_sha, phase="validate-only:finish")
        if _marker_present():
            raise RuntimeError("incomplete marker appeared during validate-only")
        # No record or marker is written here.  The existing GENERATED record
        # remains byte-for-byte the same, and validation has no promotion path.
        print(json.dumps({
            "status": "VALIDATED_READ_ONLY",
            "record_status": current["status"],
            "run_id": run_id,
            "config_sha256": config_sha_finish,
            "checks": checks,
        }, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 - read-only failure leaves evidence intact
        print(json.dumps({"status": "FAIL", "error": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 1


def _run_validate_only() -> int:
    """Run validation with a hard no-bytecode/no-write runtime guard."""
    with _no_bytecode_writes():
        return _run_validate_only_inner()


def _run_full_generation(started: str) -> int:
    """Run the one serial generation sequence under an already-held lock."""
    run_id = uuid.uuid4().hex
    record: dict | None = None
    run_started = False
    try:
        _assert_historical_3mf_contract(phase="generation:start")
        # Source discovery is read-only.  Do not replace a previous record or
        # marker until the complete overwrite backup has been verified.
        source_freeze = _initialize_source_freeze()
        config_sha256_at_start = source_freeze["hardware/src/config.py"]
        record = _new_generation_record(run_id, started, source_freeze)
        record["backup"] = _create_source_backup(run_id)

        # The backup is complete before this atomic replacement of the old
        # RECORD.  From this point every failure has a current invalid run id.
        run_started = True
        _write_record(record)
        _write_marker(record)
        record["quarantine"] = _quarantine_previous_owned_outputs(
            run_id, record["backup"])
        _write_record(record)
        _write_marker(record)

        for label, command in _stage_commands():
            _assert_config_frozen(config_sha256_at_start, phase=f"{label}:before")
            _assert_stage_output_roots_safe(label, phase=f"{label}:before")
            input_paths = _stage_input_paths(label)
            input_ledger = _ledger_for_paths(
                input_paths, f"{label} input ledger", run_id=run_id)
            stage = {
                "run_id": run_id,
                "name": label,
                "status": "RUNNING",
                "output_policy": _stage_output_policy(label),
                "command": _command_record(command),
                "returncode": None,
                "config_sha256": None,
                "seconds": None,
                "inputs": input_ledger,
                "input_ledger": input_ledger,
                "input_sha256": _ledger_hash_map(input_ledger),
                "input_spec": {
                    "requested_paths": list(input_paths),
                    "expanded_paths": [row["path"] for row in input_ledger],
                    "expanded_sha256": _ledger_hash_map(input_ledger),
                    "source_freeze_paths": sorted(_SOURCE_FREEZE),
                    "dynamic_roots": list(STAGE_INPUT_DYNAMIC_ROOTS[label]),
                    "required_exact_paths": list(STAGE_INPUT_REQUIRED_EXACT[label]),
                    "handoff_sources": _stage_handoff_records(
                        record["stages"], input_ledger),
                    "dynamic_roots_are_stage_start_only": True,
                },
                "outputs": [],
                "output_ledger": [],
                "output_sha256": {},
                "output_delta": None,
                "artifacts": [],
            }
            record["stages"].append(stage)
            _write_record(record)
            _write_marker(record)
            print(f"[print-first] {label}: {' '.join(command)}", flush=True)
            stage_started = time.monotonic()
            completed = subprocess.run(command, cwd=ROOT, check=False)
            config_after_stage = _assert_config_frozen(
                config_sha256_at_start, phase=f"{label}:after")
            output_ledger = _stage_output_ledger(label, run_id)
            stage.update({
                "status": "COMPLETED" if completed.returncode == 0 else "FAILED",
                "returncode": int(completed.returncode),
                "config_sha256": config_after_stage,
                "seconds": round(time.monotonic() - stage_started, 3),
                "outputs": output_ledger,
                "output_ledger": output_ledger,
                "output_sha256": _ledger_hash_map(output_ledger),
                "output_delta": _stage_output_delta(
                    input_ledger, output_ledger, f"{label} output delta"),
                "artifacts": output_ledger,
            })
            _write_record(record)
            _write_marker(record)
            if completed.returncode:
                raise RuntimeError(
                    f"stage {label} failed with return code {completed.returncode}")
            _assert_record_stage_ledgers(
                record, run_id, phase=f"{label}:completed", require_complete=False)

        _assert_config_frozen(config_sha256_at_start, phase="before-validate")
        _assert_record_stage_ledgers(record, run_id, phase="before-validate")
        checks = validate_outputs(run_id=run_id, mode="active", record=record)
        _assert_config_frozen(config_sha256_at_start, phase="before-final-ledger")
        expected = _expected_owned_output_paths(checks, record)
        final_outputs = _assert_owned_outputs_registered(
            expected, phase="before-final-record", run_id=run_id)
        _assert_config_frozen(config_sha256_at_start, phase="finish")
        finished = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        record.update({
            "status": "FINALIZING",
            "valid_for_consumption": False,
            "checks": checks,
            "final_outputs": final_outputs,
            "final_output_sha256": _ledger_hash_map(final_outputs),
            "config_sha256_at_finish": config_sha256_at_start,
            "source_freeze_sha256_at_finish": dict(_SOURCE_FREEZE),
            "source_directory_snapshot_at_finish": {
                path: dict(row) for path, row in _SOURCE_DIRECTORY_FREEZE.items()
            },
            "finished_at": finished,
        })
        _write_record(record)
        _write_marker(record)
        # Recheck the complete active state after writing final ledgers.  The
        # marker is removed only after this point, so any failure remains
        # visibly incomplete.
        _assert_run_state_contract(record, run_id, mode="active")
        _assert_record_stage_ledgers(record, run_id, phase="finalizing")
        _assert_final_output_ledger(record, run_id, phase="finalizing")
        _assert_config_frozen(config_sha256_at_start, phase="finalizing")
        marker_relative = _control_relative(MARKER, "incomplete marker")
        _, marker = _repo_file(marker_relative, "incomplete marker")
        marker.unlink()
        record.update({"status": "GENERATED", "valid_for_consumption": True})
        # The success record is published only after the marker is gone.  A
        # write failure is caught below and recreates a FAIL marker, never a
        # success+marker combination.
        try:
            _write_record(record)
        except Exception:
            record.update({"status": "FAIL", "valid_for_consumption": False})
            raise
        print(json.dumps({
            "status": record["status"],
            "run_id": run_id,
            "checks": checks,
            "record": _control_relative(RECORD, "generation record"),
        }, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 - preserve current invalid evidence
        if run_started and record is not None:
            record.update({
                "status": "FAIL",
                "valid_for_consumption": False,
                "error": repr(exc),
                "config_sha256_at_finish": _current_config_sha(),
                "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            })
            try:
                _write_record(record)
            except Exception:
                # Keep trying the marker: it is the fail-closed signal even if
                # the record filesystem is temporarily unavailable.
                pass
            try:
                _write_marker(record)
            except Exception:
                pass
        print(json.dumps({
            "status": "FAIL",
            "run_id": run_id,
            "error": str(exc),
            "marker": _control_relative(MARKER, "incomplete marker"),
        }, ensure_ascii=False), file=sys.stderr)
        return 1


def _main_locked(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only", action="store_true",
                        help="生成せず、既存の全出力契約だけを確認する")
    parser.add_argument(
        "--restore-backup", metavar="MANIFEST",
        help="指定した source-backups の台帳から復旧し、RESTORED_UNVERIFIED として記録する")
    parser.add_argument(
        "--verify-restore", metavar="LEDGER",
        help="復旧台帳を読み取り専用で検証する")
    args = parser.parse_args(argv)
    if args.validate_only and (args.restore_backup or args.verify_restore):
        parser.error("--validate-only と復旧操作/復旧検証は同時に指定できません")
    if args.restore_backup and args.verify_restore:
        parser.error("--restore-backup と --verify-restore は同時に指定できません")
    started = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if args.validate_only:
        return _run_validate_only()
    if args.verify_restore:
        ledger = _assert_restore_ledger(args.verify_restore)
        print(json.dumps({
            "status": "RESTORE_VERIFIED_READ_ONLY",
            "restore_id": ledger["restore_id"],
            "ledger": _repo_relative(args.verify_restore, "restore ledger"),
        }, ensure_ascii=False))
        return 0
    if args.restore_backup:
        ledger = restore_from_backup(args.restore_backup)
        print(json.dumps({
            "status": ledger["status"],
            "restore_id": ledger["restore_id"],
            "backup_manifest": ledger["backup_manifest"],
            "file_count": ledger["file_count"],
        }, ensure_ascii=False))
        return 0
    return _run_full_generation(started)


def main(argv: list[str] | None = None) -> int:
    # The non-blocking lock covers argument handling, validation, backup,
    # quarantine, every subprocess, and final publication.  A concurrent
    # validate-only therefore cannot observe or mutate an intermediate run.
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    read_only = "--validate-only" in raw_argv or "--verify-restore" in raw_argv
    try:
        with _generation_lock(read_only=read_only):
            return _main_locked(argv)
    except RunLockBusy as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - lock/path failures are fail-closed
        print(json.dumps({"status": "FAIL", "error": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
