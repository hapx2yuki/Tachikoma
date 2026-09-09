"""箱枠を残してホーン座を失う変更を、掃引検査が拒否することを確認。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import check_coxa_sweep as S

class CoxaSweepNegativeTests(unittest.TestCase):
    def test_rejects_horn_seat_loss_even_when_servo_frame_remains(self):
        # ソースやSTLは変更せず、そのプロセスのパラメータだけを変える。
        with patch.object(S.C,'COXA_REAR_MIN_X',-1.0):
            result=S.run(source=True,step=.3)
        self.assertLess(result['frame_material_lost_mm3'],.001)
        self.assertGreater(result['horn_seat_lost_mm3'],100)
        self.assertFalse(result['pass'])

if __name__=='__main__':unittest.main()
