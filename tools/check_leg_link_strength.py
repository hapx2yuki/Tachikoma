"""脚リンク (tibia_link / femur_link) の曲げ応力スキャン — 断面係数の全区間走査。

2026-09-04 機構レビュー M-01 の恒久チェッカ: 既存の check_leg_assembly.py
(干渉・クリアランス) と check_screw_bosses.py (ビス肉厚) はどちらも「梁として
の曲げ強度」を見ておらず、tibia_link の膝ネックが 45° ウェッジで 11mm² まで
痩せていた欠陥 (σ≈240MPa, 破断確実) を見逃していた。ここでは実 STL を
リンク軸に沿って 0.5mm 刻みで切断し、各断面の断面二次モーメント (多角形の
Green 公式, 穴込み) から 2 軸の断面係数 Z を求め、足先荷重による曲げ
モーメント M(s) と比較して安全率 SF = σ_allow / (M/Z) の最小値を報告する。

荷重ケース (保守的):
  F_foot  = LOAD_KGF × 9.81 N。LOAD_KGF は sim_gait.py [3] の 3 脚支持静力学
            (重心オフセット込み) の最悪脚荷重 (≈1.9kgf) に動的係数 2.0
            (check_pod_neck_strength.py と同じ) を掛けた 3.8kgf を既定とする
  tibia   : 足先 (z=-TIBIA_LEN) に横荷重 T = 0.6·F (脛傾き ≤25° の面内成分
            sin25°≈0.42 と摩擦横力 μ≈0.5 の合成上限) を x / y 両方向で個別に
            作用させ、各断面 z で M = T·(z - z_foot)
  femur   : 膝 (x=FEMUR_LEN) に鉛直 F (y 軸まわり曲げ) と横 0.5·F (z 軸まわり)
            を作用させ、各断面 x で M = F·(FEMUR_LEN - x)
許容応力 σ_allow = 55MPa (PETG 曲げ強度の文献値 50-70MPa の下寄り, UNVERIFIED —
Phase 0 で試験片実測を推奨 (docs/build_plan.md P-06))。要求 SF ≥ 2.0。

使い方: .venv/bin/python tools/check_leg_link_strength.py  → 各リンクの最弱断面と SF
"""
from __future__ import annotations

import sys
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
import config as C  # noqa: E402

STL = ROOT / "hardware" / "stl"
LOAD_KGF = 3.8          # 1.9kgf (3 脚支持・重心オフセット込み最悪脚) × 動的係数 2.0
SIGMA_ALLOW = 55.0      # MPa (PETG 曲げ, UNVERIFIED 文献値の下寄り)
SF_REQ = 2.0
STEP = 0.5
PHASE = 0.0137   # 断面位置を格子からずらす (面と一致する平面は退化リングを返す)
PRINT_FIRST_LEGS_DIR = ROOT / "outputs/print-first-20260905/legs"
PRINT_FIRST_PARTS = {
    "coxa": "pf_coxa_bracket",
    "femur": "pf_femur_link",
    "tibia": "pf_tibia_link",
}


def _ring_props(xy: np.ndarray):
    """閉多角形 (N,2) の符号付き面積・一次/二次モーメント (原点まわり)。"""
    x, y = xy[:, 0], xy[:, 1]
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    cross = x * y1 - x1 * y
    A = 0.5 * cross.sum()
    Sx = cross @ (y + y1) / 6.0          # ∫y dA
    Sy = cross @ (x + x1) / 6.0          # ∫x dA
    Ixx = cross @ (y * y + y * y1 + y1 * y1) / 12.0
    Iyy = cross @ (x * x + x * x1 + x1 * x1) / 12.0
    return A, Sx, Sy, Ixx, Iyy


