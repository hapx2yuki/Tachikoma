"""NGを表示して正常終了する回帰と、幾何計算失敗の握り潰しを防ぐ。"""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_leg_assembly as leg
import sim_gait as gait


class AuditGateTests(unittest.TestCase):
    def test_boolean_error_is_not_a_clearance(self):
        with patch.object(leg.trimesh.boolean, "intersection", side_effect=RuntimeError("engine error")):
            with self.assertRaisesRegex(RuntimeError, "干渉ブーリアン計算失敗"):
                leg.pair_intersection(None, None)

    def test_nan_volume_is_not_a_clearance(self):
        class InvalidIntersection:
            is_empty = False
            volume = float("nan")
        with patch.object(leg.trimesh.boolean, "intersection", return_value=InvalidIntersection()):
            with self.assertRaisesRegex(ValueError, "干渉体積が不正"):
                leg.pair_intersection(None, None)

    def test_insufficient_torque_fails_full_gait_check(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as output:
            with patch.object(gait, "T_HIP_NG", 0.0):
                result = gait.main(Path(tmp) / "gait.png")
        self.assertEqual(result, 1)
        self.assertIn("RESULT: FAIL: 静的トルク上限", output.getvalue())

    def test_detected_intersection_fails_full_leg_check(self):
        # 実STLを使う校正・組立検査へ、幾何エンジンの「交差あり」を注入する。
        # 正常なexit0を固定値として返す旧mainならこの検査は失敗する。
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as output:
            with patch.object(leg, "pair_intersection", return_value=0.1):
                result = leg.main(Path(tmp) / "leg.png")
        self.assertEqual(result, 1)
        self.assertIn("RESULT: FAIL: 脚内干渉", output.getvalue())


if __name__ == "__main__":
    unittest.main()
