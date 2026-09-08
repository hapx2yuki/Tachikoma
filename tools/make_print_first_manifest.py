#!/usr/bin/env python3
"""印刷優先候補の採用STL・数量・ハッシュを表とJSONへ固定する。

この台帳は設計途中の候補を本番印刷へ進める命令ではない。初回の全体仮組み
（その内側に1靴・1脚の実物適合確認を含む）と、合格後の残数を別欄にし、
同じ論理部品の向き違い・一体化前の候補・工具を印刷数へ二重計上しないために使う。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JSON = ROOT / "docs/print-first-manifest.json"
DEFAULT_MD = ROOT / "docs/print-first-manifest.md"
FEET_DIR = ROOT / "outputs/print-first-20260905/feet"
BODY_DIR = ROOT / "outputs/print-first-20260905/body"
ADAPTER_DIR = ROOT / "outputs/print-first-20260905/ld220-adapter"

sys.path.insert(0, str(ROOT / "hardware" / "src"))
import config as CONFIG  # noqa: E402

# The generated body/legs records carry their own PLA upper bound.  Feet
# assembly.json currently records volume and material but not a mass field, so
# the same material rule is applied here and serialized with the manifest.
# These are reference densities for solid-volume conversion, not slicer or
# stock measurements.
MATERIAL_DENSITY_G_PER_CM3 = {
    "PLA": CONFIG.material_density_g_cm3("PLA"),
    "TPU95A": CONFIG.material_density_g_cm3("TPU"),
}
CURRENT_PRINTABLE_STATUS = (
    "FREEZE2_AND_PHYSICAL_STOCK_GATE_PENDING"
)
# These are comparison baselines from the current print-first decision.  The
# live quantities below are always derived from body/legs/feet assembly.json;
# if a later mechanical freeze intentionally adds or removes a printed relief
# part, update this baseline and the generated initial/remaining layers in the
# same reviewed change instead of silently retaining the old count.
COMPARISON_QUANTITY_BASELINE = {
    # The two generated print-first relief shells (head and central camera
    # pod) are part of the adopted candidate and therefore increase the
    # previous 54-part comparison by two.  Live counts below still come from
    # assembly manifests; these values only document the reviewed baseline.
    "machine_design_required": 56,
    "initial_prototype": 31,
    "remaining_after_prototype": 25,
    "conditional_machine_maximum": 60,
}
# Keep the previous 54/58 review values visible when the generated assembly
# intentionally adds the two camera/head clearance relief parts.  These are
# comparison values only; they must never drive the live quantity layers.
HISTORICAL_COMPARISON_QUANTITY_BASELINE = {
    "machine_design_required": 54,
    "initial_prototype": 29,
    "remaining_after_prototype": 25,
    "conditional_machine_maximum": 58,
}

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve()))


def quantity(total, prototype, *, group=None, exclusive_group=None):
    if int(total) < 0 or int(prototype) < 0 or int(prototype) > int(total):
        raise ValueError(f"invalid quantity total={total} prototype={prototype}")
    row = {"total": int(total), "prototype": int(prototype),
           "remaining_after_prototype": int(total - prototype)}
    if group:
        row["count_group"] = group
    if exclusive_group:
        row["exclusive_group"] = exclusive_group
    return row


def _release_layers_from_components(
    machine_components, *, shell_quantity, current_status=CURRENT_PRINTABLE_STATUS
):
    """Derive the A/B/C release contract from the real component rows.

    ``machine_components`` is built from the body/legs/feet assembly rows below.
    Keeping the release quantities as a projection of those rows prevents the
    prose labels (especially the 31/25 split) from becoming an independent,
    stale source of truth after freeze2.
    """

    def summed(scope, field):
        return sum(
            int(component[field])
            for component in machine_components
            if component["scope"] == scope
        )

    def named(name, field):
        matches = [
            int(component[field])
            for component in machine_components
            if component["logical_name"] == name
        ]
        if len(matches) != 1:
            raise ValueError(
                f"release quantity requires one {name!r} component row; got {len(matches)}"
            )
        return matches[0]

    design = {
        "body": summed("body", "design_quantity"),
        "legs": summed("legs", "design_quantity"),
        "tpu_shoes": named("tpu_shoe", "design_quantity"),
        "pla_spacers": named("pla_spacer", "design_quantity"),
    }
    prototype = {
        "body": summed("body", "prototype_quantity"),
        "legs": summed("legs", "prototype_quantity"),
        "tpu_shoes": named("tpu_shoe", "prototype_quantity"),
        "pla_spacers": named("pla_spacer", "prototype_quantity"),
    }
    remaining = {
        key: design[key] - prototype[key]
        for key in design
    }
    design_total = sum(design.values())
    prototype_total = sum(prototype.values())
    remaining_total = sum(remaining.values())
    if prototype_total + remaining_total != design_total:
        raise ValueError("release quantity rows do not reconcile A+B with the design total")
    underfoot_prototype = prototype["tpu_shoes"] + prototype["pla_spacers"]
    underfoot_remaining = remaining["tpu_shoes"] + remaining["pla_spacers"]
    if underfoot_prototype != 5:
        raise ValueError(
            "the reviewed initial prototype must contain one shoe plus four spacers"
        )

    return {
        "A_minimum_fit_prototype": {
            "title": "初回全体仮組み（1靴・1脚の実物適合確認を含む）",
            "quantity": prototype_total,
            "breakdown": {
                "body": prototype["body"],
                "unique_leg_parts": prototype["legs"],
                "tpu_shoe": prototype["tpu_shoes"],
                "pla_spacers": prototype["pla_spacers"],
                "underfoot": underfoot_prototype,
                "total": prototype_total,
            },
            "purpose": (
                f"全体仮組み用body{prototype['body']}＋左右/前後の固有脚部品"
                f"{prototype['legs']}＋1脚分足裏{underfoot_prototype}"
                f"（TPU靴{prototype['tpu_shoes']}＋スペーサー{prototype['pla_spacers']}）"
            ),
            "fit_scope": {"tpu_shoe": 1, "leg": 1},
            "source_quantity_layer": "machine_design_required.prototype_quantity",
            "included_in_initial_prototype_quantity": True,
            "currently_printable_quantity": 0,
            "currently_printable_status": current_status,
            "release_rule": "freeze2・足の厳密検査・材料/現物確認を確認してから先行判定する",
        },
        "B_remaining_after_prototype": {
            "title": "A合格後の残数",
            "quantity": remaining_total,
            "breakdown": {
                "body": remaining["body"],
                "unique_leg_parts": remaining["legs"],
                "tpu_shoe": remaining["tpu_shoes"],
                "pla_spacers": remaining["pla_spacers"],
                "underfoot": underfoot_remaining,
                "total": remaining_total,
            },
            "source_quantity_layer": "machine_design_required.remaining_after_prototype",
            "currently_printable_quantity": 0,
            "currently_printable_status": current_status,
            "release_rule": "Aの実物適合・電装・最終CAD/組立・実C++物理試験の合格後に解放する",
        },
        "C_conditional_new_shin_shell": {
            "title": "条件付き新規脛殻",
            "quantity": int(shell_quantity),
            "source_quantity_layer": "conditional_new_shin_shell.quantity",
            "exclusive_with": {
                "id": "existing_shin_shell_reuse",
                "quantity": int(shell_quantity),
                "rule": "既存脛殻4個の局所加工・再使用と新規印刷を同時に数えない",
            },
            "excluded_from_A_and_B": True,
            "maximum_machine_total": design_total + int(shell_quantity),
            "currently_printable_quantity": 0,
            "currently_printable_status": current_status,
            "release_rule": "既存脛殻4個の局所加工・再使用が不成立の場合だけ選ぶ",
        },
        "invariant": {
            "A_plus_B_equals_machine_design_required": True,
            "machine_design_required": design_total,
            "A_plus_B": prototype_total + remaining_total,
            "C_is_exclusive_with_existing_shell_reuse": True,
            "C_is_not_added_to_A_plus_B": True,
        },
    }


def _finite_nonnegative(value, *, label):
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be finite and non-negative: {value!r}")
    return value


def _material_rule_for_name(name: str) -> dict:
    """Resolve the longest explicit print-first material prefix.

    Structural ``pf_*`` parts must resolve through
    ``hardware/src/config.py:PRINT_FIRST_MATERIALS``.  The generated feet and
    retained shin shells have their own explicit print-first rules because
    their names do not use the ``pf_`` prefix.
    """

    matches = [
        (prefix, spec) for prefix, spec in CONFIG.PRINT_FIRST_MATERIALS.items()
        if name.startswith(prefix)
    ]
    if matches:
        prefix, spec = max(matches, key=lambda item: len(item[0]))
        material, wall_mm, infill_fraction = spec
        source = "hardware/src/config.py:PRINT_FIRST_MATERIALS"
        config_key = f"PRINT_FIRST_MATERIALS[{prefix!r}]"
        rule_status = "CONFIG_DERIVED_LONGEST_PREFIX"
    else:
        aliases = (
            ("tpu_shoe", "TPU95A", 2.4, 1.0),
            ("pla_spacer", "PLA", 2.4, 1.0),
            ("shin_shell_retained", "PLA", 1.4, 0.08),
            ("ld220_cradle", "PLA", 2.4, 1.0),
            ("ld220_cap", "PLA", 2.4, 1.0),
            ("ld220_horn_adapter", "PLA", 2.4, 1.0),
        )
        alias = next((row for row in aliases if name.startswith(row[0])), None)
        if alias is None:
            if name.startswith("pf_"):
                raise ValueError(
                    f"{name}: print-first material rule is missing from "
                    "config.PRINT_FIRST_MATERIALS"
                )
            raise ValueError(f"{name}: no explicit print-first material rule")
        prefix, material, wall_mm, infill_fraction = alias
        source = "tools/make_print_first_manifest.py:explicit non-pf print-first rule"
        config_key = f"EXPLICIT_PRINT_FIRST_NON_PF[{prefix!r}]"
        rule_status = "EXPLICIT_NON_PF_PRINT_FIRST_RULE"

    material = str(material)
    wall_mm = _finite_nonnegative(wall_mm, label=f"{name}.wall_mm")
    infill_fraction = float(infill_fraction)
    if wall_mm <= 0.0 or not math.isfinite(infill_fraction) or not 0.0 <= infill_fraction <= 1.0:
        raise ValueError(f"{name}: invalid print rule {material}/{wall_mm}/{infill_fraction}")
    density_key = "TPU" if material == "TPU95A" else material
    try:
        density = float(CONFIG.material_density_g_cm3(density_key))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{name}: unsupported material density {material}") from exc
    density = _finite_nonnegative(density, label=f"{name}.density_g_cm3")
    return {
        "config_key": config_key,
        "matched_prefix": prefix,
        "material": material,
        "wall_mm": wall_mm,
        "infill_fraction": infill_fraction,
        "infill_percent": infill_fraction * 100.0,
        "density_g_cm3": density,
        "source": source,
        "source_sha256": sha256(ROOT / "hardware" / "src" / "config.py"),
        "status": rule_status,
    }


def _mesh_metrics(path: Path, *, rule: dict, label: str) -> dict:
    """Return geometry and both mass models for one serialized STL.

    ``estimated_print_mass_g`` follows ``tools/export_urdf.py``'s
    ``estimate_mass_g`` expression (surface area × wall plus infill).  The
    solid-volume value is retained separately as a complete-fill upper bound.
    """

    import numpy as np
    import trimesh

    mesh = trimesh.load(path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"{label}: STL did not load as one mesh")
    if len(mesh.vertices) < 4 or len(mesh.faces) < 4:
        raise ValueError(f"{label}: STL is empty")
    if not np.isfinite(mesh.vertices).all():
        raise ValueError(f"{label}: STL has non-finite vertices")
    if not mesh.is_watertight or not mesh.is_winding_consistent:
        raise ValueError(f"{label}: STL is not a watertight consistently wound volume")
    volume_mm3 = _finite_nonnegative(mesh.volume, label=f"{label}.solid_volume_mm3")
    area_mm2 = _finite_nonnegative(mesh.area, label=f"{label}.surface_area_mm2")
    wall_volume_mm3 = min(volume_mm3, area_mm2 * rule["wall_mm"])
    printed_volume_mm3 = wall_volume_mm3 + (
        volume_mm3 - wall_volume_mm3
    ) * rule["infill_fraction"]
    printed_volume_mm3 = _finite_nonnegative(
        printed_volume_mm3, label=f"{label}.estimated_print_volume_mm3"
    )
    density = rule["density_g_cm3"]
    estimated_print_mass_g = printed_volume_mm3 * density / 1000.0
    solid_mass_upper_bound_g = volume_mm3 * density / 1000.0
    return {
        "surface_area_mm2": area_mm2,
        "solid_volume_mm3": volume_mm3,
        "wall_volume_mm3": wall_volume_mm3,
        "estimated_print_volume_mm3": printed_volume_mm3,
        "estimated_print_mass_g": _finite_nonnegative(
            estimated_print_mass_g, label=f"{label}.estimated_print_mass_g"
        ),
        "solid_mass_upper_bound_g": _finite_nonnegative(
            solid_mass_upper_bound_g, label=f"{label}.solid_mass_upper_bound_g"
        ),
        "mass_model": "tools/export_urdf.py:estimate_mass_g (surface area × wall + remaining volume × infill)",
        "geometry_status": "SERIALIZED_STL_WATERTIGHT_FINITE_VOLUME",
    }


def _stock_note(material: str) -> str:
    if material == "TPU95A":
        return "手持ちTPU95A（残量未確認。支持材・失敗分は別）"
    return f"手持ち{material}（色自由・残量未確認。支持材・失敗分は別）"


def _format_print_rule(rule: dict) -> str:
    return (
        f"{rule['material']} / 壁{rule['wall_mm']:g}mm / "
        f"充填率{rule['infill_percent']:g}%（{rule['config_key']}）"
    )


def _leg_ids(legs):
    ids = sorted({leg for part in legs.get("parts", []) for leg in part.get("legs", [])})
    if not ids:
        ids = sorted(legs.get("legs", {}))
    if not ids:
        raise ValueError("legs assembly has no leg identity")
    return ids


def _fixture_catalog(excluded):
    """Describe optional LD fit tools without making their STL rows production rows."""
    by_id = {row["id"]: row for row in excluded}
    common = {
        "quantity": 1,
        "currently_printable_quantity": 0,
        "material": "手持ちPLA（残量未確認）",
        "settings": "寸法確認用。0.20 mm候補、壁数/充填率はA.mass・スライサー未確定",
        "status": CURRENT_PRINTABLE_STATUS,
        "disposition": "本番構成へ組み込まず、寸法・軸端・ホーン適合を確認した後も廃棄せず治具として保管",
        "purpose": "記録上の候補14個のうち現物未確認の対象でケース外形・軸端・ホーン径/PCD・ねじを確認する任意試験具",
    }
    specs = (
        ("ld220_cradle", "EX-LD220-LD220_CRADLE.STL", "基準面をベッドへ、ケース挿入口（+Z側）を上"),
        ("ld220_cap", "EX-LD220-LD220_CAP.STL", "皿穴側（+Z側）を上"),
        ("ld220_horn_adapter", "EX-LD220-LD220_HORN_ADAPTER.STL", "ホーン受け面/ポケット側（+Z側）を上"),
    )
    result = []
    for item_id, source_id, orientation in specs:
        if source_id not in by_id:
            raise ValueError(f"fixture source missing from excluded rows: {source_id}")
        row = dict(common)
        row.update({
            "id": item_id,
            "source_excluded_id": source_id,
            "path": by_id[source_id]["path"],
            "sha256": by_id[source_id]["sha256"],
            "material_rule": by_id[source_id].get("material_rule"),
            "geometry_metrics": by_id[source_id].get("geometry_metrics"),
            "print_rule_text": by_id[source_id].get("print_rule_text"),
            "orientation": orientation,
        })
        result.append(row)
    return result


def mass_reference(*, per_instance_g=None, implemented_quantity=None,
                   implemented_total_g=None, printed_quantity=None,
                   material, basis, status="REFERENCE_ONLY",
                   geometry_metrics=None, material_rule=None):
    """印刷数/実装数の質量を、スライサー確定前の参考値として記録する。"""
    row = {
        "per_instance_g": per_instance_g,
        "implemented_quantity": implemented_quantity,
        "implemented_total_g": implemented_total_g,
        "printed_quantity": printed_quantity,
        "material": material,
        "basis": basis,
        "status": status,
    }
    if geometry_metrics is not None:
        row["geometry_metrics"] = geometry_metrics
        row["estimated_print_mass_g_per_instance"] = geometry_metrics[
            "estimated_print_mass_g"
        ]
        row["solid_mass_upper_bound_g_per_instance"] = geometry_metrics[
            "solid_mass_upper_bound_g"
        ]
    if material_rule is not None:
        row["material_rule"] = material_rule
    return row


def assembly_reference_record(*, item_id, label, path, material, orientation,
                              reason, source_assembly):
    """印刷数へ含めない、組立座標確認用の同形状STLを記録する。"""
    path = Path(path)
    return {
        "id": item_id,
        "label": label,
        "path": rel(path),
        "sha256": sha256(path),
        "material": material,
        "quantity": quantity(0, 0),
        "orientation": orientation,
        "settings": "印刷数0。組立座標/嵌合確認用で、印刷向き候補と二重計上しない",
        "replaces_old_parts": [],
        "pre_mass_print_verification": reason,
        "role": "assembly_reference",
        "source_assembly": source_assembly,
    }


def pending_record(*, item_id, label, path, material, reason, source_assembly,
                   design_qty=0, design_prototype=0):
    """最終CAD/材料設定の凍結後に印刷可否を決める候補。

    ``quantity`` は設計上の必要数を保持し、別フィールドの
    ``currently_printable_quantity`` を0として、数量を保留状態に隠さない。
    """
    path = Path(path)
    design_quantity = quantity(design_qty, design_prototype)
    return {
        "id": item_id,
        "label": label,
        "path": rel(path),
        "sha256": sha256(path),
        "material": material,
        "quantity": design_quantity,
        "design_required_quantity": design_quantity.copy(),
        "currently_printable_quantity": 0,
        "currently_printable_status": CURRENT_PRINTABLE_STATUS,
        "orientation": "最終CAD凍結後にスライサーで確定",
        "settings": "configの材料・壁厚・充填率を反映済み。最終A.mass・実スライサー条件は再確認待ち",
        "replaces_old_parts": [],
        "pre_mass_print_verification": reason,
        "role": "pending_integration_candidate",
        "source_assembly": source_assembly,
    }


def stl_record(*, item_id, label, path, material, qty, prototype, orientation,
               settings, replaces, gate, role="printable_candidate", **extra):
    path = Path(path)
    design_quantity = quantity(qty, prototype, **extra.pop("quantity_options", {}))
    row = {
        "id": item_id,
        "label": label,
        "path": rel(path),
        "sha256": sha256(path),
        "material": material,
        "quantity": design_quantity,
        "design_required_quantity": design_quantity.copy(),
        "currently_printable_quantity": 0,
        "currently_printable_status": CURRENT_PRINTABLE_STATUS,
        "orientation": orientation,
        "settings": settings,
        "replaces_old_parts": list(replaces),
        "pre_mass_print_verification": gate,
        "role": role,
    }
    row.update(extra)
    return row


def load_json(path):
    return json.loads(path.read_text())


def build_manifest():
    feet = load_json(FEET_DIR / "assembly.json")
    body = load_json(BODY_DIR / "assembly.json")
    legs = load_json(ROOT / "outputs/print-first-20260905/legs/assembly.json")
    feet_outputs = feet["outputs"]
    body_parts = {part["name"]: part for part in body["parts"]}
    leg_ids = _leg_ids(legs)
    shoe_quantity = len(leg_ids)
    spacer_variant_names = [
        part["name"] for part in feet.get("parts", [])
        if part.get("name", "").startswith("pla_spacer_")
    ]
    if not spacer_variant_names:
        raise ValueError("feet assembly has no spacer variants")
    spacer_quantity = len(spacer_variant_names) * shoe_quantity
    shell_rows = {
        part["name"]: part for part in feet.get("parts", [])
        if part.get("name", "").startswith("shin_shell_retained")
    }
    shell_quantity_by_name = {
        name: len(part.get("legs", [])) or 1
        for name, part in shell_rows.items()
    }
    shell_quantity = sum(shell_quantity_by_name.values())
    if shell_quantity != shoe_quantity:
        raise ValueError(
            f"shell quantity {shell_quantity} does not cover leg count {shoe_quantity}"
        )
    body_quantity_by_name = {name: 1 for name in body_parts}
    legs_quantity_by_name = {
        part["name"]: len(part.get("legs", [])) or 1
        for part in legs.get("parts", [])
    }
    rows = []

    shoe = feet_outputs["tpu_shoe_print"]
    rows.append(stl_record(
        item_id="PF-FOOT-TPU", label="三葉TPU靴", path=ROOT / shoe["path"],
        material="手持ちTPU95A黒（残量未測定）",
        qty=shoe_quantity, prototype=min(1, shoe_quantity),
        orientation="印刷面を床、耳を上。穴軸を傾けずこの印刷向きを使用",
        settings="0.20mm / 4周壁 / 100%充填（候補。材料試験前）",
        replaces=["foot_pad", "Leg_Toe_Black_x12"],
        gate="幾何候補を確認済み。1靴の嵌合・接地・荷重・TPU耳5.24%ひずみを実物確認し、最終sim接触力を比較"))

    spacer = feet_outputs["pla_spacer_print"]
    rows.append(stl_record(
        item_id="PF-FOOT-SPACER-PRINT", label="段付きPLAスペーサー（印刷向き）",
        path=ROOT / spacer["path"], material="手持ち構造PLA・色自由（残量未測定）",
        qty=spacer_quantity, prototype=min(len(spacer_variant_names), spacer_quantity),
        orientation=f"フランジ面を床、穴を縦。単一部品を{shoe_quantity}脚×上下左右へ配置",
        settings="0.20mm / 4周壁 / 100%充填（候補。実ねじ適合前）",
        replaces=[],
        gate=(f"1脚分{len(spacer_variant_names)}個の実ねじ・ナット・殻との適合を確認してから"
              f"残り{spacer_quantity - min(len(spacer_variant_names), spacer_quantity)}個")))

    assembly_refs = []
    assembly_refs.append(assembly_reference_record(
        item_id="REF-FOOT-TPU-FRAME", label="三葉TPU靴（foot-frame組立参照）",
        path=ROOT / feet_outputs["tpu_shoe"]["path"], material="手持ちTPU95A黒（残量未測定）",
        orientation="足フレーム座標。印刷はtpu_shoe_print.stlを使用",
        reason="印刷向きSTLとの同一論理部品の重複を避け、組立座標だけを確認",
        source_assembly=rel(FEET_DIR / "assembly.json")))
    for name in ("pla_spacer_positive_y", "pla_spacer_negative_y",
                 "pla_spacer_upper_positive_y", "pla_spacer_upper_negative_y"):
        assembly_refs.append(assembly_reference_record(
            item_id="REF-FOOT-SPACER-" + name.removeprefix("pla_spacer_").upper(),
            label=name + "（foot-frame組立参照）", path=ROOT / feet_outputs[name]["path"],
            material="手持ち構造PLA・色自由（残量未測定）", orientation="足フレーム座標。印刷はpla_spacer_print.stlを使用",
            reason="同一solidの配置/回転を確認する参照。本番数量は印刷向き単一部品16個だけ",
            source_assembly=rel(FEET_DIR / "assembly.json")))

    # 青殻は既存4個の局所加工と新規4個の印刷を同時に数えない。
    for name, label, prototype in (
        ("shin_shell_retained_print", "青殻標準・新規印刷の排他的候補", 1),
        ("shin_shell_retained_m_print", "青殻鏡像・新規印刷の排他的候補", 0),
    ):
        rec = feet_outputs[name]
        shell_legs = list(shell_rows[name.removesuffix("_print")].get("legs", []))
        rows.append(stl_record(
            item_id="PF-SHELL-NEW-" + ("STD" if name == "shin_shell_retained_print" else "MIRROR"),
            label=label, path=ROOT / rec["path"],
            material="手持ち構造PLA・色自由（推奨青等、残量未測定）",
            qty=len(shell_legs),
            prototype=prototype, orientation="元STLの印刷向きを維持（局所加工版）",
            settings="上下ボルト座面周囲を局所加工、全長/外形を保持。壁数/充填は現物・A.mass確認待ち",
            replaces=["shin_shell"],
            gate="1脚の殻・耳・ねじ座の適合と印刷状態を確認してから残数",
            quantity_options={"group": "blue_shell", "exclusive_group": "blue_shell_source"},
            legs=shell_legs, selection="既存加工版を選ぶ場合はこの新規印刷候補を全数不採用"))

    for name, label, prototype in (
        ("shin_shell_retained", "青殻標準・既存局所加工の排他的候補", 1),
        ("shin_shell_retained_m", "青殻鏡像・既存局所加工の排他的候補", 0),
    ):
        rec = feet_outputs[name]
        shell_legs = list(shell_rows[name].get("legs", []))
        rows.append(stl_record(
            item_id="PF-SHELL-EXISTING-" + ("STD" if name == "shin_shell_retained" else "MIRROR"),
            label=label, path=ROOT / rec["path"],
            material="手持ち構造PLA・色自由（推奨青等、現物/残量未確認）",
            qty=len(shell_legs),
            prototype=prototype, orientation="既存印刷物を加工するため新規印刷しない",
            settings="上下ボルト座面周囲を局所加工、全長/外形を保持。既存印刷条件は未確認",
            replaces=["shin_shell"],
            gate="現物4個の版・寸法・加工後の嵌合を確認してから使用",
            role="existing_part_processing_candidate",
            quantity_options={"group": "blue_shell", "exclusive_group": "blue_shell_source"},
            legs=shell_legs, selection="新規印刷候補を選ぶ場合はこの既存加工候補を全数不採用"))

    # body の parts だけを採用候補とし、単体出力の post は一体化済みとして除外する。
    body_material = {
        name: _material_rule_for_name(name)["material"]
        for name in body_parts
    }
    # 新yaw蓋はbody assemblyに生成されるが、最終CAD/材料/壁数を凍結するまで
    # 本番候補へ昇格しない。単体出力のpostは従来どおり除外する。
    pending = []
    pending_body_names = {name for name in body_parts if name.startswith("pf_ld220_yaw_cap_")}
    for name, part in body_parts.items():
        if name in pending_body_names:
            pending.append(pending_record(
                item_id="PENDING-BODY-" + name.removeprefix("pf_").upper(),
                label=name + "（最終凍結後候補）", path=ROOT / part["stl"],
                material=body_material[name],
                reason="新yaw蓋。configの材料・壁厚・充填率は反映済み。最終body/legs統合、A.mass、干渉を確認してから本番数を決める",
                source_assembly=rel(BODY_DIR / "assembly.json"),
                design_qty=body_quantity_by_name[name],
                design_prototype=min(1, body_quantity_by_name[name])))
            continue
        rows.append(stl_record(
            item_id="PF-BODY-" + name.removeprefix("pf_").upper(),
            label=name, path=ROOT / part["stl"], material=body_material.get(name, "要確定"),
            qty=body_quantity_by_name[name], prototype=min(1, body_quantity_by_name[name]),
            orientation="STL原点を保持。実部品を置いたスライサー画面で最終確認",
            settings="configの材料・壁厚・充填率を反映済み。最終A.mass・実スライサー条件は再確認待ち",
            replaces=part.get("replaces", []),
            gate="A.massの材料/壁数/充填率と一致させ、全体固定部品・頭/Cabin・配線・干渉を再検査",
            source_assembly=rel(BODY_DIR / "assembly.json")))

    for part in legs.get("parts", []):
        name = part["name"]
        leg_quantity = legs_quantity_by_name[name]
        pending.append(pending_record(
            item_id="PENDING-LEGS-" + name.upper(), label=name + "（最終凍結後候補）",
            path=ROOT / part.get("stl", part.get("path")),
            material=_material_rule_for_name(name)["material"],
            reason="新LD統合脚部品。configの材料・壁厚・充填率は反映済み。最終CAD、実LD個体適合、A.mass、全脚干渉を確認してから本番数を決める",
            source_assembly=rel(ROOT / "outputs/print-first-20260905/legs/assembly.json"),
            design_qty=leg_quantity, design_prototype=min(1, leg_quantity)))

    excluded = []
    def excluded_file(item_id, label, path, reason):
        path = Path(path)
        if path.exists():
            excluded.append({"id": item_id, "label": label, "path": rel(path),
                             "sha256": sha256(path), "reason": reason})

    for name, reason in (
        ("DO_NOT_PRINT_shell_relief_tool_foot_frame.stl", "殻切削用の工具STL。印刷部品ではない"),
        ("shell_relief_tool_envelope_foot_frame.stl", "殻切削包絡の工具STL。印刷部品ではない"),
    ):
        excluded_file("EX-FOOT-" + name.upper(), name, FEET_DIR / name, reason)
    for name in ("pf_electronics_post_l.stl", "pf_electronics_post_r.stl"):
        excluded_file("EX-BODY-" + name.upper(), name, BODY_DIR / name,
                      "pf_cabin_rail_l/rへ一体化済み。単体印刷すると棚柱を二重計上する")
    for name in ("ld220_cradle.stl", "ld220_horn_adapter.stl", "ld220_cap.stl"):
        excluded_file("EX-LD220-" + name.upper(), name, ADAPTER_DIR / name,
                      "LDケージ/ホーン候補。実LDの軸端・主面・ホーン径PCDを実測する適合試験用として任意1組まで候補にできるが、寸法確定後に一体化案が不要なら印刷しない。本番数量0")
    for name in ("pf_ld220_knee_cap.stl", "pf_ld220_knee_cap_m.stl",
                 "pf_ld220_pitch_cap.stl", "pf_ld220_pitch_cap_m.stl"):
        excluded_file("EX-LEGS-" + name.upper(), name, ROOT / "outputs/print-first-20260905/legs" / name,
                      "legs/assembly.jsonに未収録の旧/未採用出力。本番数量0で隔離し、最終CAD凍結後に必要性を再評価")

    # Every serialized production/reference row receives the same explicit
    # rule and mesh-derived metrics.  Keep the source assembly name separate
    # from the human stock note so a pending gate cannot erase a design rule.
    logical_name_by_path = {}
    for name, part in body_parts.items():
        logical_name_by_path[part["stl"]] = name
    for part in legs.get("parts", []):
        logical_name_by_path[part["path"]] = part["name"]
    for key, part in feet_outputs.items():
        logical_name_by_path[part["path"]] = key.removesuffix("_print")
    for item in excluded:
        path = Path(item["path"])
        if path.parent.name == ADAPTER_DIR.name:
            logical_name_by_path[item["path"]] = path.stem
    # Reconcile the generated assembly's complete-fill mass/volume fields with
    # the serialized STL that this ledger actually reads.  A stale assembly
    # number must stop generation instead of silently becoming the public
    # reference value.
    assembly_geometry_by_path = {}
    for part in [*body.get("parts", []), *legs.get("parts", [])]:
        source_path = part.get("stl") or part.get("path")
        if not source_path:
            continue
        assembly_geometry_by_path[source_path] = {
            "volume_mm3": _finite_nonnegative(
                part.get("volume_mm3"), label=f"{source_path}.assembly_volume_mm3"
            ),
            "solid_mass_upper_bound_g": _finite_nonnegative(
                part.get("solid_pla_g_upper_bound"),
                label=f"{source_path}.assembly_solid_mass_upper_bound_g",
            ),
        }
    metrics_cache = {}
    rules_cache = {}

    def annotate_row(row):
        logical_name = logical_name_by_path.get(row["path"])
        if logical_name is None:
            return
        rule = rules_cache.setdefault(
            logical_name, _material_rule_for_name(logical_name)
        )
        path = ROOT / row["path"]
        metrics = metrics_cache.setdefault(
            row["path"],
            _mesh_metrics(path, rule=rule, label=logical_name),
        )
        row["material"] = rule["material"]
        row["logical_part_name"] = logical_name
        row["stock_material_note"] = _stock_note(rule["material"])
        row["material_rule"] = rule
        row["geometry_metrics"] = metrics
        row["print_rule_text"] = _format_print_rule(rule)
        assembly_geometry = assembly_geometry_by_path.get(row["path"])
        if assembly_geometry is not None:
            if not math.isclose(
                assembly_geometry["volume_mm3"],
                metrics["solid_volume_mm3"],
                rel_tol=1.0e-6,
                abs_tol=0.01,
            ):
                raise ValueError(
                    f"{logical_name}: assembly volume does not match serialized STL"
                )
            if not math.isclose(
                assembly_geometry["solid_mass_upper_bound_g"],
                metrics["solid_mass_upper_bound_g"],
                rel_tol=1.0e-6,
                abs_tol=1.0e-4,
            ):
                raise ValueError(
                    f"{logical_name}: assembly solid mass does not match serialized STL"
                )
            row["assembly_geometry_reconciled"] = True
            row["assembly_volume_mm3"] = assembly_geometry["volume_mm3"]
            row["assembly_solid_mass_upper_bound_g"] = assembly_geometry[
                "solid_mass_upper_bound_g"
            ]
        if row.get("role") != "assembly_reference":
            row["settings"] = (
                f"{_format_print_rule(rule)}。実スライサーの積層・支持材・"
                "失敗分は未確認"
            )

    for row in [*rows, *assembly_refs, *pending, *excluded]:
        annotate_row(row)

    # 数量は生成済みassemblyの部品・脚対応から算出する。bodyはparts各1個、
    # legsは各partのlegs対応数、靴・スペーサー・殻は同じ脚対応から導出する。
    # pending行のquantity=0で本番設計数を隠さない。
    body_required_quantity = sum(body_quantity_by_name.values())
    legs_required_quantity = sum(legs_quantity_by_name.values())
    body_prototype_quantity = sum(
        min(1, quantity) for quantity in body_quantity_by_name.values()
    )
    legs_prototype_quantity = sum(
        min(1, quantity) for quantity in legs_quantity_by_name.values()
    )
    shoe_prototype_quantity = min(1, shoe_quantity)
    spacer_prototype_quantity = min(len(spacer_variant_names), spacer_quantity)
    machine_required_total = (
        body_required_quantity + legs_required_quantity + shoe_quantity + spacer_quantity
    )
    machine_prototype_total = (
        body_prototype_quantity + legs_prototype_quantity
        + shoe_prototype_quantity + spacer_prototype_quantity
    )
    machine_remaining_total = machine_required_total - machine_prototype_total
    design_mass_layer_key = f"machine_design_required_{machine_required_total}"
    prototype_mass_layer_key = f"initial_prototype_{machine_prototype_total}"
    remaining_mass_layer_key = f"remaining_after_prototype_{machine_remaining_total}"
    production_quantity_layers = {
        "machine_design_required": {
            "body": body_required_quantity,
            "legs": legs_required_quantity,
            "tpu_shoes": shoe_quantity,
            "pla_spacers": spacer_quantity,
            "total": machine_required_total,
            "prototype_quantity": {
                "body": body_prototype_quantity,
                "legs": legs_prototype_quantity,
                "tpu_shoes": shoe_prototype_quantity,
                "pla_spacers": spacer_prototype_quantity,
                "total": machine_prototype_total,
            },
            "remaining_after_prototype": {
                "body": body_required_quantity - body_prototype_quantity,
                "legs": legs_required_quantity - legs_prototype_quantity,
                "tpu_shoes": shoe_quantity - shoe_prototype_quantity,
                "pla_spacers": spacer_quantity - spacer_prototype_quantity,
                "total": machine_required_total - machine_prototype_total,
            },
            "basis": {
                "body": "body/assembly.jsonのpartsを各1個（yaw蓋を含む）",
                "legs": "legs/assembly.jsonの各partのlegs対応数を合計",
                "tpu_shoes": "脚IDのユニーク数",
                "pla_spacers": f"feet/assembly.jsonの{len(spacer_variant_names)} spacer variants × 脚ID数",
            },
            "currently_printable_quantity": 0,
            "currently_printable_status": CURRENT_PRINTABLE_STATUS,
        },
        "conditional_new_shin_shell": {
            "quantity": shell_quantity,
            "exclusive_with": f"既存脛殻{shell_quantity}個の局所加工・再使用",
            "maximum_machine_total": machine_required_total + shell_quantity,
            "currently_printable_quantity": 0,
            "currently_printable_status": CURRENT_PRINTABLE_STATUS,
            "basis": "feet/assembly.jsonの標準・鏡像shellの脚対応数。既存加工案と新規印刷案を合計しない",
        },
        "non_production_ld_fit_fixtures": _fixture_catalog(excluded),
        "counting_rule": (
            f"設計必要{machine_required_total}（初回内数{machine_prototype_total}、適合後残り{machine_required_total - machine_prototype_total}）、"
            f"既存殻を加工できない場合のみ新規殻{shell_quantity}（最大"
            f"{body_required_quantity + legs_required_quantity + shoe_quantity + spacer_quantity + shell_quantity}）。"
            "LD治具3種各1は本番構成へ組み込まず本番数へ加算しない。"
            "一体化済みケージ/蓋/ホーン変換板を二重計上しない。"
        ),
    }
    if machine_required_total != COMPARISON_QUANTITY_BASELINE["machine_design_required"]:
        raise ValueError(
            "generated assembly quantity differs from the reviewed comparison baseline; "
            "update COMPARISON_QUANTITY_BASELINE and the initial/remaining review together: "
            f"expected {COMPARISON_QUANTITY_BASELINE['machine_design_required']}, "
            f"got {production_quantity_layers['machine_design_required']['total']}"
        )
    if machine_prototype_total != COMPARISON_QUANTITY_BASELINE["initial_prototype"]:
        raise ValueError(
            "generated assembly prototype quantity differs from the reviewed comparison baseline; "
            "update COMPARISON_QUANTITY_BASELINE and the initial/remaining review together: "
            f"expected {COMPARISON_QUANTITY_BASELINE['initial_prototype']}, "
            f"got {machine_prototype_total}"
        )
    if machine_remaining_total != COMPARISON_QUANTITY_BASELINE["remaining_after_prototype"]:
        raise ValueError(
            "generated assembly remaining quantity differs from the reviewed comparison baseline; "
            "update COMPARISON_QUANTITY_BASELINE and the initial/remaining review together: "
            f"expected {COMPARISON_QUANTITY_BASELINE['remaining_after_prototype']}, "
            f"got {machine_remaining_total}"
        )
    if machine_required_total + shell_quantity != COMPARISON_QUANTITY_BASELINE["conditional_machine_maximum"]:
        raise ValueError(
            "generated assembly shell maximum differs from the reviewed comparison baseline; "
            "update COMPARISON_QUANTITY_BASELINE with the exclusive shell decision: "
            f"expected {COMPARISON_QUANTITY_BASELINE['conditional_machine_maximum']}, "
            f"got {machine_required_total + shell_quantity}"
        )

    # Mass references are derived from serialized geometry and the resolved
    # longest-prefix rule.  The two models are intentionally kept separate:
    # the export_urdf wall/infill estimate is a planning value, while a solid
    # fill is an upper bound.
    body_design_quantity = dict(body_quantity_by_name)
    leg_design_quantity = dict(legs_quantity_by_name)
    body_components = [
        {
            "scope": "body",
            "logical_name": name,
            "path": part["stl"],
            "design_quantity": body_design_quantity[name],
            "prototype_quantity": min(1, body_design_quantity[name]),
        }
        for name, part in body_parts.items()
    ]
    leg_components = [
        {
            "scope": "legs",
            "logical_name": part["name"],
            "path": part["path"],
            "design_quantity": leg_design_quantity[part["name"]],
            "prototype_quantity": min(1, leg_design_quantity[part["name"]]),
        }
        for part in legs.get("parts", [])
    ]
    shoe_component = {
        "scope": "feet",
        "logical_name": "tpu_shoe",
        "path": feet_outputs["tpu_shoe_print"]["path"],
        "design_quantity": shoe_quantity,
        "prototype_quantity": shoe_prototype_quantity,
    }
    spacer_component = {
        "scope": "feet",
        "logical_name": "pla_spacer",
        "path": feet_outputs["pla_spacer_print"]["path"],
        "design_quantity": spacer_quantity,
        "prototype_quantity": spacer_prototype_quantity,
    }
    machine_components = [*body_components, *leg_components,
                          shoe_component, spacer_component]
    for component in machine_components:
        component["remaining_quantity"] = (
            component["design_quantity"] - component["prototype_quantity"]
        )
    release_layers = _release_layers_from_components(
        machine_components,
        shell_quantity=shell_quantity,
    )
    release_invariant = release_layers["invariant"]
    if (
        release_invariant["machine_design_required"] != machine_required_total
        or release_invariant["A_plus_B"] != machine_required_total
    ):
        raise ValueError("release layers diverge from the production quantity totals")
    production_quantity_layers["release_layers"] = release_layers
    shell_components = [
        {
            "scope": "conditional_new_shin_shell",
            "logical_name": name,
            "path": feet_outputs[f"{name}_print"]["path"],
            "design_quantity": shell_quantity_by_name[name],
            "prototype_quantity": 0,
            "remaining_quantity": shell_quantity_by_name[name],
        }
        for name in shell_rows
    ]

    def mass_layer(components, quantity_key):
        by_material = {}
        by_scope = {}
        component_rows = []
        total_quantity = 0
        estimated_total = 0.0
        solid_total = 0.0
        for component in components:
            path = component["path"]
            logical_name = component["logical_name"]
            quantity_value = int(component[quantity_key])
            rule = rules_cache[logical_name]
            metrics = metrics_cache[path]
            estimated = metrics["estimated_print_mass_g"] * quantity_value
            solid = metrics["solid_mass_upper_bound_g"] * quantity_value
            total_quantity += quantity_value
            estimated_total += estimated
            solid_total += solid
            mat = rule["material"]
            material_row = by_material.setdefault(
                mat,
                {"estimated_print_mass_g": 0.0,
                 "solid_mass_upper_bound_g": 0.0,
                 "quantity": 0},
            )
            material_row["estimated_print_mass_g"] += estimated
            material_row["solid_mass_upper_bound_g"] += solid
            material_row["quantity"] += quantity_value
            scope_row = by_scope.setdefault(
                component["scope"],
                {"quantity": 0, "estimated_print_mass_g": 0.0,
                 "solid_mass_upper_bound_g": 0.0},
            )
            scope_row["quantity"] += quantity_value
            scope_row["estimated_print_mass_g"] += estimated
            scope_row["solid_mass_upper_bound_g"] += solid
            component_rows.append({
                "scope": component["scope"],
                "logical_name": logical_name,
                "path": path,
                "quantity": quantity_value,
                "material_rule": rule,
                "geometry_metrics": metrics,
                "estimated_print_mass_g": round(estimated, 6),
                "solid_mass_upper_bound_g": round(solid, 6),
            })
        return {
            "quantity": total_quantity,
            "estimated_print_mass_g": round(estimated_total, 6),
            "solid_mass_upper_bound_g": round(solid_total, 6),
            "estimated_print_mass_g_by_material": {
                mat: {
                    "quantity": values["quantity"],
                    "mass_g": round(values["estimated_print_mass_g"], 6),
                }
                for mat, values in sorted(by_material.items())
            },
            "solid_mass_upper_bound_g_by_material": {
                mat: {
                    "quantity": values["quantity"],
                    "mass_g": round(values["solid_mass_upper_bound_g"], 6),
                }
                for mat, values in sorted(by_material.items())
            },
            "by_scope": {
                scope: {
                    "quantity": values["quantity"],
                    "estimated_print_mass_g": round(values["estimated_print_mass_g"], 6),
                    "solid_mass_upper_bound_g": round(values["solid_mass_upper_bound_g"], 6),
                }
                for scope, values in sorted(by_scope.items())
            },
            "components": component_rows,
            "status": "REFERENCE_ONLY_SUPPORT_FAILURE_SLICER_AND_STOCK_UNVERIFIED",
        }

    mass_layers = {
        design_mass_layer_key: mass_layer(machine_components, "design_quantity"),
        prototype_mass_layer_key: mass_layer(machine_components, "prototype_quantity"),
        remaining_mass_layer_key: mass_layer(machine_components, "remaining_quantity"),
    }
    conditional_shell_mass = mass_layer(shell_components, "design_quantity")
    conditional_shell_mass_key = f"conditional_new_shin_shell_{shell_quantity}"
    existing_shell_mass_key = f"existing_shin_shell_reuse_{shell_quantity}"
    existing_shell_mass = mass_layer(
        [
            {
                **component,
                "logical_name": component["logical_name"],
                "path": feet_outputs[component["logical_name"]]["path"],
            }
            for component in shell_components
        ],
        "design_quantity",
    )

    def mass_ref_for(path, quantity_value, *, status):
        logical_name = logical_name_by_path[path]
        rule = rules_cache[logical_name]
        metrics = metrics_cache[path]
        return mass_reference(
            per_instance_g=round(metrics["estimated_print_mass_g"], 6),
            implemented_quantity=quantity_value,
            implemented_total_g=round(metrics["estimated_print_mass_g"] * quantity_value, 6),
            printed_quantity=quantity_value,
            material=rule["material"],
            basis="export_urdf.py estimate_mass_g と同じ表面積×壁厚＋残体積×充填率。solid_mass_upper_bound_gを別記",
            status=status,
            geometry_metrics=metrics,
            material_rule=rule,
        )

    mass_by_path = {}
    for component in [*machine_components, *shell_components]:
        mass_by_path[component["path"]] = mass_ref_for(
            component["path"], component["design_quantity"],
            status=("UNVERIFIED_NEW_SHELL_SLICER_PENDING"
                    if component["scope"] == "conditional_new_shin_shell"
                    else "REFERENCE_SLICER_AND_STOCK_PENDING"),
        )
    # Existing foot-frame shell rows reuse the same logical shell mass but add
    # no new print quantity; retain that distinction in the row reference.
    for name, component in shell_rows.items():
        path = feet_outputs[name]["path"]
        metrics = metrics_cache[path]
        rule = rules_cache[logical_name_by_path[path]]
        mass_by_path[path] = mass_reference(
            per_instance_g=round(metrics["estimated_print_mass_g"], 6),
            implemented_quantity=shell_quantity_by_name[name],
            implemented_total_g=round(
                metrics["estimated_print_mass_g"] * shell_quantity_by_name[name], 6
            ),
            printed_quantity=0,
            material=rule["material"],
            basis="export_urdf.py estimate_mass_g と同じ。既存脛殻を再使用し、新規印刷材料へ二重計上しない",
            status="REFERENCE_EXISTING_REUSE_NO_NEW_PRINT",
            geometry_metrics=metrics,
            material_rule=rule,
        )
    for row in [*rows, *assembly_refs, *pending]:
        if row.get("path") in mass_by_path:
            row["mass_reference"] = mass_by_path[row["path"]]

    body_reference_total = sum(
        metrics_cache[part["stl"]]["solid_mass_upper_bound_g"]
        * body_design_quantity[name]
        for name, part in body_parts.items()
    )
    legs_reference_total = sum(
        metrics_cache[part["path"]]["solid_mass_upper_bound_g"]
        * leg_design_quantity[part["name"]]
        for part in legs.get("parts", [])
    )
    tpu_shoe_each = metrics_cache[shoe_component["path"]]["solid_mass_upper_bound_g"]
    pla_spacer_each = metrics_cache[spacer_component["path"]]["solid_mass_upper_bound_g"]
    feet_reference_total = (
        tpu_shoe_each * shoe_quantity + pla_spacer_each * spacer_quantity
    )
    existing_shell_total = existing_shell_mass["solid_mass_upper_bound_g"]
    new_shell_total = conditional_shell_mass["solid_mass_upper_bound_g"]

    body_component_reference = []
    for component in body_components:
        rule = rules_cache[component["logical_name"]]
        metrics = metrics_cache[component["path"]]
        qty = component["design_quantity"]
        body_component_reference.append({
            "scope": "body",
            "manifest_id": "PF-BODY-" + component["logical_name"].removeprefix("pf_").upper(),
            "label": component["logical_name"],
            "path": component["path"],
            "quantity": qty,
            "material": rule["material"],
            "material_rule": rule,
            "geometry_metrics": metrics,
            "estimated_print_mass_g_per_instance": round(metrics["estimated_print_mass_g"], 6),
            "estimated_print_mass_g": round(metrics["estimated_print_mass_g"] * qty, 6),
            "solid_mass_upper_bound_g_per_instance": round(metrics["solid_mass_upper_bound_g"], 6),
            "solid_mass_upper_bound_g": round(metrics["solid_mass_upper_bound_g"] * qty, 6),
            "status": "REFERENCE_ONLY_SUPPORT_FAILURE_SLICER_AND_STOCK_UNVERIFIED",
        })
    leg_component_reference = []
    for kind, label in (("coxa", "coxaリンク"), ("femur", "femurリンク"),
                        ("tibia", "tibiaリンク")):
        components = [c for c in leg_components
                      if next(p for p in legs["parts"] if p["name"] == c["logical_name"])
                      .get("link_kind") == kind]
        if not components:
            raise ValueError(f"legs assembly has no {kind} parts")
        total = mass_layer(components, "design_quantity")
        leg_component_reference.append({
            "scope": "legs",
            "label": label,
            "quantity": total["quantity"],
            "material_totals": total["estimated_print_mass_g_by_material"],
            "material_rule": "各ユニークSTLについてconfigの最長prefix規則を個別適用",
            "geometry_metrics_by_part": [
                {
                    "logical_name": c["logical_name"],
                    "path": c["path"],
                    "material_rule": rules_cache[c["logical_name"]],
                    "geometry_metrics": metrics_cache[c["path"]],
                }
                for c in components
            ],
            "estimated_print_mass_g": total["estimated_print_mass_g"],
            "solid_mass_upper_bound_g": total["solid_mass_upper_bound_g"],
            "status": "REFERENCE_ONLY_SUPPORT_FAILURE_SLICER_AND_STOCK_UNVERIFIED",
            "source_parts": [c["logical_name"] for c in components],
        })
    component_reference = [
        *body_component_reference,
        *leg_component_reference,
        {
            "scope": "feet", "manifest_id": "PF-FOOT-TPU", "label": "TPU靴",
            "path": shoe_component["path"], "quantity": shoe_quantity,
            "material": rules_cache["tpu_shoe"]["material"],
            "material_rule": rules_cache["tpu_shoe"],
            "geometry_metrics": metrics_cache[shoe_component["path"]],
            "estimated_print_mass_g": round(
                metrics_cache[shoe_component["path"]]["estimated_print_mass_g"]
                * shoe_quantity,
                6,
            ),
            "solid_mass_upper_bound_g": round(tpu_shoe_each * shoe_quantity, 6),
            "status": "REFERENCE_ONLY_SUPPORT_FAILURE_SLICER_AND_STOCK_UNVERIFIED",
        },
        {
            "scope": "feet", "manifest_id": "PF-FOOT-SPACER-PRINT", "label": "段付きPLAスペーサー",
            "path": spacer_component["path"], "quantity": spacer_quantity,
            "material": rules_cache["pla_spacer"]["material"],
            "material_rule": rules_cache["pla_spacer"],
            "geometry_metrics": metrics_cache[spacer_component["path"]],
            "estimated_print_mass_g": round(
                metrics_cache[spacer_component["path"]]["estimated_print_mass_g"] * spacer_quantity, 6
            ),
            "solid_mass_upper_bound_g": round(pla_spacer_each * spacer_quantity, 6),
            "status": "REFERENCE_ONLY_SUPPORT_FAILURE_SLICER_AND_STOCK_UNVERIFIED",
        },
        {
            "scope": "conditional_new_shin_shell", "manifest_id": "PF-SHELL-NEW-*",
            "label": "新規脛殻（既存加工との択一）", "quantity": shell_quantity,
            "material_rule": "shell variantごとに同じ非pf規則を適用",
            "estimated_print_mass_g": conditional_shell_mass["estimated_print_mass_g"],
            "solid_mass_upper_bound_g": conditional_shell_mass["solid_mass_upper_bound_g"],
            "status": f"CONDITIONAL_ONLY_NOT_IN_MACHINE_{machine_required_total}",
        },
        {
            "scope": "feet", "manifest_id": "PF-SHELL-EXISTING-*",
            "label": "既存脛殻の局所加工・再使用", "quantity": shell_quantity,
            "material": rules_cache["shin_shell_retained"]["material"],
            "material_rule": {
                "selection": "shell variantごとに同じ非pf規則を適用",
                "variants": {
                    name: rules_cache[name] for name in shell_rows
                },
            },
            "geometry_metrics_by_variant": {
                name: metrics_cache[feet_outputs[name]["path"]]
                for name in shell_rows
            },
            "estimated_print_mass_g": existing_shell_mass["estimated_print_mass_g"],
            "solid_mass_upper_bound_g": existing_shell_total,
            "printed_quantity": 0,
            "status": "REFERENCE_EXISTING_REUSE_NO_NEW_PRINT",
        },
    ]
    sources = []
    for path in (FEET_DIR / "assembly.json", BODY_DIR / "assembly.json",
                 ROOT / "tools/check_print_first_feet.py",
                 ROOT / "tools/print_first_assembly.py",
                 ROOT / "hardware/src/make_print_first_feet.py",
                 ROOT / "hardware/src/make_print_first_body.py",
                 ROOT / "hardware/src/make_print_first_leg.py",
                 ROOT / "hardware/src/config.py",
                 ROOT / "tools/print_first_components.py",
                 ROOT / "tools/make_print_first_manifest.py"):
        if path.exists():
            sources.append({"path": rel(path), "sha256": sha256(path)})
    manifest = {
        "schema_version": 1,
        "reviewed_on": "2026-09-06",
        "status": "FREEZE2_PENDING_CANDIDATE",
        "scope": "印刷優先候補。CAD検査と実物/量産許可を分け、追加購入を発生させないための台帳",
        "physical_stock_verified": False,
        "material_color_policy": "構造PLAは手持ちの青/灰/黒/赤から色自由で配分する。色は追加購入の条件にしない。TPU靴は手持ち黒95Aを候補とするが、残量と失敗/支持材を含む必要量は未測定で、最終gをスライサーで確認してから配分する。",
        "prototype_rule": "各論理部品のprototypeは設計必要数の内数。試作合格後のremainingだけを追加計数する。",
        "exclusive_rule": "blue_shell_sourceは既存局所加工4個または新規印刷4個の択一で、両方を合計しない。",
        "production_quantity_layers": production_quantity_layers,
        "material_mass_rule": {
            "source": "hardware/src/config.py:PRINT_FIRST_MATERIALS",
            "selection": "部品名に一致する最長prefixを一つ選び、material/wall_mm/infill_fractionを直接適用",
            "density_g_per_cm3": MATERIAL_DENSITY_G_PER_CM3,
            "non_pf_rules": {
                "tpu_shoe": {"material": "TPU95A", "wall_mm": 2.4, "infill_fraction": 1.0},
                "pla_spacer": {"material": "PLA", "wall_mm": 2.4, "infill_fraction": 1.0},
                "shin_shell_retained": {"material": "PLA", "wall_mm": 1.4, "infill_fraction": 0.08},
                "ld220_fit_fixture": {"material": "PLA", "wall_mm": 2.4, "infill_fraction": 1.0},
            },
            "estimated_mass_model": "tools/export_urdf.py:estimate_mass_g — surface area × wall + remaining solid volume × infill",
            "solid_upper_bound_model": "serialized STL solid volume × density (complete fill)",
            "status": "CONFIG_DERIVED_REFERENCE_ONLY_NOT_SLICER_OR_STOCK",
        },
        "adopted_candidate_stls": rows,
        "assembly_reference_stls": assembly_refs,
        "pending_integration_candidates": pending,
        "excluded_from_production_count": excluded,
        "ld220_integration_rule": "LDケージ/ホーン単体候補はcoxa等へ一体化後に本番数へ加えない。現物の型番・寸法・適合を未確認のまま量産しない。",
        "mass_summary": {
            "status": "REFERENCE_CONFIG_DERIVED_SUPPORT_FAILURE_SLICER_AND_STOCK_UNVERIFIED",
            "basis": "各serialized STLの表面積・実体積とconfigの最長prefix材料規則から算出。A.massの最終配置、支持材、失敗分、実スライサー値、手持ち残量、印刷完了を保証しない。",
            "implemented_quantity_weighted_g": {
                "body": round(body_reference_total, 4),
                "legs": round(legs_reference_total, 4),
                "feet": round(feet_reference_total, 4),
                "reported_total": round(body_reference_total + legs_reference_total + feet_reference_total, 4),
                "displayed_component_sum": round(body_reference_total + legs_reference_total + feet_reference_total, 4),
                "rounding_difference": 0.0,
            },
            "material_totals_reference_g": {
                "PLA": {
                    "body": round(body_reference_total, 4),
                    "legs": round(legs_reference_total, 4),
                    "feet_spacers": round(pla_spacer_each * spacer_quantity, 4),
                    "feet_existing_shell_reuse": round(existing_shell_total, 4),
                    "feet_new_shell_print": round(new_shell_total, 4),
                    "known_with_existing_shell_reuse": round(body_reference_total + legs_reference_total + pla_spacer_each * spacer_quantity + existing_shell_total, 4),
                    "known_print_material_excluding_existing_shell_reuse": round(body_reference_total + legs_reference_total + pla_spacer_each * spacer_quantity, 4),
                },
                "TPU95A": {
                    "feet_shoes": round(tpu_shoe_each * shoe_quantity, 4),
                },
            },
            "material_totals_estimated_print_g": {
                mat: {
                    design_mass_layer_key: values["mass_g"],
                    prototype_mass_layer_key: mass_layers[prototype_mass_layer_key]["estimated_print_mass_g_by_material"].get(mat, {"mass_g": 0.0})["mass_g"],
                    remaining_mass_layer_key: mass_layers[remaining_mass_layer_key]["estimated_print_mass_g_by_material"].get(mat, {"mass_g": 0.0})["mass_g"],
                }
                for mat, values in mass_layers[design_mass_layer_key]["estimated_print_mass_g_by_material"].items()
            },
            "material_totals_solid_mass_upper_bound_g": {
                mat: {
                    design_mass_layer_key: values["mass_g"],
                    prototype_mass_layer_key: mass_layers[prototype_mass_layer_key]["solid_mass_upper_bound_g_by_material"].get(mat, {"mass_g": 0.0})["mass_g"],
                    remaining_mass_layer_key: mass_layers[remaining_mass_layer_key]["solid_mass_upper_bound_g_by_material"].get(mat, {"mass_g": 0.0})["mass_g"],
                }
                for mat, values in mass_layers[design_mass_layer_key]["solid_mass_upper_bound_g_by_material"].items()
            },
            "quantity_layer_keys": {
                "design": design_mass_layer_key,
                "prototype": prototype_mass_layer_key,
                "remaining": remaining_mass_layer_key,
            },
            "comparison_quantity_baseline": dict(COMPARISON_QUANTITY_BASELINE),
            "historical_comparison_quantity_baseline": dict(
                HISTORICAL_COMPARISON_QUANTITY_BASELINE
            ),
            "quantity_layer_mass": mass_layers,
            conditional_shell_mass_key: conditional_shell_mass,
            existing_shell_mass_key: existing_shell_mass,
            "required_quantity_plan": {
                "body": body_required_quantity,
                "legs": legs_required_quantity,
                "feet": {"tpu_shoes": shoe_quantity, "pla_spacers": spacer_quantity},
                "conditional_new_shin_shell": shell_quantity,
            },
            "component_reference": component_reference,
            "reconciliation_note": "estimated_print_mass_gはexport_urdf.pyと同じ壁厚・充填率概算、solid_mass_upper_bound_gは完全充填上限。支持材・失敗分・実スライサー値・手持ち残量は未確認で、印刷可否や購入不足へ読み替えない。",
            "checks_pending": [
                "部品別A.massの材料・壁数・充填率・最終freeze2 geometryと一致",
                "TPU支持材/失敗分を含むスライサーg",
                "手持ちフィラメント残量の実測",
                "新規脛殻を選ぶ場合のスライサーg",
            ],
        },
        "verification_gates": [
            "新STLのhash・material・版・向きを固定",
            "印刷向きTPU靴1個/段付きスペーサー4個の単体嵌合・ねじ締結を確認",
            "脚1本の支持・荷重・電源・停止を確認",
            f"body/legsのA.mass部品別material/wall/infillと一致させ、最終freeze2で生成assemblyの設計必要数{machine_required_total}を突合",
            "全機の固定部品・電装・自己干渉を再生成して検査",
            "歩行シムを最終STL・質量・接触材料で再実行し、実機歩行は別に確認",
        ],
        "sources": sources,
    }
    manifest["candidate_summary"] = {
        "previous_incomplete_listing_count": 19,
        "adopted_production_entry_count": len(rows),
        "assembly_reference_entry_count": len(assembly_refs),
        "pending_integration_entry_count": len(pending),
        "excluded_entry_count": len(excluded),
        "design_required_quantity": production_quantity_layers["machine_design_required"]["total"],
        "currently_printable_quantity": production_quantity_layers["machine_design_required"]["currently_printable_quantity"],
        "note": "設計必要数は生成assemblyから算出し、今印刷可数はfreeze2・足検査・材料/在庫ゲート未通過のため0。旧19件の履歴一覧と混同しない。",
    }
    all_groups = {
        "adopted_candidate_stls": rows,
        "assembly_reference_stls": assembly_refs,
        "pending_integration_candidates": pending,
        "excluded_from_production_count": excluded,
    }
    seen_paths = {}
    for group, group_rows in all_groups.items():
        for row in group_rows:
            path = row["path"]
            if path in seen_paths:
                raise ValueError(f"同一STLが役割間で重複: {path} ({seen_paths[path]} と {group})")
            seen_paths[path] = group
    return manifest


def markdown(manifest):
    rows = manifest["adopted_candidate_stls"]
    layers = manifest["production_quantity_layers"]
    machine = layers["machine_design_required"]
    shell = layers["conditional_new_shin_shell"]
    prototype = machine["prototype_quantity"]
    remaining = machine["remaining_after_prototype"]
    quantity_baseline = manifest["mass_summary"]["comparison_quantity_baseline"]
    historical_baseline = manifest["mass_summary"][
        "historical_comparison_quantity_baseline"
    ]
    release = layers["release_layers"]
    release_a = release["A_minimum_fit_prototype"]
    release_b = release["B_remaining_after_prototype"]
    release_c = release["C_conditional_new_shin_shell"]
    lines = [
        "# 印刷優先マニフェスト（2026-09-06）", "",
        f"**状態: `{manifest['status']}`。** {manifest['scope']}。", "",
        "初回の全体仮組みは設計必要数の内数で、その内側に1靴・1脚の実物適合確認を含める。"
        "合格後の残数だけを追加計数する。"
        "青殻は既存局所加工4個と新規印刷4個を択一にし、LDケージ単体候補・工具・"
        "同じ論理部品の向き違いを本番数へ重ねない。構造PLAは手持ち色を自由に配分し、"
        "色を追加購入の条件にしない。候補形状はfreeze2前のため、以下の数量は設計必要数と今印刷可数を分離して記録する。", "",
        "## 個数の三層（設計必要数と今印刷可数）", "",
        "今印刷可数は、freeze2・足の厳密検査・実物適合・材料残量のゲートをすべて通過するまで **0** とする。設計必要数は生成済みassemblyの部品対応から算出し、pending行の数量0へ隠さない。", "",
        "| 層 | 設計必要数 | 初回内数 | 適合後残数 | 今印刷可数 | 扱い |", "|---|---:|---:|---:|---:|---|",
        f"| 設計必要数 body（量産未解放） | {machine['body']} | {prototype['body']} | {remaining['body']} | 0 | body/assembly.jsonの全parts（yaw蓋を含む） |",
        f"| 設計必要数 legs（量産未解放） | {machine['legs']} | {prototype['legs']} | {remaining['legs']} | 0 | legs/assembly.jsonの脚対応数の合計。各ユニークSTLを1個ずつ先行 |",
        f"| 設計必要数 TPU靴（量産未解放） | {machine['tpu_shoes']} | {prototype['tpu_shoes']} | {remaining['tpu_shoes']} | 0 | 脚IDのユニーク数。1個を先行 |",
        f"| 設計必要数 PLAスペーサー（量産未解放） | {machine['pla_spacers']} | {prototype['pla_spacers']} | {remaining['pla_spacers']} | 0 | feetのspacer variants×脚ID数。1脚分を先行 |",
        f"| 設計必要数（量産未解放） | **{machine['total']}** | **{prototype['total']}** | **{remaining['total']}** | **{machine['currently_printable_quantity']}** | freeze2後に再生成・再検査 |",
        f"| 条件付き新規脛殻 | {shell['quantity']} | — | — | {shell['currently_printable_quantity']} | 既存脛殻{shell['quantity']}個を加工できない場合だけ。設計上限{shell['maximum_machine_total']}個。二案を合計しない |",
        f"| 非本番LD適合治具 | 3種×各1 | — | — | 0 | `ld220_cradle` / `ld220_cap` / `ld220_horn_adapter`。量産へ組み込まず、{machine['total']}/{shell['maximum_machine_total']}へ加算しない |",
        f"現行の比較基準は設計必要数{quantity_baseline['machine_design_required']}（量産未解放）、初回{quantity_baseline['initial_prototype']}、残り{quantity_baseline['remaining_after_prototype']}、条件付き殻を含む設計上限{quantity_baseline['conditional_machine_maximum']}。頭上部と中央カメラ逃がし部品をassemblyへ意図的に追加したため、旧レビュー値（設計必要数{historical_baseline['machine_design_required']}、初回{historical_baseline['initial_prototype']}、残り{historical_baseline['remaining_after_prototype']}、設計上限{historical_baseline['conditional_machine_maximum']}）は履歴比較として保持する。現行assemblyが変わる場合は、意図を確認したうえで基準・初回/残数・質量層を同時に更新する。", "",
        "## 印刷解放の三つの単位", "",
        f"Aは**{release_a['quantity']}個**の初回全体仮組みです。{release_a['purpose']}。このAの中で実物適合を判定する最小対象は1靴・1脚です。BはAの合格後に進める**{release_b['quantity']}個**です。Cは既存脛殻{release_c['quantity']}個の局所加工・再使用が不成立の場合だけ選ぶ新規{release_c['quantity']}個で、設計上限は{release_c['maximum_machine_total']}個です。A＋B={release['invariant']['A_plus_B']}個で設計必要数{release['invariant']['machine_design_required']}個に一致し、CはA/Bへ加えません。`currently_printable_quantity=0` はA・B・Cの解放未確定を示す値で、追加印刷全体を不要とする判定ではありません。", "",
        "| 解放単位 | 数量 | 内訳/用途 | 解放条件 | 設計必要数との関係 |", "|---|---:|---|---|---|",
        f"| A 初回全体仮組み | **{release_a['quantity']}** | {release_a['purpose']}。実物適合の最小対象は1靴・1脚 | {release_a['release_rule']} | 初回内数の全量 |",
        f"| B A合格後の残数 | **{release_b['quantity']}** | body{release_b['breakdown']['body']}＋固有脚部品{release_b['breakdown']['unique_leg_parts']}＋足裏{release_b['breakdown']['underfoot']} | {release_b['release_rule']} | A＋B={release['invariant']['A_plus_B']} |",
        f"| C 条件付き新規脛殻 | **{release_c['quantity']}** | 既存脛殻再使用との択一 | {release_c['release_rule']} | A/B外。最大{release_c['maximum_machine_total']} |", "",
        "", "LD治具は実物寸法の確認に使う任意試験具で、手持ちPLA・基準面を下に置く候補向き（capは皿穴側、horn_adapterはポケット側を上）です。0.20 mmは候補値、壁数/充填率はA.massとスライサーで確定します。寸法確認後も廃棄せず保管し、一体化済みケージや蓋を二重計上しません。", "",
        "## 少量試作の順序と合否", "",
        "`currently_printable_quantity=0` のまま、実測に必要な最小試作だけをこの順で判定する。LD保持が不要と確認できた場合は治具を省略し、印刷済み品を増やさない。", "",
        "1. **LD適合治具（必要時のみ）**: `ld220_cradle`、`ld220_cap`、`ld220_horn_adapter`を各1個。ケース外形・取付軸・ホーン径/PCD・ねじ・工具進入を実物で照合し、保持でき、干渉せず、無理な押し込みなしに取り外せること。3種とも非本番。候補値の実測前採用はNo-Go。",
        "2. **TPU靴1個**: 1脚へ取り付け、四隅の接地、保持/引抜き、足裏の滑り、低速可動、配線/工具の進入を確認する。静荷重を段階的に加えて白化・裂け・永久変形・接着剥離がなく、硬いトゥが先行接地しないこと。接触力・材料強度・連続歩行は別ゲートで、合格を全4個の解放へ拡張しない。",
        "3. **隠し補強1個（候補形状を確定できた場合のみ）**: 既存部品の内側へ仮合わせし、外観面を変えず、支持面の全周接触、ねじ/接着面、工具・配線経路、静荷重時のたわみと脱落を確認する。候補を確定できない間は数量0・量産未解放とし、首補強候補11種を同時印刷しない。",
        "各段階の合否は、使用STLのSHA、材料/積層、対象個体、寸法、荷重、変形、写真/動画を記録してから次段へ進める。ホスト検査やnative traceのPASSだけで実機完成とは判定しない。", "",
        "| ID | 採用候補STL（相対path） | SHA-256 | 材料 | 材料条件 | 体積 / 表面積 | 概算質量 / 完全充填上限 | 設計合計 / 試作 / 残数 | 今印刷可 | 向き | 置換旧部品 | 量産前ゲート |",
        "|---|---|---|---|---|---:|---:|---:|---:|---|---|---|",
    ]
    for row in rows:
        q = row["quantity"]
        qty = f"{q['total']} / {q['prototype']} / {q['remaining_after_prototype']}"
        replace = ", ".join(row["replaces_old_parts"]) or "—"
        geometry = row.get("geometry_metrics", {})
        lines.append("| {id} | `{path}` | `{sha}` | {mat} | {rule} | V {volume:.1f} / A {area:.1f} | {estimated:.2f} g / {solid:.2f} g | {qty} | {current} | {ori} | {replace} | {gate} |".format(
            id=row["id"], path=row["path"], sha=row["sha256"][:12] + "…", mat=row["material"],
            rule=row.get("print_rule_text", row.get("settings", "—")),
            volume=geometry.get("solid_volume_mm3", 0.0), area=geometry.get("surface_area_mm2", 0.0),
            estimated=geometry.get("estimated_print_mass_g", 0.0), solid=geometry.get("solid_mass_upper_bound_g", 0.0),
            qty=qty, current=row.get("currently_printable_quantity", 0), ori=row["orientation"], replace=replace,
            gate=row["pre_mass_print_verification"]))
    mass = manifest["mass_summary"]
    q = mass["implemented_quantity_weighted_g"]
    quantity_mass = mass["quantity_layer_mass"]
    quantity_layer_keys = mass["quantity_layer_keys"]
    design_mass_key = quantity_layer_keys["design"]
    prototype_mass_key = quantity_layer_keys["prototype"]
    remaining_mass_key = quantity_layer_keys["remaining"]
    conditional_shell_key = next(
        key for key in mass if key.startswith("conditional_new_shin_shell_")
    )

    def layer_material_mass(layer, key, material):
        return layer.get(f"{key}_by_material", {}).get(material, {}).get("mass_g", 0.0)

    def layer_component_mass(layer, key, logical_name):
        field = "estimated_print_mass_g" if key == "estimated" else "solid_mass_upper_bound_g"
        return sum(
            float(component[field])
            for component in layer.get("components", [])
            if component["logical_name"] == logical_name
        )

    lines += ["", "## 質量と材料の参考集計", "",
              "各STLの表面積・実体積を読み、configの最長prefix規則（材料・壁厚・充填率）を適用した。『概算』は `tools/export_urdf.py:estimate_mass_g` と同じ表面積×壁厚＋残体積×充填率、『完全充填上限』は実体積×密度である。支持材・失敗分・実スライサー値・手持ち残量・実測重量は未確認であり、印刷可数は別表のとおり0のまま。", "",
              "| 層 | 個数 | 概算 PLA | 概算 TPU95A | 完全充填 PLA | 完全充填 TPU95A | 状態 |", "|---|---:|---:|---:|---:|---:|---|",
              f"| 設計必要数（量産未解放） | {quantity_mass[design_mass_key]['quantity']} | {layer_material_mass(quantity_mass[design_mass_key], 'estimated_print_mass_g', 'PLA'):.4f} g | {layer_material_mass(quantity_mass[design_mass_key], 'estimated_print_mass_g', 'TPU95A'):.4f} g | {layer_material_mass(quantity_mass[design_mass_key], 'solid_mass_upper_bound_g', 'PLA'):.4f} g | {layer_material_mass(quantity_mass[design_mass_key], 'solid_mass_upper_bound_g', 'TPU95A'):.4f} g | body{machine['body']} + legs{machine['legs']} + 靴{machine['tpu_shoes']} + spacer{machine['pla_spacers']} |",
              f"| 初回内数 | {quantity_mass[prototype_mass_key]['quantity']} | {layer_material_mass(quantity_mass[prototype_mass_key], 'estimated_print_mass_g', 'PLA'):.4f} g | {layer_material_mass(quantity_mass[prototype_mass_key], 'estimated_print_mass_g', 'TPU95A'):.4f} g | {layer_material_mass(quantity_mass[prototype_mass_key], 'solid_mass_upper_bound_g', 'PLA'):.4f} g | {layer_material_mass(quantity_mass[prototype_mass_key], 'solid_mass_upper_bound_g', 'TPU95A'):.4f} g | body{prototype['body']} + ユニーク脚STL{prototype['legs']} + 靴{prototype['tpu_shoes']} + spacer{prototype['pla_spacers']} |",
              f"| 適合後残数 | {quantity_mass[remaining_mass_key]['quantity']} | {layer_material_mass(quantity_mass[remaining_mass_key], 'estimated_print_mass_g', 'PLA'):.4f} g | {layer_material_mass(quantity_mass[remaining_mass_key], 'estimated_print_mass_g', 'TPU95A'):.4f} g | {layer_material_mass(quantity_mass[remaining_mass_key], 'solid_mass_upper_bound_g', 'PLA'):.4f} g | {layer_material_mass(quantity_mass[remaining_mass_key], 'solid_mass_upper_bound_g', 'TPU95A'):.4f} g | legs{remaining['legs']} + 靴{remaining['tpu_shoes']} + spacer{remaining['pla_spacers']} |",
              f"| 条件付き新規脛殻 | {mass[conditional_shell_key]['quantity']} | {layer_material_mass(mass[conditional_shell_key], 'estimated_print_mass_g', 'PLA'):.4f} g | — | {layer_material_mass(mass[conditional_shell_key], 'solid_mass_upper_bound_g', 'PLA'):.4f} g | — | 既存{shell['quantity']}個の局所加工不可時だけ。量産へ加えず設計上限{shell['maximum_machine_total']} |", "",
              f"完全充填上限の旧互換集計は body **{q['body']:.4f} g**、legs **{q['legs']:.4f} g**、feet（靴＋スペーサー） **{q['feet']:.4f} g**、合計 **{q['reported_total']:.4f} g**です。既存殻再使用は新規印刷材料へ足さず、新規殻4個とは択一です。", "",
              f"| 設計必要数{machine['total']}（量産未解放）の内訳 | 概算 | 完全充填上限 | 個数 |", "|---|---:|---:|---:|",
              f"| body | {sum(c['estimated_print_mass_g'] for c in quantity_mass[design_mass_key]['components'] if c['scope']=='body'):.4f} g | {sum(c['solid_mass_upper_bound_g'] for c in quantity_mass[design_mass_key]['components'] if c['scope']=='body'):.4f} g | {machine['body']} |",
              f"| legs | {sum(c['estimated_print_mass_g'] for c in quantity_mass[design_mass_key]['components'] if c['scope']=='legs'):.4f} g | {sum(c['solid_mass_upper_bound_g'] for c in quantity_mass[design_mass_key]['components'] if c['scope']=='legs'):.4f} g | {machine['legs']} |",
              f"| TPU靴 | {layer_component_mass(quantity_mass[design_mass_key], 'estimated', 'tpu_shoe'):.4f} g | {layer_component_mass(quantity_mass[design_mass_key], 'solid', 'tpu_shoe'):.4f} g | {machine['tpu_shoes']} |",
              f"| PLAスペーサー | {layer_component_mass(quantity_mass[design_mass_key], 'estimated', 'pla_spacer'):.4f} g | {layer_component_mass(quantity_mass[design_mass_key], 'solid', 'pla_spacer'):.4f} g | {machine['pla_spacers']} |", "",
              f"設計必要数は body {machine['body']}、legs {machine['legs']}、TPU靴 {machine['tpu_shoes']}、PLAスペーサー {machine['pla_spacers']} の **{machine['total']}個**です。これは量産未解放の設計値です。既存脛殻4個の局所加工・再使用と新規脛殻4個は択一で、設計上限は{shell['maximum_machine_total']}個。LD治具3種各1は非本番です。", "",
              "## 組立参照（数量0）", "",
              "foot-frame版は組立座標の参照であり、印刷向き候補と同じ論理部品を二重計上しない。", "",
              "| ID | 相対path | SHA-256 | 材料 | 数量 | 役割 | 理由 |", "|---|---|---|---|---:|---|---|"]
    for row in manifest["assembly_reference_stls"]:
        lines.append(f"| {row['id']} | `{row['path']}` | `{row['sha256'][:12]}…` | {row['material']} | 0 | 組立参照 | {row['pre_mass_print_verification']} |")
    lines += ["", "## 最終凍結後に印刷可否を確定する候補", "",
              "下表の数量は設計必要数として表示し、今印刷可数は上の層表で0と分離する。最終freeze2後にCAD・材料・実LD適合・全脚干渉を再確認する。", "",
              "| ID | 相対path | SHA-256 | 材料/設定 | 設計必要数 | 今印刷可 | 理由 |", "|---|---|---|---|---:|---:|---|"]
    for row in manifest["pending_integration_candidates"]:
        lines.append(f"| {row['id']} | `{row['path']}` | `{row['sha256'][:12]}…` | {row['material']} / {row['settings']} | {row['quantity']['total']} | {row.get('currently_printable_quantity', 0)} | {row['pre_mass_print_verification']} |")
    lines += ["", "## 生産数へ含めない候補", "",
              "| ID | 相対path | SHA-256 | 理由 |", "|---|---|---|---|"]
    for row in manifest["excluded_from_production_count"]:
        lines.append(f"| {row['id']} | `{row['path']}` | `{row['sha256'][:12]}…` | {row['reason']} |")
    lines += ["", "## 非本番LD適合治具の個別仕様", "",
              "治具は量産・本番数に含めない。実物寸法の確認に使い、確認後も廃棄せず保管する。", "",
              "| ID | 相対path | 設計必要数 | 今印刷可 | 材料/向き | 用途 |", "|---|---|---:|---:|---|---|"]
    for fixture in manifest["production_quantity_layers"]["non_production_ld_fit_fixtures"]:
        lines.append(f"| `{fixture['id']}` | `{fixture['path']}` | {fixture['quantity']} | {fixture['currently_printable_quantity']} | {fixture['material']} / {fixture['orientation']} | {fixture['purpose']}。{fixture['disposition']} |")
    lines += ["", "## 固定の順序", ""]
    lines.extend(f"{i}. {gate}" for i, gate in enumerate(manifest["verification_gates"], 1))
    lines += ["", "## 根拠", ""]
    lines.extend(f"- `{src['path']}` — `{src['sha256']}`" for src in manifest["sources"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MD)
    args = parser.parse_args()
    manifest = build_manifest()
    args.json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    args.markdown.write_text(markdown(manifest))
    print(json.dumps({"json": rel(args.json), "markdown": rel(args.markdown),
                      "candidate_stl_count": len(manifest["adopted_candidate_stls"]),
                      "excluded_count": len(manifest["excluded_from_production_count"]),
                      "status": manifest["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
