"""表示が誤った組立状態や古いメッシュを証拠に使わないための回帰。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "hardware/src")]
import make_visuals as V
import preview_robot
import render_shin_orientation as S
import kit_assembly as K


class RenderContracts(unittest.TestCase):
    def test_head_height_has_one_config_source(self):
        import make_head_eyecut as H
        import export_urdf as E
        self.assertEqual(V.HEAD_TOP_Z_OFFSET, K.C.HEAD_TOP_Z_OFFSET)
        self.assertEqual(E.HEAD_TOP_Z_OFFSET, K.C.HEAD_TOP_Z_OFFSET)
        self.assertEqual(H.HEAD_TOP_Z_OFFSET, K.C.HEAD_TOP_Z_OFFSET)
        with patch.object(K.C, "HEAD_TOP_Z_OFFSET", 51.3):
            p = next(p for p in K._iter_front(K.DATA / "kit_assembly_front.json") if p.part == "Head_Top_Eyecut")
            self.assertEqual(p.t[2], 51.3)

    def test_head_shell_follows_config_and_matches_child_frame(self):
        import export_urdf as E
        for hub_y in (11.0, 7.3):
            with patch.object(K.C, "ARM_MOUNT_HUB_Y", hub_y):
                placements = list(K._iter_front(K.DATA / "kit_assembly_front.json"))
                for name, frame in (("Head_Top_Eyecut", V.trans(0, 0, -V.C.HIP_DROP) @ E.head_top_frame()),
                                    ("Head_Bottom_Blue", V.trans(0, hub_y, -3) @ V.rot(180, "z"))):
                    p = next(p for p in placements if p.part == name)
                    np.testing.assert_allclose(K.trans(*p.t) @ p.R, frame, atol=1e-9)

    def test_mirrored_arm_retains_outward_winding(self):
        right = V.arm_meshes(1, V.ARM_READY, 0, dress=True)
        left = V.arm_meshes(-1, V.ARM_READY, 0, dress=True)
        self.assertEqual(len(right), len(left))
        for (r, _, _), (l, _, _) in zip(right, left):
            self.assertGreater(r.volume, 0)
            self.assertGreater(l.volume, 0)
            self.assertAlmostEqual(l.volume, r.volume, places=5)
            np.testing.assert_allclose(l.vertices, r.vertices * [-1, 1, 1], atol=1e-8)

    def test_replaced_stl_refreshes_in_same_process(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "part.stl"
            trimesh.creation.box([1, 1, 1]).export(p)
            old_time = p.stat().st_mtime_ns
            self.assertAlmostEqual(V.load("part", Path(folder)).volume, 1)
            trimesh.creation.box([2, 1, 1]).export(p)
            os.utime(p, ns=(old_time + 1_000_000, old_time + 1_000_000))
            self.assertAlmostEqual(V.load("part", Path(folder)).volume, 2)

    def test_holding_is_independent_of_gait_phase(self):
        first = V.robot_meshes(0, 0, 0, 0, V.BODY_H, arms=None)
        later = V.robot_meshes(0.43, 0, 0, 0, V.BODY_H, arms=None)
        for (a, _, _), (b, _, _) in zip(first, later):
            np.testing.assert_allclose(a.vertices, b.vertices, atol=1e-8)

    def test_failed_ik_does_not_render_invented_pose(self):
        with patch.object(V, "leg_ik", return_value=None):
            with self.assertRaisesRegex(ValueError, "IK不成立"):
                V.robot_meshes(0, 0, 0, 0, V.BODY_H, arms=None)

    def test_previews_share_current_holding_pose(self):
        meshes = preview_robot.stance_meshes()
        current = V.robot_meshes(0, 0, 0, 0, V.BODY_H, arms=V.ARM_READY, holding=True)
        self.assertEqual(len(meshes), len(current))
        for (_, a), (b, _, _) in zip(meshes, current):
            np.testing.assert_allclose(a.vertices, b.vertices, atol=1e-8)
        for leg in range(4):
            x, y, z = V.foot_target(leg, 0, 0, 0, 0, holding=True)
            np.testing.assert_allclose(S.stance_pose(leg), V.leg_ik(x, y, z), atol=1e-8)

    def test_foot_render_matches_projected_workspace_at_height_limits(self):
        # 支持脚の最下点でなく、足ソケットの座標系そのものをFWの目標と比較する。
        for height in (110.0, 130.0):
            meshes = V.robot_meshes(0, 0, 0, 0, height, arms=None, holding=True)
            for leg in range(4):
                target = V.foot_target(leg, 0, 0, 0, 0, height, holding=True)
                yaw, pitch, knee = V.leg_ik(*target)
                T = (V.trans(V.ORIGIN[leg][0], V.ORIGIN[leg][1], height)
                     @ V.rot(np.degrees(V.MOUNT[leg]) + yaw, "z")
                     @ V.trans(V.C.COXA_LEN, 0, 0) @ V.rot(pitch, "y")
                     @ V.trans(V.C.FEMUR_LEN, 0, 0) @ V.rot(knee, "y")
                     @ V.trans(0, 0, -V.C.TIBIA_LEN))
                expected = V.load("leg_foot_bored")
                expected.apply_transform(T)
                np.testing.assert_allclose(meshes[3 + leg * 6 + 3][0].vertices,
                                           expected.vertices, atol=1e-8)

    def test_asymmetric_internal_cut_keeps_original_reference_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            original = trimesh.creation.box([10, 10, 10])
            original.apply_translation([3, 4, 5])
            original.export(folder / "Mouth_Neck_Blue.stl")
            cut = trimesh.creation.box([7.5, 15, 15])
            cut.apply_translation([0.75, 6, 7.5])
            cut.export(folder / "Mouth_Neck_Bored.stl")
            with patch.object(K, "MODEL", folder), patch.object(K, "STL", folder):
                normalized = K.normalized_mesh("Mouth_Neck_Blue")
                # 加工後bboxを再中心化する旧挙動ならx=0となり、この検査は落ちる。
                np.testing.assert_allclose(normalized.bounds.mean(axis=0), [-3.75, 0, 0])


if __name__ == "__main__":
    unittest.main()