def section_props(mesh: trimesh.Trimesh, origin, normal, axes):
    """断面の (A, Zu, Zv): axes=(u,v) は断面内の 2 直交軸 (3D 単位ベクトル)。
    穴は自動的に負の面積として効く (リング向きを包含数で決める)。"""
    s = mesh.section(plane_origin=origin, plane_normal=normal)
    if s is None:
        return None
    rings = []
    for path in s.discrete:
        p = np.asarray(path)
        if len(p) < 3:
            continue
        uv = np.stack([p @ axes[0], p @ axes[1]], axis=1)
        rings.append(uv)
    if not rings:
        return None
    # 向き正規化: 外周 CCW (+), 穴 CW (-)。穴判定 = 他リングに奇数回含まれる
    from matplotlib.path import Path as MPath
    paths = [MPath(r) for r in rings]
    props = []
    for i, r in enumerate(rings):
        inside = sum(1 for j, pp in enumerate(paths) if j != i and pp.contains_point(r[0]))
        A, Sx, Sy, Ixx, Iyy = _ring_props(r)
        sign = -1.0 if inside % 2 == 1 else 1.0
        if (A > 0) != (sign > 0):
            A, Sx, Sy, Ixx, Iyy = -A, -Sx, -Sy, -Ixx, -Iyy
        props.append((A, Sx, Sy, Ixx, Iyy, r))
    A = sum(p[0] for p in props)
    if A <= 1e-6:
        return None
    cu = sum(p[2] for p in props) / A
    cv = sum(p[1] for p in props) / A
    Iuu = sum(p[3] for p in props) - A * cv * cv   # v 方向分布 → u 軸まわり
    Ivv = sum(p[4] for p in props) - A * cu * cu
    allv = np.concatenate([p[5] for p in props])
    c_v = np.abs(allv[:, 1] - cv).max()
    c_u = np.abs(allv[:, 0] - cu).max()
    return A, Iuu / c_v, Ivv / c_u   # Z_u (u 軸まわり曲げ), Z_v


def scan(name, mesh, s_range, origin_fn, normal, axes, moment_fns, labels,
         *, sigma_allow=SIGMA_ALLOW, sf_req=SF_REQ, emit=True,
         return_details=False):
    worst = (np.inf, None)
    rows = []
    lo, hi = sorted(s_range)
    for s in np.arange(lo, hi, STEP) + PHASE:
        pr = section_props(mesh, origin_fn(s), normal, axes)
        if pr is None:
            continue
        A, Zu, Zv = pr
        for (mfn, Z, lab) in ((moment_fns[0], Zu, labels[0]), (moment_fns[1], Zv, labels[1])):
            M = mfn(s)
            if M <= 0 or Z <= 1e-9:
                continue
            sigma = M / Z
            sf = sigma_allow / sigma
            rows.append((s, A, Zu, Zv))
            if sf < worst[0]:
                worst = (sf, (s, A, Z, M, sigma, lab))
    if worst[1] is None:
        raise ValueError(f"{name}: no finite positive section was found in {s_range}")
    sf, (s, A, Z, M, sigma, lab) = worst
    ok = sf >= sf_req
    details = {'name': name, 'ok': bool(ok), 'safety_factor': float(sf),
               'weak_section_mm': float(s), 'area_mm2': float(A),
               'section_modulus_mm3': float(Z), 'moment_Nm': float(M / 1000.),
               'stress_mpa': float(sigma), 'axis': lab,
               'sigma_allow_mpa': float(sigma_allow),
               'required_safety_factor': float(sf_req)}
    if emit:
        print(f"[{name}] 最弱断面 s={s:+.1f}mm ({lab}): A={A:.1f}mm² Z={Z:.1f}mm³ "
              f"M={M/1000:.2f}N·m σ={sigma:.1f}MPa → SF={sf:.2f} "
              f"({'OK' if ok else 'NG — SF<' + str(sf_req)})")
    return details if return_details else bool(ok)


def _load_solid(path: Path, label: str) -> trimesh.Trimesh:
    """強度計算へ渡すSTLを閉じた有限正体積として検査する。"""
    if not path.is_file():
        raise FileNotFoundError(f"{label}: STLが無い: {path}")
    mesh = trimesh.load(path, force="mesh")
    faces = np.asarray(mesh.faces)
    vertices = np.asarray(mesh.vertices, dtype=float)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or not len(vertices)
            or not np.isfinite(vertices).all()):
        raise ValueError(f"{label}: 非有限/空頂点")
    if (faces.ndim != 2 or faces.shape[1] != 3 or not len(faces)
            or not np.isfinite(faces).all()):
        raise ValueError(f"{label}: 非有限/空面")
    if (not mesh.is_watertight or not mesh.is_winding_consistent
            or not mesh.is_volume or not np.isfinite(mesh.volume)
            or mesh.volume <= 0.0):
        raise ValueError(
            f"{label}: finite/watertight/winding/is_volume/positive-volume 検査失敗"
        )
    return mesh


