#!/usr/bin/env python3
"""pod_neck (ポッド接続ネック梁) の曲げ強度検証 (2026-07-31 QA 再検証で新規作成)。

**手法上の注意 (2026-07-31 QA major 指摘への対応)**: 旧 docs/assembly.md の
計算は「断面係数 Z(y) が最小になる断面 (y=-50, ボルト穴の中心)」を単独で
特定し、そこに『別に計算した』重心までのレバーアーム (同じ y=-50 基準) を
掛け合わせて σ=M/Z, 安全率を出していた。しかしカンチレバー梁の曲げ応力は
位置ごとに σ(y) = M(y)/Z(y) (M(y) = F×|y-CoG|) で決まり、Z(y) が最小の点が
必ずしも σ(y) が最大の点 (=真の最弱点) とは限らない。本スクリプトは
pod_neck 実寸の全区間で Z(y) を実メッシュから直接算出 (trimesh の平面切断
+ Green の定理による二次モーメント、単純な矩形近似ではなくボルト穴の減肉・
丸め形状を反映) し、各 y で σ(y)=M(y)/Z(y) を評価して真の最弱点 (σ 最大点)
で安全率を報告する。

  - 荷重 F: Cabin ポッド系実重量 600g (切り上げ) × g × 動的係数2
    = 11.77N (`tools/filament_calc.py` の printed_cm3 モデルで Cabin 一式を
    実メッシュ体積から積算した実重量 587.4g を切り上げ, docs/assembly.md
    「頭部中央寄せ」強度検証節に導出過程を記載)
  - 重心 COG_Y: `Cabin_Front_Blue`/`Cabin_Back_Blue_Repaired` の実配置後
    centroid の質量加重平均 = -187.1mm (同上, `tools/make_visuals.py
    shell_ghosts()` と同一の配置変換を適用して算出)
  - PETG 曲げ強度: 70MPa (文献値, UNVERIFIED — 実測ではない)
  - 応力集中係数 Kt: 段付き梁の曲げに関する文献チャート (Peterson 型) から
    保守的に Kt≈1.5-2.5 と見積る (FEA 未実施, UNVERIFIED)

判定: 最弱点における SF_effective = (PETG_MPA/σ_worst) / Kt(=2.5, 最も
保守的) が要求安全率 3.0 以上であること。
"""
import sys
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
import config as C  # noqa: E402
import make_chassis as MC  # noqa: E402

F_N = 0.6 * 9.81 * 2          # 600g (切り上げ) × g × 動的係数2 [N]
COG_Y = -187.1                 # Cabin 質量加重重心 y [mm] (docs/assembly.md 参照)
PETG_MPA = 70.0                 # PETG 曲げ強度 [MPa] (文献値, UNVERIFIED)
KT_RANGE = (1.5, 2.0, 2.5)      # 応力集中係数の見積り範囲 (保守的な上限を判定に使用)
SF_REQUIRED = 3.0
Y_STEP = 0.2                   # 全区間スキャンの刻み幅 [mm]

OK = True


def check(cond, msg):
    global OK
    print(f"  {'OK ' if cond else 'NG '} {msg}")
    OK &= bool(cond)


def to_tm(m):
    mesh = m.to_mesh()
    return trimesh.Trimesh(vertices=np.array(mesh.vert_properties[:, :3]),
                            faces=np.array(mesh.tri_verts), process=False)


def _polygon_moments(coords):
    x = np.asarray(coords[:, 0]); y = np.asarray(coords[:, 1])
    x1 = np.roll(x, -1); y1 = np.roll(y, -1)
    cross = x * y1 - x1 * y
    A = 0.5 * np.sum(cross)
    if abs(A) < 1e-12:
        return 0.0, 0.0, 0.0, 0.0
    Cx = np.sum((x + x1) * cross) / (6 * A)
    Cy = np.sum((y + y1) * cross) / (6 * A)
    Ixx_o = np.sum((y * y + y * y1 + y1 * y1) * cross) / 12.0
    return A, Cx, Cy, Ixx_o


