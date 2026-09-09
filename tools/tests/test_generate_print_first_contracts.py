#!/usr/bin/env python3
"""印刷優先直列入口の失敗閉鎖契約。"""
import ast
from copy import deepcopy
import importlib.util
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = ROOT / "tools/generate_print_first.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_print_first_contracts_impl", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GeneratePrintFirstContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generator = load_generator()
        cls.freeze = cls.generator._initialize_source_freeze()

    def test_public_paths_reject_absolute_parent_and_display_token_inputs(self):
        absolute_tmp = str(Path("/").joinpath("tmp", "out.json"))
        for value in (absolute_tmp, "../outside.json", "outputs/../docs/x.json", "$OUTPUT/foo.stl"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.generator._repo_relative(value, "regression")
        self.assertEqual(
            self.generator._repo_relative("docs/print-first-manifest.json", "regression"),
            "docs/print-first-manifest.json",
        )

    def test_xiao_cli_rejects_equals_form_external_paths(self):
        """The public XIAO entry point must not silently ignore --option=value."""
        import tools.xiao_retention_plan as xiao

        with self.assertRaises(ValueError):
            xiao.main(["--output=" + str(Path("/").joinpath("tmp", "xiao-plan.json")), "--no-meshes"])
        with self.assertRaises(ValueError):
            xiao.main(["--mesh-output=../xiao-candidate", "--no-meshes"])

    def test_public_allowlist_rejects_symlink_paths(self):
        import tools.make_print_first_publication_allowlist as allowlist

        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            explicit_root = Path(temporary)
            link = Path(temporary) / "outside.json"
            link.symlink_to(Path("/", "tmp"))
            relative = link.relative_to(ROOT).as_posix()
            with self.assertRaisesRegex(ValueError, "symlink|outside"):
                allowlist.public_relative_path(relative, label="regression")

            parent_link = Path(temporary) / "parent-link"
            parent_link.symlink_to(Path("/", "tmp"), target_is_directory=True)
            parent_candidate = parent_link / "nested.json"
            parent_relative = parent_candidate.relative_to(ROOT).as_posix()
            with self.assertRaisesRegex(ValueError, "symlink|outside"):
                allowlist.public_relative_path(parent_relative, label="parent-regression")
            with self.assertRaisesRegex(ValueError, "symlink|outside"):
                allowlist._assert_no_symlink_components(
                    parent_candidate, root=explicit_root, label="explicit-parent-regression"
                )

            regular_parent = explicit_root / "regular"
            regular_parent.mkdir()
            regular_file = regular_parent / "file.json"
            regular_file.write_text("{}", encoding="utf-8")
            self.assertIsNone(
                allowlist._assert_no_symlink_components(
                    regular_file, root=explicit_root, label="explicit-root-positive"
                )
            )

    def test_public_allowlist_rejects_windows_drive_paths_with_both_separators(self):
        import tools.make_print_first_publication_allowlist as allowlist

        values = (
            "C:" + "/" + "Users/private.json",
            "C:" + chr(92) + "Users" + chr(92) + "private.json",
        )
        for value in values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "Windows absolute"):
                    allowlist.public_relative_path(value, label="windows-regression")

    def test_status_is_an_explicit_allowlist(self):
        self.generator._assert_status(
            "SERIALIZED_PARTS_MANIFEST", "urdf", "regression")
        with self.assertRaisesRegex(ValueError, "not one of"):
            self.generator._assert_status("WARN", "urdf", "regression")

    def test_profile_status_allowlist_is_the_serialized_candidate_status(self):
        """The profile status and the validator result label are distinct."""
        self.generator._assert_status(
            "CANDIDATE_NOT_ADOPTED", "profile", "regression")
        with self.assertRaisesRegex(ValueError, "not one of"):
            self.generator._assert_status(
                "PASS_SOURCE_CONFIG_BOUND", "profile", "regression")

    def test_source_hash_map_rejects_external_and_traversal_paths(self):
        for path in (str(Path("/").joinpath("tmp", "source.py")), "../source.py"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    self.generator._assert_source_hashes(
                        {"source_sha256": {path: "0" * 64}}, "regression"
                    )

    def test_all_source_freeze_hashes_are_current_and_config_is_bound(self):
        self.assertGreaterEqual(len(self.freeze), 80)
        self.assertIn("model/Head_Top_Blue.stl", self.freeze)
        self.assertNotIn("hardware/stl/tibia_link.stl", self.freeze)
        for required_tool in (
            "tools/check_print_first_feet.py",
            "tools/kit_assembly.py",
            "tools/make_visuals.py",
            "tools/mesh_checks.py",
            "tools/sim_gait.py",
            "tools/data/kit_assembly_front.json",
            "tools/data/kit_assembly_rear.json",
        ):
            with self.subTest(required_tool=required_tool):
                self.assertIn(required_tool, self.freeze)
        self.generator._assert_source_freeze(phase="regression")
        with self.assertRaisesRegex(RuntimeError, "config.py differs|expected"):
            self.generator._assert_config_frozen("0" * 64, phase="regression")

    def test_firmware_config_readers_are_explicitly_in_the_source_closure(self):
        """AST-visible generation readers must be covered by the freeze set."""
        readers = (
            "hardware/src/make_head.py",
            "tools/export_urdf.py",
            "tools/make_visuals.py",
            "tools/sim_gait.py",
        )
        for relative in readers:
            with self.subTest(reader=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                tree = ast.parse(source, filename=relative)
                read_calls = [
                    ast.get_source_segment(source, node) or ""
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "read_text"
                ]
                firmware_reads = [call for call in read_calls if "firmware" in call]
                self.assertTrue(
                    any("config.h" in call for call in firmware_reads),
                    f"{relative} no longer has an AST-visible config.h read",
                )
                self.assertTrue(
                    all("config.h" in call for call in firmware_reads),
                    f"{relative} introduced an unfrozen firmware read: {firmware_reads!r}",
                )
        self.assertEqual(
            set(self.generator.FIRMWARE_SOURCE_FREEZE_STATIC_PATHS),
            {"firmware/src/config.h"},
        )
        self.assertIn("firmware/src/config.h", self.freeze)

    def test_firmware_config_change_between_stage_boundaries_fails_closed(self):
        """The firmware input hash is checked at each stage boundary."""
        original_freeze = dict(self.generator._SOURCE_FREEZE)
        try:
            self.generator._SOURCE_FREEZE["firmware/src/config.h"] = "0" * 64
            with self.assertRaisesRegex(RuntimeError, "firmware/src/config.h"):
                self.generator._assert_source_freeze(phase="firmware-stage-after")
        finally:
            self.generator._SOURCE_FREEZE.clear()
            self.generator._SOURCE_FREEZE.update(original_freeze)

    def test_feet_checker_input_ledger_includes_all_reloaded_meshes(self):
        """Feet verification cannot hide a changed legacy or candidate STL."""
        paths = set(self.generator._stage_input_paths("check_print_first_feet"))
        self.assertIn("hardware/stl", paths)
        self.assertIn("outputs/print-first-20260905/feet/assembly.json", paths)
        self.assertIn(
            "outputs/print-first-20260905/feet/tpu_shoe_foot_frame.stl", paths)
        self.assertNotIn("outputs/print-first-20260905/feet", paths)

    def test_shared_stage_roots_are_explicit_append_only_handoffs(self):
        """Root overlap must carry every prior output into the next stage."""
        for name in ("legacy_build", "print_first_feet"):
            policy = self.generator._stage_output_policy(name)
            self.assertEqual(policy["mode"], "append_only")
            self.assertIsNotNone(policy["next_stage"])
            next_policy = self.generator._stage_output_policy(policy["next_stage"])
            self.assertEqual(next_policy["mode"], "sealed")

    def test_temporary_append_only_handoff_accepts_append_and_rejects_mutation(self):
        """A handoff may add B after A, but may not change A."""
        g = self.generator
        run_id = "e" * 32
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            root = Path(temporary)
            first = root / "A.stl"
            appended = root / "B.stl"
            first.write_bytes(b"A before\n")
            appended.write_bytes(b"B appended\n")
            first_relative = first.relative_to(ROOT).as_posix()
            appended_relative = appended.relative_to(ROOT).as_posix()
            previous = {"outputs": [g._ledger_row(first_relative, first, run_id=run_id)]}
            following = {"inputs": [
                g._ledger_row(first_relative, first, run_id=run_id),
                g._ledger_row(appended_relative, appended, run_id=run_id),
            ]}
            following["outputs"] = list(following["inputs"])
            g._verify_ledger(previous["outputs"], "handoff.previous", run_id=run_id)
            g._verify_ledger(following["inputs"], "handoff.next", run_id=run_id)
            g._assert_stage_handoff(
                previous, following, "stage_a", "stage_b", phase="append-regression")
            following_with_spec = {
                "input_spec": {
                    "handoff_sources": g._stage_handoff_records(
                        [previous], following["inputs"])
                }
            }
            g._assert_stage_handoff_records(
                following_with_spec, [previous], following["inputs"], "handoff.next")
            first.write_bytes(b"A changed by stage B\n")
            with self.assertRaisesRegex(RuntimeError, "SHA-256 changed|size changed"):
                g._verify_ledger(previous["outputs"], "handoff.previous", run_id=run_id)
            changed_output = {
                "outputs": [
                    g._ledger_row(first_relative, first, run_id=run_id),
                    g._ledger_row(appended_relative, appended, run_id=run_id),
                ]
            }
            with self.assertRaisesRegex(RuntimeError, "changed an input path"):
                g._stage_output_delta(
                    following["inputs"], changed_output["outputs"], "handoff output")

    def test_append_only_handoff_rejects_deletion_from_b_outputs(self):
        g = self.generator
        run_id = "f" * 32
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            first = Path(temporary) / "A.stl"
            first.write_bytes(b"A\n")
            relative = first.relative_to(ROOT).as_posix()
            previous = {"outputs": [g._ledger_row(relative, first, run_id=run_id)]}
            next_stage = {
                "inputs": [g._ledger_row(relative, first, run_id=run_id)],
                "outputs": [],
            }
            with self.assertRaisesRegex(RuntimeError, "deleted or mutated"):
                g._assert_stage_handoff(
                    previous, next_stage, "stage_a", "stage_b",
                    phase="negative-output-retention")

    def test_stage_input_spec_keeps_stage_start_expansion_after_root_append(self):
        """Final checks use the saved dynamic-root expansion, not a new scan."""
        g = self.generator
        run_id = "f" * 32
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            root = Path(temporary)
            source = root / "source.py"
            dynamic = root / "dynamic"
            dynamic.mkdir()
            first = dynamic / "A.stl"
            source.write_text("source\n", encoding="utf-8")
            first.write_bytes(b"A\n")
            source_relative = source.relative_to(ROOT).as_posix()
            dynamic_relative = dynamic.relative_to(ROOT).as_posix()
            original_freeze = dict(g._SOURCE_FREEZE)
            original_dynamic = dict(g.STAGE_INPUT_DYNAMIC_ROOTS)
            original_exact = dict(g.STAGE_INPUT_REQUIRED_EXACT)
            try:
                g._SOURCE_FREEZE.clear()
                g._SOURCE_FREEZE[source_relative] = g._sha(source)
                g.STAGE_INPUT_DYNAMIC_ROOTS["head_eyecut"] = (dynamic_relative,)
                g.STAGE_INPUT_REQUIRED_EXACT["head_eyecut"] = ()
                requested = sorted([source_relative, dynamic_relative])
                inputs = g._ledger_for_paths(
                    requested, "dynamic input", run_id=run_id)
                stage = {
                    "input_spec": {
                        "requested_paths": requested,
                        "expanded_paths": [row["path"] for row in inputs],
                        "expanded_sha256": g._ledger_hash_map(inputs),
                        "source_freeze_paths": [source_relative],
                        "dynamic_roots": [dynamic_relative],
                        "required_exact_paths": [],
                        "handoff_sources": [],
                        "dynamic_roots_are_stage_start_only": True,
                    }
                }
                first.write_bytes(b"A retained\n")
                # The changed retained path must fail its saved SHA ledger.
                with self.assertRaisesRegex(RuntimeError, "SHA-256 changed|size changed"):
                    g._verify_ledger(inputs, "dynamic input", run_id=run_id)
                first.write_bytes(b"A\n")
                (dynamic / "B.stl").write_bytes(b"B appended later\n")
                g._verify_ledger(inputs, "dynamic input", run_id=run_id)
                g._assert_stage_input_spec(stage, "head_eyecut", inputs, "dynamic stage")
            finally:
                g._SOURCE_FREEZE.clear()
                g._SOURCE_FREEZE.update(original_freeze)
                g.STAGE_INPUT_DYNAMIC_ROOTS.clear()
                g.STAGE_INPUT_DYNAMIC_ROOTS.update(original_dynamic)
                g.STAGE_INPUT_REQUIRED_EXACT.clear()
                g.STAGE_INPUT_REQUIRED_EXACT.update(original_exact)

    def test_mock_full_run_publishes_shared_root_handoffs(self):
        """A serial run records append-only handoffs before publishing success."""
        g = self.generator
        run_id = "d" * 32
        original_commands = g._stage_commands()
        captured_records = []
        appended_relative = "hardware/stl/_append_only_contract_test.stl"
        appended_path = ROOT / appended_relative
        original_head_contract = g.STAGE_OUTPUT_FILE_CONTRACTS["head_eyecut"]
        g.STAGE_OUTPUT_FILE_CONTRACTS["head_eyecut"] = {
            **original_head_contract,
            "exact": frozenset(set(original_head_contract["exact"]) | {appended_relative}),
        }
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            root = Path(temporary)
            marker = root / "run.incomplete"
            record_path = root / "run.json"

            def fake_marker(payload):
                marker.write_text(json.dumps(payload), encoding="utf-8")

            def stage_output(label, current_run_id):
                rows = g._owned_inventory_for_roots(
                    g.STAGE_OUTPUT_ROOTS[label], require_exists=True,
                    run_id=current_run_id)
                contract = g.STAGE_OUTPUT_FILE_CONTRACTS[label]
                exact = set(contract["exact"])
                patterns = tuple(contract["patterns"])
                return [row for row in rows if (
                    row["path"] in exact
                    or any(pattern.fullmatch(row["path"]) for pattern in patterns)
                )]

            def final_output_rows(*args, **kwargs):
                return g._owned_inventory_for_roots(
                    g.MATERIAL_OUTPUT_ROOTS, require_exists=True, run_id=run_id)
            def fake_run(command, **kwargs):
                # Model a head stage appending one file after its input ledger
                # has been captured.  The next stage may consume it, while the
                # head input ledger must remain the pre-append snapshot.
                if command[1].endswith("tools/make_head_eyecut.py"):
                    appended_path.write_bytes(b"append-only handoff\n")
                return mock.Mock(returncode=0)

            try:
                with mock.patch.object(g.uuid, "uuid4", return_value=mock.Mock(hex=run_id)), \
                        mock.patch.object(g, "MARKER", marker), \
                        mock.patch.object(g, "RECORD", record_path), \
                        mock.patch.object(g, "_create_source_backup", return_value={
                            "status": "BACKUP_COMPLETE", "run_id": run_id,
                            "manifest": "outputs/test-backup/manifest.json",
                            "completeness": {"status": "PASS"},
                        }), \
                        mock.patch.object(g, "_quarantine_previous_owned_outputs",
                                          return_value={
                                              "status": "QUARANTINE_COMPLETE",
                                              "run_id": run_id,
                                              "manifest": "outputs/test-quarantine/manifest.json",
                                          }), \
                        mock.patch.object(g, "_write_record",
                                          side_effect=lambda payload: captured_records.append(
                                              deepcopy(payload))), \
                        mock.patch.object(g, "_write_marker", side_effect=fake_marker), \
                        mock.patch.object(g, "_assert_config_frozen",
                                          return_value=g._SOURCE_FREEZE["hardware/src/config.py"]), \
                        mock.patch.object(g, "_stage_output_ledger", side_effect=stage_output), \
                        mock.patch.object(g.subprocess, "run", side_effect=fake_run), \
                        mock.patch.object(g, "validate_outputs",
                                          return_value={"profile": "PASS_SOURCE_CONFIG_BOUND"}), \
                        mock.patch.object(g, "_assert_owned_outputs_registered",
                                          side_effect=final_output_rows), \
                        mock.patch.object(g, "_assert_run_state_contract"):
                    self.assertEqual(
                        g._run_full_generation("2026-09-06T00:00:00Z"), 0)
            finally:
                appended_path.unlink(missing_ok=True)
                g.STAGE_OUTPUT_FILE_CONTRACTS["head_eyecut"] = original_head_contract

            self.assertFalse(marker.exists())
            self.assertTrue(captured_records)
            published = captured_records[-1]
            self.assertEqual(published["status"], "GENERATED")
            self.assertEqual(published["run_id"], run_id)
            self.assertEqual(len(published["stages"]), len(original_commands))
            self.assertEqual(
                published["stages"][0]["output_policy"]["mode"], "append_only")
            self.assertEqual(
                published["stages"][0]["output_policy"]["next_stage"], "head_eyecut")
            self.assertEqual(
                published["stages"][3]["output_policy"]["next_stage"],
                "check_print_first_feet")
            head_stage = published["stages"][1]
            self.assertNotIn(
                appended_relative, head_stage["input_spec"]["expanded_paths"])
            self.assertIn(appended_relative, head_stage["output_delta"]["new_paths"])

    def test_geometry_claim_cannot_forge_topology_or_volume(self):
        """Manifest topology and volume fields are bound to the STL reload."""
        import trimesh

        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            stl_path = Path(temporary) / "claim.stl"
            trimesh.creation.box(extents=[1, 1, 1]).export(stl_path)
            relative = stl_path.relative_to(ROOT).as_posix()
            actual = self.generator._assert_stl(
                relative, "claim regression", expected_sha=self.generator._sha(stl_path),
                require_sha=True)
            self.generator._assert_geometry_claim(
                {"watertight": True, "solid_components": 1,
                 "volume_mm3": actual["signed_volume_mm3"]},
                actual, "claim regression")
            with self.assertRaisesRegex(ValueError, "watertight"):
                self.generator._assert_geometry_claim(
                    {"watertight": False}, actual, "claim regression")
            with self.assertRaisesRegex(ValueError, "volume_mm3"):
                self.generator._assert_geometry_claim(
                    {"volume_mm3": actual["signed_volume_mm3"] + 1.0},
                    actual, "claim regression")

    def test_source_change_between_stage_boundaries_fails_closed(self):
        """A changed frozen source is a hard failure before later stages run."""
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            path = Path(temporary) / "stage-source.py"
            path.write_text("before\n", encoding="utf-8")
            relative = path.relative_to(ROOT).as_posix()
            original_freeze = dict(self.generator._SOURCE_FREEZE)
            try:
                self.generator._SOURCE_FREEZE.clear()
                self.generator._SOURCE_FREEZE[relative] = self.generator._sha(path)
                path.write_text("changed during stage\n", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "source changed"):
                    self.generator._assert_source_freeze(phase="stage-after")
            finally:
                self.generator._SOURCE_FREEZE.clear()
                self.generator._SOURCE_FREEZE.update(original_freeze)

    def test_source_file_added_between_stage_boundaries_fails_closed(self):
        """A clean-clone source directory addition cannot evade the freeze."""
        with tempfile.TemporaryDirectory(dir=ROOT / "tools") as temporary:
            path = Path(temporary) / "new_runtime_dependency.py"
            path.write_text("VALUE = 1\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "source directory snapshot changed"):
                self.generator._assert_source_freeze(phase="new-source-after-stage")

    def test_generation_lock_rejects_a_competing_invocation(self):
        """Full and validate-only executions share one non-blocking lock."""
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            lock_path = Path(temporary) / "generation.lock"
            with mock.patch.object(self.generator, "LOCK_PATH", lock_path):
                with self.generator._generation_lock():
                    with self.assertRaises(self.generator.RunLockBusy):
                        with self.generator._generation_lock():
                            pass

    def test_read_only_lock_missing_is_fail_closed_without_creating_parent(self):
        """Read-only validation cannot race a full run through a missing lock."""
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            lock_path = Path(temporary) / "missing-parent" / "generation.lock"
            with mock.patch.object(self.generator, "LOCK_PATH", lock_path):
                with self.assertRaises(self.generator.RunLockBusy):
                    with self.generator._generation_lock(read_only=True):
                        pass
            self.assertFalse(lock_path.parent.exists())
            self.assertFalse(lock_path.exists())

    def test_stage_output_contract_rejects_unexpected_debug_stl(self):
        """A debug STL cannot enlarge a stage's exact output set."""
        with self.assertRaisesRegex(RuntimeError, "unexpected"):
            self.generator._assert_stage_output_contract(
                "print_first_feet",
                [{"path": "outputs/print-first-20260905/feet/debug.stl"}],
                phase="negative-extra",
            )

    def test_body_generator_names_match_contract_and_reject_an_extra_stl(self):
        """The body generator's integrated post names are checked without a full STL run."""
        spec = importlib.util.spec_from_file_location(
            "make_print_first_body_contract_impl",
            ROOT / "hardware/src/make_print_first_body.py",
        )
        body = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(body)
        generated = {
            f"outputs/print-first-20260905/body/{name}.stl"
            for name in body.generated_part_names()
        }
        generated.add("outputs/print-first-20260905/body/assembly.json")
        contract = self.generator.STAGE_OUTPUT_FILE_CONTRACTS["print_first_body"]
        self.assertEqual(generated, set(contract["exact"]))
        self.assertNotIn(
            "outputs/print-first-20260905/body/pf_electronics_post_l.stl",
            generated,
        )
        with self.assertRaisesRegex(RuntimeError, "unexpected"):
            self.generator._assert_stage_output_contract(
                "print_first_body",
                [{"path": path} for path in sorted(generated)]
                + [{
                    "path": "outputs/print-first-20260905/body/pf_debug_extra.stl",
                }],
                phase="body-extra-stl",
            )

    def test_urdf_output_contract_rejects_unreferenced_pattern_match(self):
        expected = self.generator._urdf_stage_expected_paths()
        rows = [{"path": path} for path in sorted(expected)]
        rows.append({
            "path": "hardware/urdf-print-first/meshes/base_link__col_999.stl",
        })
        with self.assertRaisesRegex(RuntimeError, "unexpected"):
            self.generator._assert_stage_output_contract(
                "print_first_urdf", rows, phase="negative-urdf-extra")

    def test_xiao_geometry_contract_is_exactly_four_named_checks(self):
        self.assertEqual(
            self.generator.XIAO_EXPECTED_GEOMETRY_CHECK_NAMES,
            frozenset({
                "without_sd_pre_serialization",
                "without_sd_serialized_roundtrip",
                "with_sd_pre_serialization",
                "with_sd_serialized_roundtrip",
            }),
        )

    def test_stage_output_root_is_checked_before_subprocess_write(self):
        """An output root symlink is rejected before a stage can write outside."""
        g = self.generator
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary, \
                tempfile.TemporaryDirectory(dir=ROOT / "outputs") as outside:
            root = Path(temporary) / "redirected-root"
            root.symlink_to(Path(outside))
            relative = root.relative_to(ROOT).as_posix()
            original_roots = dict(g.STAGE_OUTPUT_ROOTS)
            try:
                g.STAGE_OUTPUT_ROOTS["head_eyecut"] = (relative,)
                with self.assertRaisesRegex((ValueError, RuntimeError), "symlink|outside"):
                    g._assert_stage_output_roots_safe(
                        "head_eyecut", phase="before-subprocess")
            finally:
                g.STAGE_OUTPUT_ROOTS.clear()
                g.STAGE_OUTPUT_ROOTS.update(original_roots)

    def test_success_record_and_marker_are_rejected_as_an_invalid_pair(self):
        """A stale marker cannot be hidden behind a GENERATED record."""
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            root = Path(temporary)
            marker = root / "print-first-generation.incomplete"
            record_path = root / "print-first-generation.json"
            run_id = "a" * 32
            record = {
                "schema_version": 3,
                "run_id": run_id,
                "status": "GENERATED",
                "valid_for_consumption": True,
            }
            marker.write_text(json.dumps({
                "schema_version": 3, "run_id": run_id, "status": "FAIL",
                "valid_for_consumption": False,
            }), encoding="utf-8")
            with mock.patch.object(self.generator, "MARKER", marker), \
                    mock.patch.object(self.generator, "RECORD", record_path):
                with self.assertRaisesRegex(RuntimeError, "coexist"):
                    self.generator._assert_run_state_contract(
                        record, run_id, mode="validate-only")

    def test_validate_only_is_read_only_for_a_current_generated_record(self):
        """Read validation leaves the current record untouched and emits no marker."""
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            root = Path(temporary)
            marker = root / "print-first-generation.incomplete"
            record_path = root / "print-first-generation.json"
            run_id = "b" * 32
            record = self.generator._new_generation_record(
                run_id, "2026-09-06T00:00:00Z", self.freeze)
            record.update({
                "status": "GENERATED",
                "valid_for_consumption": True,
                "config_sha256_at_finish": self.freeze["hardware/src/config.py"],
                "source_freeze_sha256_at_finish": dict(self.generator._SOURCE_FREEZE),
                "source_directory_snapshot_at_finish": {
                    path: dict(row)
                    for path, row in self.generator._SOURCE_DIRECTORY_FREEZE.items()
                },
            })
            record["record_digest"]["path"] = (
                record_path.with_name("print-first-generation.digest.json")
                .relative_to(ROOT).as_posix()
            )
            checks = {"profile": "PASS_SOURCE_CONFIG_BOUND"}
            def visible_snapshot(directory):
                snapshot = {}
                for entry in sorted(directory.rglob("*")):
                    relative = entry.relative_to(directory).as_posix()
                    stat = entry.lstat()
                    if entry.is_symlink():
                        snapshot[relative] = ("symlink", entry.readlink().as_posix())
                    elif entry.is_file():
                        snapshot[relative] = (
                            "file", stat.st_size, stat.st_mtime_ns,
                            hashlib.sha256(entry.read_bytes()).hexdigest(),
                        )
                    elif entry.is_dir():
                        snapshot[relative] = ("directory", stat.st_mtime_ns)
                return snapshot
            with mock.patch.object(self.generator, "MARKER", marker), \
                    mock.patch.object(self.generator, "RECORD", record_path), \
                    mock.patch.object(
                        self.generator, "_initialize_source_freeze",
                        return_value=dict(self.generator._SOURCE_FREEZE)), \
                    mock.patch.object(
                        self.generator, "_assert_config_frozen",
                        return_value=self.freeze["hardware/src/config.py"]), \
                    mock.patch.object(self.generator, "_assert_backup_ledger"), \
                    mock.patch.object(self.generator, "_assert_quarantine_ledger"), \
                    mock.patch.object(self.generator, "validate_outputs", return_value=checks), \
                    mock.patch.object(self.generator, "_assert_record_stage_ledgers"), \
                    mock.patch.object(self.generator, "_expected_owned_output_paths", return_value=set()), \
                    mock.patch.object(self.generator, "_assert_owned_outputs_registered", return_value=[]), \
                    mock.patch.object(self.generator, "_assert_final_output_ledger", return_value=[]):
                record_path.write_text(
                    json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
                self.generator._write_record_digest(record)
                before = record_path.read_bytes()
                before_visible = visible_snapshot(root)
                self.assertEqual(self.generator._run_validate_only(), 0)
            self.assertEqual(record_path.read_bytes(), before)
            self.assertFalse(marker.exists())
            self.assertEqual(visible_snapshot(root), before_visible)
            self.assertFalse(any(path.name == "__pycache__" for path in root.rglob("*")))

    def test_validate_outputs_requires_mode_with_supplied_record(self):
        with self.assertRaisesRegex(ValueError, "explicit mode"):
            self.generator.validate_outputs(record={"status": "GENERATED"})

    def test_record_digest_rejects_post_write_record_tampering(self):
        """The sidecar digest catches bytes changed after the record write."""
        g = self.generator
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            root = Path(temporary)
            record_path = root / "print-first-generation.json"
            digest_path = root / "print-first-generation.digest.json"
            run_id = "1" * 32
            record = g._new_generation_record(
                run_id, "2026-09-06T00:00:00Z", self.freeze)
            record["record_digest"]["path"] = digest_path.relative_to(ROOT).as_posix()
            with mock.patch.object(g, "RECORD", record_path):
                record_path.write_text(
                    json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
                g._write_record_digest(record)
                record_path.write_text(
                    record_path.read_text(encoding="utf-8") + "tampered\n",
                    encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "size is stale|does not match record bytes"):
                    g._assert_record_digest(record, run_id, phase="tamper")

    def test_record_checks_must_equal_recomputed_checks(self):
        record = {"checks": {"profile": "PASS"}}
        g = self.generator
        g._assert_record_checks(record, {"profile": "PASS"}, phase="equal")
        with self.assertRaisesRegex(RuntimeError, "differ from recomputation"):
            g._assert_record_checks(record, {"profile": "CHANGED"}, phase="tamper")

    def test_stage_sha_ledger_rejects_mutation_after_recording(self):
        """A later stage cannot silently consume a changed recorded input."""
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            path = Path(temporary) / "input.dat"
            path.write_bytes(b"before")
            relative = path.relative_to(ROOT).as_posix()
            row = self.generator._ledger_row(relative, path, run_id="c" * 32)
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "size changed|SHA-256 changed"):
                self.generator._verify_ledger(
                    [row], "stage.inputs", run_id="c" * 32)

    def test_nested_owned_incomplete_and_unregistered_extra_are_rejected(self):
        """Partial files and files outside the final registration ledger fail closed."""
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            output_root = Path(temporary)
            relative_root = output_root.relative_to(ROOT).as_posix()
            partial = output_root / "nested.incomplete"
            partial.write_text("partial\n", encoding="utf-8")
            with mock.patch.object(self.generator, "MATERIAL_OUTPUT_ROOTS", (relative_root,)):
                with self.assertRaisesRegex(RuntimeError, "incomplete"):
                    self.generator._assert_no_owned_incomplete(phase="nested-marker")
                partial.unlink()
                extra = output_root / "unregistered.stl"
                extra.write_bytes(b"extra")
                with self.assertRaisesRegex(RuntimeError, "extra"):
                    self.generator._assert_owned_outputs_registered(
                        set(), phase="extra-output")

    def test_owned_output_registration_attaches_run_id(self):
        """The final output ledger must carry the active generation run id."""
        run_id = "a" * 32
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            output_root = Path(temporary)
            output_file = output_root / "registered.stl"
            output_file.write_bytes(b"registered\n")
            relative_root = output_root.relative_to(ROOT).as_posix()
            relative_file = output_file.relative_to(ROOT).as_posix()
            with mock.patch.object(self.generator, "MATERIAL_OUTPUT_ROOTS", (relative_root,)):
                rows = self.generator._assert_owned_outputs_registered(
                    {relative_file}, phase="run-id-propagation", run_id=run_id)
            self.assertEqual(rows[0]["path"], relative_file)
            self.assertEqual(rows[0]["run_id"], run_id)

    def test_owned_output_symlink_is_rejected(self):
        """Output roots cannot redirect an inventory outside the repository."""
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary, \
                tempfile.TemporaryDirectory(dir=ROOT / "outputs") as outside:
            root = Path(temporary)
            link = root / "redirect"
            link.symlink_to(Path(outside))
            relative = link.relative_to(ROOT).as_posix()
            with self.assertRaisesRegex((ValueError, RuntimeError), "symlink|outside"):
                self.generator._owned_inventory(relative)

    def test_registered_historical_3mf_is_retained_and_excluded_from_owned_inventory(self):
        """A verified historical plate stays in place and out of output ledgers."""
        g = self.generator
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            fake_root = Path(temporary)
            stl_root = fake_root / "hardware" / "stl"
            stl_root.mkdir(parents=True)
            historical = stl_root / "historical.3mf"
            historical.write_bytes(b"historical plate\n")
            relative = "hardware/stl/historical.3mf"
            digest = hashlib.sha256(historical.read_bytes()).hexdigest()
            with mock.patch.object(g, "ROOT", fake_root), \
                    mock.patch.object(g, "HISTORICAL_3MF_SHA256", {relative: digest}):
                self.assertEqual(
                    g._assert_historical_3mf_contract(phase="historical-retain"),
                    {relative},
                )
                rows = g._owned_inventory_for_roots(
                    ("hardware/stl",), require_exists=True)
                self.assertNotIn(relative, {row["path"] for row in rows})
                self.assertEqual(
                    g._backup_files_under(stl_root, exclude_historical=True), [])
                self.assertTrue(historical.is_file())

    def test_historical_3mf_tampering_is_rejected(self):
        """A registered plate with a changed SHA cannot be carried silently."""
        g = self.generator
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            fake_root = Path(temporary)
            stl_root = fake_root / "hardware" / "stl"
            stl_root.mkdir(parents=True)
            historical = stl_root / "historical.3mf"
            historical.write_bytes(b"historical plate\n")
            relative = "hardware/stl/historical.3mf"
            digest = hashlib.sha256(historical.read_bytes()).hexdigest()
            historical.write_bytes(b"tampered plate\n")
            with mock.patch.object(g, "ROOT", fake_root), \
                    mock.patch.object(g, "HISTORICAL_3MF_SHA256", {relative: digest}):
                with self.assertRaisesRegex(RuntimeError, r"changed=.*historical\.3mf"):
                    g._assert_historical_3mf_contract(phase="historical-tamper")

    def test_unknown_3mf_is_rejected(self):
        """An unregistered 3MF below the owned STL root must fail explicitly."""
        g = self.generator
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            fake_root = Path(temporary)
            stl_root = fake_root / "hardware" / "stl"
            stl_root.mkdir(parents=True)
            historical = stl_root / "historical.3mf"
            historical.write_bytes(b"historical plate\n")
            unknown = stl_root / "unknown.3mf"
            unknown.write_bytes(b"unknown plate\n")
            relative = "hardware/stl/historical.3mf"
            digest = hashlib.sha256(historical.read_bytes()).hexdigest()
            with mock.patch.object(g, "ROOT", fake_root), \
                    mock.patch.object(g, "HISTORICAL_3MF_SHA256", {relative: digest}):
                with self.assertRaisesRegex(RuntimeError, r"extra=.*unknown\.3mf"):
                    g._assert_historical_3mf_contract(phase="historical-extra")

    def test_quarantine_keeps_all_26_verified_historical_3mf_files_in_place(self):
        """The real quarantine path excludes every verified historical plate."""
        g = self.generator
        historical_sha = dict(g.HISTORICAL_3MF_SHA256)
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            fake_root = Path(temporary)
            for relative in sorted(historical_sha):
                target = fake_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / relative).read_bytes())
            backup_root = fake_root / "outputs" / "source-backups"
            output_root = fake_root / "outputs" / "orchestrator"
            with mock.patch.multiple(
                    g,
                    ROOT=fake_root,
                    HISTORICAL_3MF_SHA256=historical_sha,
                    BACKUP_ROOT=backup_root,
                    OUTPUT_ROOT=output_root,
                    BACKUP_MUTABLE_ROOTS=("hardware/stl",),
                    MATERIAL_OUTPUT_ROOTS=("hardware/stl",),
            ):
                run_id = "a" * 32
                backup = g._create_source_backup(run_id)
                quarantine = g._quarantine_previous_owned_outputs(run_id, backup)
                self.assertEqual(quarantine["file_count"], 0)
                self.assertEqual(
                    len(list((fake_root / "hardware" / "stl").glob("*.3mf"))), 26)
                for relative, expected in historical_sha.items():
                    target = fake_root / relative
                    self.assertTrue(target.is_file(), relative)
                    self.assertEqual(g._sha(target), expected, relative)
                quarantine_root = fake_root / quarantine["manifest"].split("/manifest.json")[0]
                self.assertEqual(list(quarantine_root.rglob("*.3mf")), [])

    def test_quarantine_rejects_a_missing_historical_3mf_before_any_move(self):
        """A missing registered plate stops the real quarantine entry point."""
        g = self.generator
        historical_sha = dict(g.HISTORICAL_3MF_SHA256)
        missing_relative = sorted(historical_sha)[0]
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            fake_root = Path(temporary)
            for relative in sorted(historical_sha):
                if relative == missing_relative:
                    continue
                target = fake_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / relative).read_bytes())
            with mock.patch.multiple(
                    g,
                    ROOT=fake_root,
                    HISTORICAL_3MF_SHA256=historical_sha,
                    MATERIAL_OUTPUT_ROOTS=("hardware/stl",),
            ):
                with self.assertRaisesRegex(
                        RuntimeError, rf"missing=.*{re.escape(missing_relative)}"):
                    g._quarantine_previous_owned_outputs("b" * 32, {})

    def test_profile_numeric_contract_rejects_a_changed_constant(self):
        """The profile header is checked against every frozen numeric field."""
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            output = Path(temporary) / "profile.h"
            profile_spec = importlib.util.spec_from_file_location(
                "generate_print_first_profile_contract_impl",
                ROOT / "tools/generate_print_first_profile.py")
            profile = importlib.util.module_from_spec(profile_spec)
            profile_spec.loader.exec_module(profile)
            profile.generate(output)
            text = output.read_text(encoding="utf-8")
            self.generator._assert_profile_numeric_contract(text)
            changed = re.sub(
                r"(PRINT_FIRST_MAX_STEP = )([-+0-9.eE]+)f;",
                lambda match: f"{match.group(1)}{float(match.group(2)) + 0.1:.6f}f;",
                text,
                count=1,
            )
            with self.assertRaisesRegex(ValueError, "PRINT_FIRST_MAX_STEP"):
                self.generator._assert_profile_numeric_contract(changed)

    def test_validate_only_preserves_an_existing_incomplete_marker(self):
        """Validation cannot turn a prior partial generation into a success."""
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            output_root = Path(temporary)
            marker = output_root / "print-first-generation.incomplete"
            record = output_root / "print-first-generation.json"
            marker.write_text("sentinel from failed run\n", encoding="utf-8")
            with mock.patch.object(self.generator, "OUTPUT_ROOT", output_root), \
                    mock.patch.object(self.generator, "MARKER", marker), \
                    mock.patch.object(self.generator, "RECORD", record), \
                    mock.patch.object(
                        self.generator, "_initialize_source_freeze",
                        side_effect=RuntimeError("source changed before validation"),
                    ):
                self.assertEqual(self.generator.main(["--validate-only"]), 1)
            self.assertEqual(marker.read_text(encoding="utf-8"), "sentinel from failed run\n")
            self.assertFalse(record.exists())

    def test_source_manifest_rows_and_hash_map_must_match_the_same_freeze_set(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            path = Path(temporary) / "manifest-source.py"
            path.write_text("source\n", encoding="utf-8")
            relative = path.relative_to(ROOT).as_posix()
            digest = self.generator._sha(path)
            original_freeze = dict(self.generator._SOURCE_FREEZE)
            try:
                self.generator._SOURCE_FREEZE[relative] = digest
                valid = {
                    "source_sha256": {relative: digest},
                    "source_files": [{"path": relative, "exists": True, "sha256": digest}],
                }
                self.generator._assert_source_manifest(valid, "regression", "source_files")
                invalid_exists = {
                    **valid,
                    "source_files": [{"path": relative, "exists": False, "sha256": digest}],
                }
                with self.assertRaisesRegex(ValueError, "exists must be true"):
                    self.generator._assert_source_manifest(
                        invalid_exists, "regression", "source_files")
                invalid_set = {
                    **valid,
                    "source_sha256": {
                        relative: digest,
                        "hardware/src/config.py": self.freeze["hardware/src/config.py"],
                    },
                }
                with self.assertRaisesRegex(ValueError, "different freeze sets"):
                    self.generator._assert_source_manifest(
                        invalid_set, "regression", "source_files")
            finally:
                self.generator._SOURCE_FREEZE.clear()
                self.generator._SOURCE_FREEZE.update(original_freeze)

    def test_backup_ledger_is_complete_and_documents_new_file_cleanup(self):
        """Overwritten roots receive verified objects plus a reversible inventory."""
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            workspace = Path(temporary)
            target = workspace / "mutable"
            target.mkdir()
            source = target / "old.stl"
            source.write_bytes(b"old bytes\n")
            backup_root = workspace / "backup"
            target_relative = target.relative_to(ROOT).as_posix()
            with mock.patch.object(self.generator, "BACKUP_ROOT", backup_root), \
                    mock.patch.object(
                        self.generator, "BACKUP_MUTABLE_ROOTS", (target_relative,)
                    ):
                run_id = "1" * 32
                report = self.generator._create_source_backup(run_id)
                self.generator._assert_backup_ledger(
                    {"backup": report}, run_id, phase="backup-regression")
            manifest_path = ROOT / report["manifest"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "BACKUP_COMPLETE")
            self.assertEqual(manifest["completeness"]["status"], "PASS")
            self.assertEqual(manifest["original_file_count"], 1)
            self.assertEqual(manifest["backup_file_count"], 1)
            row = manifest["files"][0]
            self.assertEqual(row["original_path"], source.relative_to(ROOT).as_posix())
            self.assertTrue(row["verified"])
            backup_object = ROOT / row["backup_object"]
            self.assertEqual(
                hashlib.sha256(backup_object.read_bytes()).hexdigest(), row["sha256"]
            )
            target_record = manifest["target_roots"][0]
            self.assertEqual(target_record["original_file_paths"], [row["original_path"]])
            self.assertIn("一覧にない生成ファイルを削除", " ".join(
                manifest["restore_procedure"]["steps"]
            ))

    def test_restore_round_trip_separates_material_and_control_state(self):
        """Restore rewrites controls while returning every material byte to its old SHA."""
        g = self.generator
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            workspace = Path(temporary)
            material = workspace / "material"
            material.mkdir()
            source = material / "old.dat"
            source.write_bytes(b"old material\n")
            control = workspace / "control"
            control.mkdir()
            record = control / "print-first-generation.json"
            digest = control / "print-first-generation.digest.json"
            marker = control / "print-first-generation.incomplete"
            record.write_bytes(b'{"old_record":true}\n')
            digest.write_bytes(b'{"old_digest":true}\n')
            marker.write_bytes(b'{"old_marker":true}\n')
            material_relative = material.relative_to(ROOT).as_posix()
            record_relative = record.relative_to(ROOT).as_posix()
            digest_relative = digest.relative_to(ROOT).as_posix()
            marker_relative = marker.relative_to(ROOT).as_posix()
            with mock.patch.multiple(
                    g,
                    BACKUP_ROOT=workspace / "source-backups",
                    BACKUP_MUTABLE_ROOTS=(
                        material_relative, record_relative, digest_relative, marker_relative),
                    CONTROL_OUTPUT_ROOTS=(record_relative, digest_relative, marker_relative),
                    OUTPUT_ROOT=workspace / "orchestrator",
                    RECORD=record,
                    RECORD_DIGEST=digest,
                    MARKER=marker,
            ):
                backup = g._create_source_backup("1" * 32)
                source.write_bytes(b"changed during generation\n")
                extra = material / "new-extra.dat"
                extra.write_bytes(b"new extra\n")
                restored = g.restore_from_backup(
                    backup["manifest"], restore_id="2" * 32)
                self.assertEqual(restored["status"], "RESTORED_UNVERIFIED")
                self.assertEqual(source.read_bytes(), b"old material\n")
                self.assertFalse(extra.exists())
                self.assertEqual(g._json(record)["status"], "RESTORED_UNVERIFIED")
                self.assertEqual(g._json(marker), g._json(record))
                self.assertEqual(g._json(digest)["record_status"], "RESTORED_UNVERIFIED")
                ledger_relative = g._json(record)["restore_ledger"]
                ledger = g._json(ROOT / ledger_relative)
                self.assertEqual(ledger["file_count"], 1)
                self.assertEqual(ledger["control_file_count"], 3)
                self.assertEqual(
                    {row["classification"] for row in ledger["files"]},
                    {"non_control_restored"},
                )
                self.assertEqual(
                    {row["classification"] for row in ledger["control_files"]},
                    {"control_replaced"},
                )
                self.assertEqual(
                    len(ledger["files"]) + len(ledger["control_files"]),
                    len(json.loads((ROOT / backup["manifest"]).read_text())["files"]),
                )
                self.assertEqual(g._assert_restore_ledger(ledger_relative), ledger)

    def test_restore_rejects_a_tampered_old_control_object_before_material_restore(self):
        """An old digest object must be verified even though its bytes are not restored."""
        g = self.generator
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            workspace = Path(temporary)
            material = workspace / "material"
            material.mkdir()
            source = material / "old.dat"
            source.write_bytes(b"old\n")
            control = workspace / "control"
            control.mkdir()
            record = control / "print-first-generation.json"
            digest = control / "print-first-generation.digest.json"
            marker = control / "print-first-generation.incomplete"
            record.write_bytes(b"old record\n")
            digest.write_bytes(b"old digest\n")
            marker.write_bytes(b"old marker\n")
            material_relative = material.relative_to(ROOT).as_posix()
            record_relative = record.relative_to(ROOT).as_posix()
            digest_relative = digest.relative_to(ROOT).as_posix()
            marker_relative = marker.relative_to(ROOT).as_posix()
            with mock.patch.multiple(
                    g,
                    BACKUP_ROOT=workspace / "source-backups",
                    BACKUP_MUTABLE_ROOTS=(
                        material_relative, record_relative, digest_relative, marker_relative),
                    CONTROL_OUTPUT_ROOTS=(record_relative, digest_relative, marker_relative),
                    OUTPUT_ROOT=workspace / "orchestrator",
                    RECORD=record,
                    RECORD_DIGEST=digest,
                    MARKER=marker,
            ):
                backup = g._create_source_backup("3" * 32)
                manifest_path = ROOT / backup["manifest"]
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                digest_row = next(
                    row for row in manifest["files"]
                    if row["original_path"] == digest_relative)
                (ROOT / digest_row["backup_object"]).write_bytes(b"tampered old digest")
                source.write_bytes(b"changed\n")
                with self.assertRaisesRegex(RuntimeError, "backup object"):
                    g.restore_from_backup(backup["manifest"], restore_id="4" * 32)
                self.assertEqual(source.read_bytes(), b"changed\n")

    def test_restore_ledger_rejects_mid_restore_material_or_extra_tampering(self):
        """The post-restore audit fails closed when either class of bytes changes."""
        g = self.generator
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            workspace = Path(temporary)
            material = workspace / "material"
            material.mkdir()
            source = material / "old.dat"
            source.write_bytes(b"old\n")
            control = workspace / "control"
            control.mkdir()
            record = control / "print-first-generation.json"
            digest = control / "print-first-generation.digest.json"
            marker = control / "print-first-generation.incomplete"
            record.write_bytes(b"old record\n")
            digest.write_bytes(b"old digest\n")
            marker.write_bytes(b"old marker\n")
            material_relative = material.relative_to(ROOT).as_posix()
            record_relative = record.relative_to(ROOT).as_posix()
            digest_relative = digest.relative_to(ROOT).as_posix()
            marker_relative = marker.relative_to(ROOT).as_posix()
            with mock.patch.multiple(
                    g,
                    BACKUP_ROOT=workspace / "source-backups",
                    BACKUP_MUTABLE_ROOTS=(
                        material_relative, record_relative, digest_relative, marker_relative),
                    CONTROL_OUTPUT_ROOTS=(record_relative, digest_relative, marker_relative),
                    OUTPUT_ROOT=workspace / "orchestrator",
                    RECORD=record,
                    RECORD_DIGEST=digest,
                    MARKER=marker,
            ):
                backup = g._create_source_backup("5" * 32)
                source.write_bytes(b"changed\n")
                extra = material / "extra.dat"
                extra.write_bytes(b"extra\n")
                restored = g.restore_from_backup(
                    backup["manifest"], restore_id="6" * 32)
                ledger_relative = g._json(record)["restore_ledger"]
                ledger = g._json(ROOT / ledger_relative)
                extra_path = ROOT / ledger["extra_quarantined"][0]["quarantine_path"]
                extra_path.write_bytes(b"tampered extra\n")
                with self.assertRaisesRegex(RuntimeError, "restore extra file"):
                    g._assert_restore_ledger(ledger_relative)
                extra_path.write_bytes(b"extra\n")
                source.write_bytes(b"tampered restored material\n")
                with self.assertRaisesRegex(RuntimeError, "restored file is missing or changed"):
                    g._assert_restore_ledger(ledger_relative)

    def test_visual_mesh_records_signed_volume_without_claiming_positive_volume(self):
        import trimesh

        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            path = trimesh.creation.icosphere(subdivisions=1, radius=1.0)
            path.faces = path.faces[:, ::-1]
            stl_path = Path(temporary) / "visual.stl"
            path.export(stl_path)
            relative = stl_path.relative_to(ROOT).as_posix()
            record = self.generator._assert_stl(
                relative, "visual regression", require_solid=False)
            self.assertIn("signed_volume_mm3", record)
            self.assertLess(record["signed_volume_mm3"], 0.0)
            self.assertNotIn("positive_volume_mm3", record)

    def test_generated_stl_parts_require_a_non_null_sha(self):
        """A generated part/output row with sha256=null is never accepted."""
        import trimesh

        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temporary:
            stl_path = Path(temporary) / "solid.stl"
            trimesh.creation.box(extents=[1, 1, 1]).export(stl_path)
            relative = stl_path.relative_to(ROOT).as_posix()
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                self.generator._assert_stl(
                    relative, "generated part", expected_sha=None, require_sha=True)


if __name__ == "__main__":
    unittest.main()
