"""電圧の違う電源枝を電力で換算し、未実測を合格にしない電源監査。

出力は設計候補の感度計算であり、サーボの機械出力から電流を推定しない。
部品型番・効率・電流・温度の実測がない現段階では終了2 (UNVERIFIED)。
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hardware/src"))
from config import POWER_AUDIT as P, POWER_COMPONENTS as COMPONENTS


def input_current(servo_a, logic_a, battery_v, servo_eff, logic_eff):
    """Vout*Iout/efficiency を両枝で加算して入力電圧で割る。"""
    import math
    values = (servo_a, logic_a, battery_v, servo_eff, logic_eff)
    if not all(math.isfinite(v) for v in values):
        raise ValueError("電源計算値は有限数が必要")
    if min(servo_a, logic_a) < 0 or battery_v <= 0:
        raise ValueError("電流は0以上、電圧は正が必要")
    if not (0 < servo_eff <= 1 and 0 < logic_eff <= 1):
        raise ValueError("変換効率は0より大きく1以下が必要")
    return (P["servo_v"] * servo_a / servo_eff + P["logic_v"] * logic_a / logic_eff) / battery_v


def report():
    servo = COMPONENTS["DS3218"]
    a, b = servo["points"]
    ratio = (P["servo_v"] - a[0]) / (b[0] - a[0])
    if not 0 <= ratio <= 1:
        raise ValueError("メーカー測定点の範囲外へ外挿しない")
    stall_a = a[2] + ratio * (b[2] - a[2])
    cases = []
    for sv, lo, bv, efficiency in itertools.product(
        P["servo_load_a_cases"], P["logic_load_a_cases"],
        P["battery_v_cases"], P["efficiency_cases"],
    ):
        bat_a = input_current(sv, lo, bv, efficiency, efficiency)
        cases.append(dict(servo_a=sv, logic_a=lo, battery_v=bv,
                          assumed_efficiency=efficiency, input_a=bat_a,
                          exceeds_old_10a_switch=bat_a > P["existing_switch_a"]))
    return {
        "status": "UNVERIFIED", "components": COMPONENTS, "assumptions": P,
        "cases": cases,
        "standard_servos_all_stalled": {
            "interpolated_each_a": stall_a,
            "total_a_excluding_arms_and_eyes": P["standard_servo_count"] * stall_a,
            "method": "メーカー5V/6.8Vの点から6Vを線形補間。実測値でも通常歩行電流でもない",
        },
        "unverified": [
            "実購入サーボ/UBEC型番。LD20MG/LD220MG/HENGEと設計値DS3218/Hobbywingの相違",
            "全関節の無負荷/保持/過渡電流と6V/5V/電池入力の同時測定",
            "2S末期のUBEC負荷時降圧余裕、効率と温度上昇",
            "スイッチDC開閉定格、配線/コネクタ容量、ヒューズ溶断特性",
            "独立停止時のPWM停止と転倒防止。ソフト停止はI2C断線中の出力を消せない",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    values = [x["input_a"] for x in result["cases"]]
    print(f"電池入力の仮定範囲: {min(values):.2f}〜{max(values):.2f} A")
    print(f"標準サーボ12個の同時ストール補間: {result['standard_servos_all_stalled']['total_a_excluding_arms_and_eyes']:.2f} A (腕・目を含まない)")
    print("UNVERIFIED: この計算だけで10A UBEC・10Aスイッチ・15Aヒューズを適合判定しない")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