def _print_first_rule() -> dict:
    """configの印刷優先脚材料規則を検査し、計算条件を正規化する。"""
    rule = getattr(C, "PRINT_FIRST_STRENGTH_RULE", None)
    if not isinstance(rule, dict):
        raise ValueError("config.PRINT_FIRST_STRENGTH_RULE が無い")
    required = ("material", "density_g_cm3", "infill_fraction",
                "allowable_bending_mpa", "required_safety_factor",
                "base_load_kgf", "load_kgf", "dynamic_factor",
                "load_includes_dynamic_factor")
    missing = [key for key in required if key not in rule]
    if missing:
        raise ValueError(f"PRINT_FIRST_STRENGTH_RULE missing: {missing}")
    try:
        normalized = {
            "material": str(rule["material"]),
            "density_g_cm3": float(rule["density_g_cm3"]),
            "infill_fraction": float(rule["infill_fraction"]),
            "allowable_bending_mpa": float(rule["allowable_bending_mpa"]),
            "required_safety_factor": float(rule["required_safety_factor"]),
            "base_load_kgf": float(rule["base_load_kgf"]),
            "load_kgf": float(rule["load_kgf"]),
            "dynamic_factor": float(rule["dynamic_factor"]),
            "load_includes_dynamic_factor": bool(rule["load_includes_dynamic_factor"]),
        }
    except (TypeError, ValueError) as exc:
        raise ValueError(f"PRINT_FIRST_STRENGTH_RULE が数値でない: {exc}") from exc
    if normalized["material"].upper() != "PLA":
        raise ValueError("print-first脚リンクの材料規則はPLAでなければならない")
    for key in ("density_g_cm3", "allowable_bending_mpa",
                "required_safety_factor", "load_kgf", "dynamic_factor"):
        if not np.isfinite(normalized[key]) or normalized[key] <= 0.0:
            raise ValueError(f"PRINT_FIRST_STRENGTH_RULE.{key} は有限正でなければならない")
    if (not np.isfinite(normalized["infill_fraction"])
            or not 0.0 < normalized["infill_fraction"] <= 1.0):
        raise ValueError("PRINT_FIRST_STRENGTH_RULE.infill_fraction が不正")
    if normalized["dynamic_factor"] < 1.0:
        raise ValueError("PRINT_FIRST_STRENGTH_RULE.dynamic_factor は1以上が必要")
    if (not np.isfinite(normalized["base_load_kgf"])
            or normalized["base_load_kgf"] <= 0.0):
        raise ValueError("PRINT_FIRST_STRENGTH_RULE.base_load_kgf は有限正でなければならない")
    if not normalized["load_includes_dynamic_factor"]:
        raise ValueError("print-first load_kgf must be the dynamic envelope")
    if not np.isclose(normalized["base_load_kgf"] * normalized["dynamic_factor"],
                      normalized["load_kgf"], rtol=1e-9, atol=1e-9):
        raise ValueError("base_load_kgf × dynamic_factor と load_kgf が不一致")

    material_rules = getattr(C, "PRINT_FIRST_MATERIALS", {})
    for prefix in PRINT_FIRST_PARTS.values():
        spec = material_rules.get(prefix)
        if not isinstance(spec, (list, tuple)) or len(spec) < 3:
            raise ValueError(f"PRINT_FIRST_MATERIALS[{prefix!r}] が不正")
        material, wall_mm, infill = spec[:3]
        if (str(material).upper() != "PLA" or not np.isfinite(float(wall_mm))
                or float(wall_mm) <= 0.0 or not np.isfinite(float(infill))
                or not 0.0 < float(infill) <= 1.0):
            raise ValueError(f"PRINT_FIRST_MATERIALS[{prefix!r}] が不正")
        if not np.isclose(float(wall_mm), 2.4, atol=1e-9):
            raise ValueError(f"{prefix}: print-first壁厚は2.4mmでなければならない")
        if not np.isclose(float(infill), normalized["infill_fraction"], atol=1e-9):
            raise ValueError(f"{prefix}: 強度規則と材料規則の充填率が不一致")
    normalized["wall_mm"] = 2.4
    normalized["layer_orientation_status"] = "UNVERIFIED_PRINT_DIRECTION_AND_INTERLAYER_STRENGTH"
    normalized["physical_status"] = "UNVERIFIED_NO_PRINTED_PLA_STRENGTH_GUARANTEE"
    return normalized