def section_modulus_at_y(mesh_tm, y_cut):
    """y_cut での断面係数 Z=I_xx/c を Green の定理で数値算出 (ボルト穴などの
    内周ループ=負の面積を正しく減算する)。"""
    sec = mesh_tm.section(plane_origin=[0, y_cut, 0], plane_normal=[0, 1, 0])
    if sec is None:
        return None
    planar = trimesh.path.Path2D(entities=sec.entities, vertices=sec.vertices[:, [0, 2]])
    polys = planar.polygons_full
    if not polys:
        return None
    rings = []
    zmin, zmax = np.inf, -np.inf
    A_tot = 0.0
    for poly in polys:
        ext = np.array(poly.exterior.coords)
        A, Cx, Cy, Ixx_o = _polygon_moments(ext)
        if A < 0:
            ext = ext[::-1]
            A, Cx, Cy, Ixx_o = _polygon_moments(ext)
        rings.append((A, Cy, Ixx_o)); A_tot += A
        zmin = min(zmin, ext[:, 1].min()); zmax = max(zmax, ext[:, 1].max())
        for interior in poly.interiors:
            hpts = np.array(interior.coords)
            hA, hCx, hCy, hIxx_o = _polygon_moments(hpts)
            if hA > 0:
                hpts = hpts[::-1]
                hA, hCx, hCy, hIxx_o = _polygon_moments(hpts)
            rings.append((hA, hCy, hIxx_o)); A_tot += hA
    if abs(A_tot) < 1e-9:
        return None
    Cz = sum(A * Cy for A, Cy, _ in rings) / A_tot
    Ixx_o_tot = sum(Ixx_o for _, _, Ixx_o in rings)
    Ixx_c = Ixx_o_tot - A_tot * Cz ** 2
    c = max(abs(zmax - Cz), abs(zmin - Cz))
    Z = Ixx_c / c if c > 0 else 0.0
    return {"A": A_tot, "Ixx": Ixx_c, "c": c, "Z": Z, "Cz": Cz}


def main():
    print(f"pod_neck 曲げ強度検証 (ARM_MOUNT_HUB_Y={C.ARM_MOUNT_HUB_Y}, "
          f"HEAD_RELIEF_PROTECT_H={MC.HEAD_RELIEF_PROTECT_H}, "
          f"CHAMFER_RUN_MM={MC.CHAMFER_RUN_MM})")
    pn = to_tm(MC.pod_neck())
    print(f"pod_neck bounds (y): [{pn.bounds[0][1]:.1f}, {pn.bounds[1][1]:.1f}]")

    ylo, yhi = pn.bounds[0][1] + 0.3, pn.bounds[1][1] - 0.3
    ys = np.arange(ylo, yhi, Y_STEP)
    rows = []
    for y in ys:
        r = section_modulus_at_y(pn, float(y))
        if r is None or r["Z"] <= 0:
            continue
        arm = abs(y - COG_Y)
        M = F_N * arm
        sigma = M / r["Z"]
        sf = PETG_MPA / sigma
        rows.append((float(y), r["Z"], arm, sigma, sf))

    check(len(rows) > 10, f"全区間 ({ylo:.1f}〜{yhi:.1f}, {Y_STEP}mm刻み) で "
          f"{len(rows)} 断面を評価")

    rows.sort(key=lambda t: t[4])  # SF_nominal 昇順 = 最弱点が先頭
    y0, Z0, arm0, sigma0, sf0 = rows[0]
    print(f"\n[1] 全区間スキャンによる真の最弱点 (σ(y)=M(y)/Z(y) 最大点):")
    print(f"    y={y0:.2f}mm  Z={Z0:.2f}mm^3  arm={arm0:.2f}mm  "
          f"sigma={sigma0:.3f}MPa  SF_nominal={sf0:.3f}")

    sf_eff = {Kt: sf0 / Kt for Kt in KT_RANGE}
    for Kt, sf in sf_eff.items():
        check(sf >= SF_REQUIRED if Kt == max(KT_RANGE) else True,
              f"Kt={Kt}: SF_effective = SF_nominal/Kt = {sf0:.3f}/{Kt} = {sf:.3f} "
              f"({'>=' if sf >= SF_REQUIRED else '<'} 要求安全率{SF_REQUIRED})")

    margin_pct = (sf_eff[max(KT_RANGE)] / SF_REQUIRED - 1.0) * 100
    print(f"\n    最も保守的な Kt={max(KT_RANGE)} での実効安全率 "
          f"{sf_eff[max(KT_RANGE)]:.3f} (マージン {margin_pct:.1f}%)")

    print(f"\n[2] 参考: Z(y) 最小点のみで評価した場合 (旧手法, 過去の docs 記載方式)")
    z_only = min(rows, key=lambda t: t[1])
    print(f"    Z(y) 最小点: y={z_only[0]:.2f}  Z={z_only[1]:.2f}mm^3 "
          f"(σ(y) 最大点 y={y0:.2f} とは{'一致' if abs(z_only[0]-y0) < Y_STEP else '不一致'})")

    print(f"\nresult: {'OK' if OK else 'NG'}")
    return 0 if OK else 1


if __name__ == "__main__":
    sys.exit(main())
