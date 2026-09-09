#!/usr/bin/env python3
"""機構監査の STL/3MF 台帳を作る。印刷物・設計ソースは変更しない。"""
from __future__ import annotations

import hashlib
import ast
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/audits/20260905-round2"
sys.path.insert(0, str(ROOT / "tools"))
from check_print_artifacts import audit


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mesh_facts(m):
    return {"vertices": len(m.vertices), "triangles": len(m.faces),
            "watertight": bool(m.is_watertight), "winding_consistent": bool(m.is_winding_consistent),
            "finite": bool(np.isfinite(m.vertices).all()),
            "components": len(m.split(only_watertight=False)),
            "bounds_mm": m.bounds.tolist(), "volume_mm3": float(m.volume)}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    paths = sorted(list((ROOT / "model").glob("*.stl")) +
                   list((ROOT / "hardware/stl").glob("*.stl")) +
                   list((ROOT / "hardware/stl").glob("*.3mf")) + list(ROOT.glob("*.3mf")) +
                   list((ROOT / "hardware/src").glob("*.py")) +
                   [ROOT / "tools" / n for n in (
                       "kit_assembly.py", "extract_meshes.py", "make_head_eyecut.py", "make_plates.py",
                       "check_leg_assembly.py", "check_screw_bosses.py", "check_leg_link_strength.py",
                       "check_pod_neck_strength.py", "check_arm.py", "check_eye.py", "check_audio.py",
                       "check_camera.py", "check_shin_arm_leg.py", "check_head_pod_clearance.py",
                       "check_audio_assembly.py", "check_arm_joints.py", "check_kit_transforms.py", "mesh_checks.py",
                       "check_print_artifacts.py", "check_print_strength_sensitivity.py", "check_toe_contact.py",
                       "design_foot_support_candidates.py", "design_neck_wire_passage.py", "design_ov3660_camera.py",
                       "design_xiao_head_mount.py", "design_xiao_camera_cradle.py", "check_head_attachment_candidates.py",
                       "check_ov3660_full_fov.py", "check_mouth_chassis.py", "check_elbow_cover_mount.py", "check_hand_assembly.py",
                       "render_foot_contact.py", "render_foot_support_candidates.py", "audit_mechanical_inventory.py")] +
                   [ROOT/'tools/tests'/n for n in ('test_mesh_checks.py','test_print_artifacts.py','test_stl_export.py','test_export_stl.py')] +
                   list((ROOT / "tools/data").glob("*.json")))
    rows = []
    for path in paths:
        row = {"path": str(path.relative_to(ROOT)), "sha256": digest(path),
               "review_method": [], "findings": [], "evidence": {}}
        if path.suffix.lower() == ".stl":
            row["review_method"].append("trimesh 全頂点/面読込・有限値・閉形状・向き・連結性・体積検査")
            row["evidence"] = mesh_facts(trimesh.load(path, force="mesh"))
            if not row["evidence"]["watertight"]:
                row["findings"].append("非閉形状: 印刷可否はスライサ修復に依存")
            if row["evidence"]["components"] != 1:
                row["findings"].append("複数連結成分: 部品用途と分離体を個別確認")
        elif path.suffix.lower() == ".3mf":
            row["review_method"].append("ZIP CRC と全 .model XML・全メッシュの面参照/閉形状検査")
            objects = []
            with zipfile.ZipFile(path) as z:
                bad = z.testzip()
                if bad:
                    row["findings"].append(f"ZIP CRC 不一致: {bad}")
                for member in z.namelist():
                    if not member.endswith(".model"):
                        continue
                    xml = ET.fromstring(z.read(member))
                    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
                    for obj in xml.findall(".//m:object", ns):
                        me = obj.find("m:mesh", ns)
                        if me is None:
                            continue
                        vs = [[float(e.attrib[a]) for a in ("x", "y", "z")]
                              for e in me.findall("m:vertices/m:vertex", ns)]
                        fs = [[int(e.attrib[a]) for a in ("v1", "v2", "v3")]
                              for e in me.findall("m:triangles/m:triangle", ns)]
                        if not vs or not fs or min(min(f) for f in fs) < 0 or max(max(f) for f in fs) >= len(vs):
                            raise ValueError(f"invalid mesh: {path}:{member}:{obj.get('id')}")
                        facts = mesh_facts(trimesh.Trimesh(vertices=vs, faces=fs, process=True))
                        objects.append({"member": member, "id": obj.get("id"), **facts})
                row["evidence"]["mesh_objects"] = objects
                row["evidence"]["gcode_members"] = [n for n in z.namelist() if n.endswith(".gcode")]
            row["review_method"].append("配置変換込みの現行 STL 形状・材種・壁数・密度照合")
            result = audit(path)
            row["evidence"]["current_source_audit"] = result
            row["findings"].extend(result.get("errors", []))
            row["findings"].extend(result.get("warnings", []))
        elif path.suffix == ".py":
            source = path.read_text()
            tree = ast.parse(source)
            row["review_method"].append("全ソース読解、構文木全走査、寸法/座標変換/生成順/検査失敗経路の比較")
            row["evidence"] = {"lines": len(source.splitlines()), "functions": [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))],
                               "report": "docs/audits/20260905-round2/mechanical.md"}
            if path.name == "make_cabin_electronics.py":
                row["review_method"] = ["分担者 firmware_audit が全ソース実装・CAD/挿入/外皮検査。機構担当は境界座標と配線孔を照合"]
            references = {
                "make_arm.py": ("腕の隣接干渉とケース底カバー移設後の前腕干渉を修正。4可動ペア/固定4項目を検査", "elbow-cover-candidates/arm-joints-source.json"),
                "arm_shell.py": ("肘軸とケース底カバー原点の混同で上腕/ケースへ食い込み。原球を保存しY=-13.8へ移設", "elbow-cover-candidates/source-current.json"),
                "make_audio.py": ("マイク挿入不可、Neck-BallとCap-Neck食い込み。内部通路と挿入掃引座を修正", "audio-assembly-cap-seat-source.json"),
                "make_head_eyecut.py": ("下向きサーボのケース高さに ABOVE_TAB を誤用。外殻内収納の再設計が必要", "simulation/yaw-pack-lower-3.json"),
                "make_camera.py": ("本体基板の保持未設計、OV3660では寸法/画角が異なる。内部候補あり", "camera-ov3660-candidate/comparison.json"),
                "check_head_pod_clearance.py": ("基部パッドを除外して実交差をPASSにしていた。全材で検査しFAILを保持", "head-pod-before.log"),
                "check_camera.py": ("25mm以内の遮蔽を除外。近距離遮蔽も検査するよう修正", "mechanical.md"),
                "make_chassis.py": ("口3部品との交差を隠れた逃げで修正、前90度穴に3mmカラーで5mm支持断面を保持。頭内収納/頭固定/首は未解消", "mouth-chassis-source.json"),
            }
            if path.name in references:
                finding, evidence = references[path.name]
                row["findings"].append(finding)
                row["evidence"]["finding_evidence"] = "docs/audits/20260905-round2/" + evidence
        else:
            obj = json.loads(path.read_text())
            row["review_method"] = ["JSON全構造/全値解析、配置ローダ全展開、全行列の倍率/直交/有限値照合、口/足/後部の説明と実体の突合"]
            row["evidence"] = {"top_level_keys": list(obj), "transforms": "docs/audits/20260905-round2/kit-transform-audit-after.json"}
            if path.name == "kit_assembly_front.json":
                row["findings"].append("toe12個だけ100%行列。根元位置維持で印刷仕様150%へ修正。旧説明に残る接着代=実体重なりの解釈は無効")
                row["evidence"]["correction"] = "docs/audits/20260905-round2/toe-scale-correction.json"
        rows.append(row)
        print(row["path"], "findings", len(row["findings"]), flush=True)
    target = OUT / "coverage-mechanical.json"
    target.write_text(json.dumps({"date": "2026-09-05", "files": rows}, ensure_ascii=False, indent=2) + "\n")
    print(f"台帳: {len(rows)} files -> {target}")


if __name__ == "__main__":
    main()