def _print_first_paths(folder: Path) -> dict:
    """assembly.jsonの6つの標準/鏡像構造STLを、台帳付きで解決する。"""
    manifest = folder / "assembly.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"print-first脚台帳が無い: {manifest}")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"print-first脚台帳を読めない: {exc}") from exc
    rows = {str(row.get("name")): row for row in data.get("parts", [])
            if isinstance(row, dict) and row.get("name")}
    paths = {}
    for kind, stem in PRINT_FIRST_PARTS.items():
        for suffix in ("", "_m"):
            name = f"{stem}{suffix}"
            row = rows.get(name)
            if row is None or row.get("role") != "link_structure":
                raise ValueError(f"print-first脚台帳に構造部品が無い: {name}")
            raw = row.get("path")
            if not isinstance(raw, str):
                raise ValueError(f"{name}: 台帳pathが無い")
            path = ROOT / raw
            if path.resolve() != (folder / f"{name}.stl").resolve():
                raise ValueError(f"{name}: 台帳pathが指定フォルダ外")
            mesh = _load_solid(path, name)
            recorded = row.get("sha256")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if recorded and recorded != actual:
                raise ValueError(f"{name}: assembly.json SHA-256不一致")
            paths[(kind, suffix)] = (path, mesh)
    return paths


def print_first_scan(folder: Path, *, sigma_allow: float, sf_req: float,
                     load_kgf: float, emit=True, return_details=False):
    """print-firstのcoxa/femur/tibia標準・鏡像を同じ荷重で走査する。"""
    paths = _print_first_paths(folder)
    force = load_kgf * 9.81
    ok = True
    details = []
    for kind in ("coxa", "femur", "tibia"):
        for suffix in ("", "_m"):
            path, mesh = paths[(kind, suffix)]
            label = f"print-first {kind}{suffix or ' standard'}"
            if kind == "coxa":
                # 股ヨー軸から股ピッチ軸までの区間を梁として取り、
                # ピッチ軸で受ける鉛直/横荷重の保守的な曲げを確認する。
                xk = float(C.COXA_LEN)
                lo = max(float(mesh.bounds[0, 0]) + 0.5, -10.0)
                hi = min(float(mesh.bounds[1, 0]) - 0.5, xk - 0.5)
                if hi <= lo:
                    raise ValueError(f"{label}: coxa走査区間が無い")
                row = scan(label, mesh, (lo, hi), lambda x: [x, 0, 0],
                           [1, 0, 0],
                           (np.array([0, 0, 1.0]), np.array([0, 1.0, 0])),
                           (lambda x: force * (xk - x),
                            lambda x: 0.6 * force * (xk - x)),
                           ("鉛直荷重 z (y 軸まわり)", "横荷重 y (z 軸まわり)"),
                           sigma_allow=sigma_allow, sf_req=sf_req,
                           emit=emit, return_details=True)
            elif kind == "femur":
                xk = float(C.FEMUR_LEN)
                row = scan(label, mesh, (8.0, xk - 12.0),
                           lambda x: [x, 0, 0], [1, 0, 0],
                           (np.array([0, 1.0, 0]), np.array([0, 0, 1.0])),
                           (lambda x: force * (xk - x),
                            lambda x: 0.5 * force * (xk - x)),
                           ("鉛直荷重 z (y 軸まわり)", "横荷重 y (z 軸まわり)"),
                           sigma_allow=sigma_allow, sf_req=sf_req,
                           emit=emit, return_details=True)
            else:
                zf = -float(C.TIBIA_LEN)
                transverse = 0.6 * force
                row = scan(label, mesh, (-8.0, zf + 12.0),
                           lambda z: [0, 0, z], [0, 0, 1],
                           (np.array([1.0, 0, 0]), np.array([0, 1.0, 0])),
                           (lambda z: transverse * (z - zf),
                            lambda z: transverse * (z - zf)),
                           ("横荷重 y (x 軸まわり)", "面内荷重 x (y 軸まわり)"),
                           sigma_allow=sigma_allow, sf_req=sf_req,
                           emit=emit, return_details=True)
            ok &= row['ok']
            row.update({'kind': kind, 'suffix': suffix, 'path': str(path.relative_to(ROOT)),
                        'volume_mm3': float(mesh.volume)})
            details.append(row)
            if emit:
                print(f"  source={path.relative_to(ROOT)} volume={mesh.volume:.3f}mm³")
    return details if return_details else bool(ok)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--print-first", action="store_true",
        help="生成済み print-first 脚STLと config.PRINT_FIRST_STRENGTH_RULE を使う")
    parser.add_argument(
        "--print-first-dir", type=Path, default=PRINT_FIRST_LEGS_DIR,
        help="print-first脚のassembly.json/STLフォルダ（--print-first専用）")
    args = parser.parse_args(argv)
    if args.print_first:
        rule = _print_first_rule()
        sigma_allow = rule["allowable_bending_mpa"]
        sf_req = rule["required_safety_factor"]
        load_kgf = rule["load_kgf"]
        print(
            "[print-first material rule] "
            f"config.PRINT_FIRST_STRENGTH_RULE material={rule['material']} "
            f"density={rule['density_g_cm3']:.2f}g/cm3 wall={rule['wall_mm']:.1f}mm "
            f"infill={rule['infill_fraction']:.0%} "
            f"sigma_allow={sigma_allow:.1f}MPa SF_req={sf_req:.1f} "
            f"load={load_kgf:.2f}kgf (=base {rule['base_load_kgf']:.2f}kgf x "
            f"dynamic_factor {rule['dynamic_factor']:.2f}; already applied, no second multiplication) "
            f"layer_status={rule['layer_orientation_status']} physical_status={rule['physical_status']}"
        )
        ok = print_first_scan(args.print_first_dir.resolve(), sigma_allow=sigma_allow,
                              sf_req=sf_req, load_kgf=load_kgf)
        print(f"print-first geometric beam model: {'OK' if ok else 'NG'}; physical material/layer guarantee: UNVERIFIED")
        return 0 if ok else 1
    else:
        sigma_allow = SIGMA_ALLOW
        sf_req = SF_REQ
        load_kgf = LOAD_KGF
        tibia_path = STL / "tibia_link.stl"
        femur_path = STL / "femur_link.stl"
    F = load_kgf * 9.81
    ok = True
    # ---- tibia: 原点=膝軸, 足先 z=-TIBIA_LEN, 断面は z=const (u=x, v=y)
    tib = _load_solid(tibia_path, "tibia_link")
    T = 0.6 * F
    zf = -C.TIBIA_LEN
    ok &= scan("tibia_link", tib, (-8.0, zf + 12.0),
               lambda z: [0, 0, z], [0, 0, 1],
               (np.array([1.0, 0, 0]), np.array([0, 1.0, 0])),
               (lambda z: T * (z - zf), lambda z: T * (z - zf)),
               ("横荷重 y (x 軸まわり)", "面内荷重 x (y 軸まわり)"),
               sigma_allow=sigma_allow, sf_req=sf_req)
    # ---- femur: 原点=股ピッチ軸, 膝 x=FEMUR_LEN, 断面は x=const (u=y, v=z)
    fem = _load_solid(femur_path, "femur_link")
    xk = C.FEMUR_LEN
    ok &= scan("femur_link", fem, (8.0, xk - 12.0),
               lambda x: [x, 0, 0], [1, 0, 0],
               (np.array([0, 1.0, 0]), np.array([0, 0, 1.0])),
               (lambda x: F * (xk - x), lambda x: 0.5 * F * (xk - x)),
               ("鉛直荷重 z (y 軸まわり)", "横荷重 y (z 軸まわり)"),
               sigma_allow=sigma_allow, sf_req=sf_req)
    print(f"荷重 F={load_kgf}kgf (動的係数込み), σ_allow={sigma_allow}MPa (UNVERIFIED 文献値), "
          f"要求 SF≥{sf_req} → {'ALL OK' if ok else 'NG'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
