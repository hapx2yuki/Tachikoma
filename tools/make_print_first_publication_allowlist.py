#!/usr/bin/env python3
"""freeze2公開候補の相対path・SHA・除外理由を再生成する。

``outputs`` と ``hardware/urdf-print-first`` は再帰追加せず、印刷優先
manifestの採用/保留行と、今回の設計・検査・firmwareに必要なソースを
個別に記録する。外部Issue/Projectの書込みやgit addはこのツールから行わない。
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from print_first_source_closure import PRINT_FIRST_SOURCE_CLOSURE  # noqa: E402

DEFAULT_JSON = ROOT / "docs/audits/20260905-round2/print-first-publication-allowlist-20260906.json"
DEFAULT_MD = ROOT / "docs/audits/20260905-round2/print-first-publication-allowlist-20260906.md"
MANIFEST = ROOT / "docs/print-first-manifest.json"
XIAO_PLAN = ROOT / "docs/audits/20260905-round2/xiao-retention-plan.json"
# Candidate allowlists are content-addressed records.  A wall-clock timestamp
# would make identical source inputs produce different JSON/Markdown bytes and
# would invalidate the recorded SHA on every read-only regeneration.
CANDIDATE_CHECKED_AT = "2026-09-06T00:00:00+09:00"

sys.path.insert(0, str(ROOT / "tools/issues"))
from publication_gate import (  # type: ignore  # noqa: E402
    assert_current_issue_publication,
    assert_no_placeholders,
)

CANDIDATE_DOCUMENTS = (
    "docs/print-first.md",
    "docs/print-first-manifest.json",
    "docs/print-first-manifest.md",
    "docs/print-first-orientation-manifest.json",
    "docs/print-first-orientation-manifest.md",
    "docs/print-first-electrical.md",
    "docs/print-first-simulation.md",
    "docs/print_manifest.md",
    "docs/printing.md",
    "docs/wiring.md",
    "docs/additional-printing.json",
    "docs/additional-printing.md",
    "docs/additional-purchases.json",
    "docs/additional-purchases.md",
    "docs/issues-audit-20260905.md",
    "docs/audits/20260905-round2/README.md",
    "docs/audits/20260905-round2/xiao-retention-plan.json",
    "docs/audits/20260905-round2/xiao-retention-plan.md",
    "docs/audits/20260905-round2/github-issues-refresh-20260906.json",
    "docs/audits/20260905-round2/github-issues-refresh-20260906.md",
    "docs/audits/20260905-round2/github-safe-snapshot-20260906.json",
    "docs/audits/20260905-round2/print-first-finalization-checklist-20260906.md",
    "docs/audits/20260905-round2/print-first-draft-pr-20260906.md",
)

# Compact, human-readable evidence is listed one file at a time.  The large
# raw simulation traces, native outputs, and collision caches remain excluded.
# These are candidate records only until freeze2 is rerun on one SHA set.
CANDIDATE_EVIDENCE_FILES: tuple[tuple[str, str], ...] = (
    ("outputs/print-first-20260905/body/assembly.json", "print_first_body_assembly_candidate"),
    ("outputs/print-first-20260905/body/checks.json", "print_first_body_checks_candidate"),
    ("outputs/print-first-20260905/legs/assembly.json", "print_first_legs_assembly_candidate"),
    ("outputs/print-first-20260905/feet/assembly.json", "print_first_feet_assembly_candidate"),
    ("outputs/print-first-20260905/feet/verification.json", "print_first_feet_verification_candidate"),
    ("outputs/print-first-20260905/feet/verification.log", "print_first_feet_verification_log_candidate"),
    ("outputs/print-first-20260905/feet/generate.log", "print_first_feet_generation_log_candidate"),
    ("outputs/print-first-20260905/check_print_first_body.json", "print_first_body_check_candidate"),
    ("outputs/print-first-20260905/render-freeze2-candidate/assembly-preview.json", "render_candidate_contract"),
    ("outputs/print-first-20260905/mechanical-diagnostics/t0-real-mesh.json", "t0_actual_mesh_summary"),
    ("outputs/print-first-20260905/simulation/recovery/exit-code-external-output.json", "simulation_recovery_summary"),
    ("outputs/print-first-20260905/simulation/recovery/exit-code-external-output.log", "simulation_recovery_log"),
    ("outputs/print-first-20260905/simulation/selected-screen-plan.json", "simulation_selected_screen_plan"),
    ("outputs/print-first-20260905/simulation/report-summary.json", "simulation_candidate_report_summary"),
    ("outputs/print-first-20260905/simulation/comparison.png", "simulation_candidate_comparison_image"),
    ("outputs/print-first-20260905/components/diagnostic.json", "component_occupancy_candidate_summary"),
    ("outputs/print-first-20260905/components/diagnostic.png", "component_occupancy_candidate_image"),
    ("outputs/print-first-20260905/electrical/recovery/build-profile-host-summary.json", "firmware_recovery_summary"),
    ("outputs/print-first-20260905/electrical/recovery/builds-summary.log", "firmware_recovery_build_log"),
    ("outputs/print-first-20260905/electrical/recovery/profile-regression-summary.log", "firmware_recovery_profile_log"),
    ("outputs/print-first-20260905/electrical/recovery/host-regression-summary.log", "firmware_recovery_host_log"),
    ("outputs/print-first-20260905/electrical/verification.json", "firmware_electrical_verification_candidate"),
    ("docs/audits/20260905-round2/final/static-all-pairs.json", "final_static_all_pairs_summary"),
    ("docs/audits/20260905-round2/static-intersection-final-triage.json", "static_intersection_triage_summary"),
    ("docs/audits/20260905-round2/toe-contact-after-scale.json", "toe_contact_summary"),
    ("docs/audits/20260905-round2/simulation/final_refresh_evidence.json", "simulation_final_refresh_summary"),
    ("docs/audits/20260905-round2/simulation/verification-final.json", "simulation_verification_summary"),
    ("docs/audits/20260905-round2/simulation/coverage-validation.json", "simulation_coverage_summary"),
    ("docs/audits/20260905-round2/simulation/contact-probe-cases.json", "simulation_contact_probe_cases"),
    ("docs/audits/20260905-round2/simulation/native-nominal-replay.json", "simulation_native_nominal_replay"),
    ("docs/audits/20260905-round2/simulation/derived-mass-constant-after.json", "simulation_mass_constant_check"),
    ("docs/audits/20260905-round2/simulation/derived-mass-constant-reproduction.json", "simulation_mass_constant_reproduction"),
    ("docs/audits/20260905-round2/simulation/foot-candidate-cases.json", "simulation_foot_candidate_cases"),
    ("docs/audits/20260905-round2/simulation/foot-candidate-summary.json", "simulation_foot_candidate_summary"),
    ("docs/audits/20260905-round2/simulation/joint-convergence-final/summary.json", "simulation_joint_convergence_summary"),
    ("docs/audits/20260905-round2/simulation/joint-dynamics-final/summary.json", "simulation_joint_dynamics_summary"),
    ("docs/audits/20260905-round2/simulation/slope-initialization-final/summary.json", "simulation_slope_initialization_summary"),
    ("docs/audits/20260905-round2/simulation/self_collision_initial.json", "simulation_self_collision_summary"),
    ("docs/audits/20260905-round2/simulation/warning-cleanup.json", "simulation_warning_cleanup_summary"),
    ("docs/audits/20260905-round2/simulation/test-plan.json", "simulation_test_plan"),
    ("docs/audits/20260905-round2/firmware-agent-final-hash-check.json", "firmware_hash_summary"),
)

# Keep the groups explicit.  A future freeze must review a changed path rather
# than silently picking up every file below a large generated directory.
SOURCE_GROUPS: dict[str, tuple[str, ...]] = {
    # Repository-level policy and handoff files are part of the reviewed
    # publication change.  Keeping them in an explicit group prevents a
    # staged README or ignore-rule change from bypassing the same SHA gate as
    # the generators and evidence.
    "repository_context": (
        ".gitignore",
        "AGENTS.md",
        "README.md",
        "docs/HANDOFF.md",
        "docs/assembly.md",
    ),
    "publication_allowlist": (
        "tools/make_print_first_publication_allowlist.py",
        "tools/print_first_source_closure.py",
    ),
    "print_first_generation": (
        "tools/generate_print_first.py",
        "tools/config_contract.py",
        "hardware/src/config.py",
        "hardware/src/build_all.py",
        "hardware/src/arm_shell.py",
        "hardware/src/make_arm.py",
        "hardware/src/make_audio.py",
        "hardware/src/make_camera.py",
        "hardware/src/make_chassis.py",
        "hardware/src/make_eye.py",
        "hardware/src/make_head.py",
        "hardware/src/make_ld220_adapter.py",
        "hardware/src/make_leg.py",
        "hardware/src/make_print_first_body.py",
        "hardware/src/make_print_first_feet.py",
        "hardware/src/make_print_first_leg.py",
        "hardware/src/lib.py",
        "hardware/src/shell_mod.py",
        "tools/print_first_assembly.py",
        "tools/make_head_eyecut.py",
        "tools/print_first_components.py",
        "tools/make_print_first_manifest.py",
        "tools/make_print_orientation_manifest.py",
        "tools/make_print_first_freeze_manifest.py",
        "tools/generate_print_first_profile.py",
        "tools/export_print_first_native_trace.py",
        "tools/render_print_first.py",
    ),
    "mechanism_and_diagnostics": (
        "tools/xiao_retention_plan.py",
        "tools/design_xiao_camera_cradle.py",
        "tools/design_xiao_head_mount.py",
        "tools/audit_mechanical_inventory.py",
        "tools/check_ld220_adapter.py",
        "tools/check_ov3660_full_fov.py",
        "tools/check_print_first_body.py",
        "tools/check_print_first_feet.py",
        "tools/check_print_first_motion_quality.py",
        "tools/check_print_first_native_trace.py",
        "tools/check_print_first_proxy_exclusions.py",
        "tools/check_print_first_t0_mesh.py",
        "tools/diagnose_print_first_initial.py",
        "tools/sim_print_first.py",
        "tools/sim_collision.py",
        "tools/sim_physics.py",
        "tools/sim_self_collision.py",
        "tools/sim_stress.py",
        "tools/sim_yaw_pack_search.py",
        "tools/check_print_artifacts.py",
        "tools/mesh_checks.py",
        "tools/check_toe_contact.py",
        "tools/check_print_strength_sensitivity.py",
        "tools/check_leg_link_strength.py",
        "tools/filament_calc.py",
        "tools/export_urdf.py",
        "tools/check_urdf.py",
        "tools/check_static_assembly.py",
        "tools/check_head_pod_clearance.py",
        "tools/check_pod_neck_strength.py",
        "tools/check_shin_arm_leg.py",
        "tools/check_camera.py",
        "tools/check_audio.py",
        "tools/check_arm.py",
        "tools/check_eye.py",
        "tools/check_mouth_chassis.py",
        "tools/check_leg_assembly.py",
        "tools/check_coxa_sweep.py",
        "tools/check_screw_bosses.py",
        "tools/check_kit_transforms.py",
    ),
    "audit_orchestration": (
        "tools/run_design_audit.py",
        "tools/sim_gait.py",
        "tools/check_power_budget.py",
    ),
    "print_first_contract_tests": (
        "tools/tests/test_print_first_contracts.py",
        "tools/tests/test_print_first_initial_diagnostic.py",
        "tools/tests/test_print_first_motion_quality.py",
        "tools/tests/test_print_first_t0_contracts.py",
        "tools/tests/test_config_contract.py",
        "tools/tests/test_print_first_native_trace.py",
        "tools/tests/simulation_firmware_trace.cpp",
        "tools/tests/simulation_output_trace.cpp",
        "tools/tests/simulation_regression.py",
    ),
    "audit_regression_tests": (
        "tools/tests/test_audit_gates.py",
        "tools/tests/test_issue_sync.py",
        "tools/tests/test_print_artifacts.py",
        "tools/tests/test_render_contracts.py",
        "tools/tests/test_extract_meshes.py",
        "tools/tests/test_mesh_checks.py",
        "tools/tests/test_coxa_sweep.py",
        "tools/tests/test_integration_audit.py",
        "tools/tests/test_stl_export.py",
        "tools/tests/test_export_stl.py",
    ),
    "firmware_host_tests": (
        "tools/tests/firmware_run.py",
        "tools/tests/firmware_calibration_main_test.cpp",
        "tools/tests/firmware_contract_test.cpp",
        "tools/tests/firmware_fault_test.cpp",
        "tools/tests/firmware_main_test.cpp",
        "tools/tests/firmware_audio_test.cpp",
        "tools/tests/firmware_ui_test.cjs",
        "tools/tests/firmware_stubs/WiFi.h",
        "tools/tests/firmware_stubs/ESPAsyncWebServer.h",
        "tools/tests/firmware_stubs/driver/i2s.h",
    ),
    "firmware_print_first": (
        "firmware/platformio.ini",
        "firmware/src/config.h",
        "firmware/src/arms.h",
        "firmware/src/audio.h",
        "firmware/src/control.h",
        "firmware/src/gait.h",
        "firmware/src/main.cpp",
        "firmware/src/peripherals.h",
        "firmware/src/web_ui.h",
        "firmware/src/print_first_gait.h",
        "firmware/src/profile_config.h",
        "firmware/src/servos.h",
    ),
    "issue_publication_plan": (
        "tools/issues/make_print_first_update_plan.py",
        "tools/issues/audit_plan_data.py",
        "tools/issues/plan.py",
        "tools/issues/issue_map.json",
        "tools/issues/issue_publication_data.py",
        "tools/issues/sync_github_issues.py",
        "tools/issues/sync_project.py",
        "tools/issues/fetch_public_snapshot.py",
        "tools/issues/public_purchase_ledger.py",
        "tools/issues/publication_gate.py",
        "tools/issues/update_github_issues.py",
    ),
    "publication_contract_tests": (
        "tools/tests/test_additional_printing_contracts.py",
        "tools/tests/test_additional_purchases_contracts.py",
        "tools/tests/test_additional_purchases_privacy.py",
        "tools/tests/test_generate_print_first_contracts.py",
        "tools/tests/test_issue_append_update.py",
        "tools/tests/test_issue_publication_contracts.py",
        "tools/tests/test_publication_gate.py",
        "tools/tests/test_safe_snapshot_contracts.py",
    ),
}

TEXT_SUFFIXES = {".md", ".json", ".py", ".h", ".cpp", ".c", ".cjs", ".ini", ".urdf"}
FINAL_EVIDENCE_SUFFIXES = {".json", ".png", ".log", ".md", ".urdf"}
FINAL_EVIDENCE_MAX_BYTES = 5 * 1024 * 1024
FINAL_EVIDENCE_FORBIDDEN_PARTS = {
    "collision-cache",
    "public-reproduction",
    "private",
}
SENSITIVE_VALUE_PATTERNS = {
    # A slash after an identifier or another slash is a repository-relative
    # segment/URL path.  The leading-boundary rule catches general POSIX roots
    # such as standard system roots without rejecting HTTPS URLs or
    # ``outputs/private/**`` labels.
    "absolute_workspace_path": re.compile(
        r"(?<![\w:/.])/(?!/)[^\s\"'<>]+"  # source-policy-literal: allowlist-path-patterns
    ),
    "home_path": re.compile(r"(?<![\w])~(?:/|[^\d\s/][^\s/]*/)", re.UNICODE),  # source-policy-literal: allowlist-path-patterns
    "windows_absolute_path": re.compile(r"(?<![\w])[A-Za-z]:[\\/][^\s\"'<>]+"),  # source-policy-literal: allowlist-path-patterns
    "file_uri": re.compile(r"(?i)file://"),  # source-policy-literal: allowlist-path-patterns
    "github_token": re.compile(r"(?:gh[pousr]_|github_pat_|sk-[A-Za-z0-9])"),
    "amazon_order_id": re.compile(r"\b\d{3}-\d{7}-\d{7}\b"),
    "postal_address_value": re.compile(r"\b\d{3}-\d{4}\b"),
}
CACHE_ROOT = "docs/audits/20260905-round2/simulation/collision-cache"
CACHE_GLOB = "**/collision-cache/**"
WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=check
    )


def _git_blob_sha(path: str) -> str | None:
    result = git("show", f"HEAD:{path}", check=False)
    if result.returncode:
        return None
    return sha256_bytes(result.stdout.encode("utf-8"))


def _assert_no_symlink_components(
    candidate: Path, *, root: Path | None = None, label: str = "path"
) -> None:
    """Reject symlinks at the leaf and at every parent component."""
    # Resolve the default at call time so tests and callers can inject an
    # explicit root without being trapped by a definition-time Path value.
    if root is None:
        root = ROOT
    root = root.absolute()
    candidate = candidate.absolute()
    if root.is_symlink():
        raise ValueError(f"{label} repository root is a symlink: {root}")
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} is outside the repository: {candidate}") from exc
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"{label} contains a symlink component: {current}")


def public_relative_path(raw: str, *, label: str = "public path") -> str:
    """Validate a path embedded in a public allowlist.

    CLI output/final-evidence arguments intentionally retain their existing
    absolute internal form; manifest and allowlist members must be explicit
    repository-relative paths and cannot contain traversal components.
    """
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ValueError(f"{label} must be a repository-relative path")
    if WINDOWS_ABSOLUTE_PATH_RE.match(raw):
        raise ValueError(f"{label} must not use a Windows absolute drive path: {raw!r}")
    if "\\" in raw or raw.startswith("~"):
        raise ValueError(f"{label} must use POSIX repository-relative syntax: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"{label} must stay inside the repository: {raw!r}")
    normalized = path.as_posix()
    if normalized != raw:
        raise ValueError(f"{label} is not canonical: {raw!r}")
    candidate = ROOT / normalized
    _assert_no_symlink_components(candidate, label=label)
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(ROOT):
        raise ValueError(f"{label} resolves outside the repository: {raw!r}")
    if resolved.relative_to(ROOT).as_posix() != normalized:
        raise ValueError(f"{label} resolves to a non-canonical repository path: {raw!r}")
    _assert_no_symlink_components(resolved, label=label)
    return normalized


def _record(path: str, *, role: str) -> dict:
    path = public_relative_path(path, label=f"allowlist {role} path")
    file_path = ROOT / path
    result = {"path": path, "role": role, "exists": file_path.is_file()}
    if file_path.is_file():
        result.update({"size_bytes": file_path.stat().st_size, "sha256": sha256(file_path)})
    return result


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _candidate_stls(manifest: dict) -> tuple[list[str], list[dict]]:
    rows = [*manifest["adopted_candidate_stls"], *manifest["pending_integration_candidates"]]
    records: list[dict] = []
    paths: list[str] = []
    for row in rows:
        path = public_relative_path(str(row["path"]), label="manifest STL path")
        if path in paths:
            continue
        paths.append(path)
        record = {
            "id": row.get("id"),
            "label": row.get("label"),
            "path": path,
            "role": row.get("role"),
            "material": row.get("material"),
            "design_quantity": row.get("design_required_quantity", row.get("quantity")),
            "currently_printable_quantity": row.get("currently_printable_quantity"),
            "status": row.get("currently_printable_status"),
            "exists": (ROOT / path).is_file(),
        }
        if record["exists"]:
            record["size_bytes"] = (ROOT / path).stat().st_size
            record["sha256"] = sha256(ROOT / path)
            if row.get("sha256") and row["sha256"] != record["sha256"]:
                raise ValueError(f"manifest STL SHA mismatch: {path}")
        records.append(record)
    if not all(row["exists"] for row in records):
        missing = [row["path"] for row in records if not row["exists"]]
        raise ValueError(f"manifest STL candidate is missing: {missing}")
    return paths, records


def _holder_paths(plan: dict) -> list[str]:
    result: list[str] = []
    meshes = plan.get("holder_meshes", {})
    for group in ("without_sd_candidate", "with_sd_candidate"):
        candidate_rows = meshes.get(group, {})
        if isinstance(candidate_rows, dict):
            candidate_rows = candidate_rows.values()
        for row in candidate_rows:
            path = row.get("path")
            if path:
                result.append(public_relative_path(str(path), label="XIAO holder path"))
    return _unique(result)


def _source_records() -> tuple[list[dict], dict[str, list[str]]]:
    groups: dict[str, list[str]] = {name: list(paths) for name, paths in SOURCE_GROUPS.items()}
    firmware_test_paths = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "firmware/tests").rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix.lower() not in {".pyc", ".pyo"}
    )
    groups["firmware_contract_tests"] = firmware_test_paths
    source_paths = [path for paths in groups.values() for path in paths]
    duplicate_paths = sorted(
        path for path, count in Counter(source_paths).items() if count > 1
    )
    if duplicate_paths:
        raise ValueError(
            "duplicate source record path(s) in SOURCE_GROUPS: "
            + ", ".join(duplicate_paths)
        )
    records: list[dict] = []
    for group, paths in groups.items():
        for path in paths:
            item = _record(path, role=group)
            if not item["exists"]:
                raise ValueError(f"required source is missing: {path}")
            records.append(item)
    record_paths = [str(item["path"]) for item in records]
    if len(record_paths) != len(set(record_paths)):
        raise ValueError("duplicate source record path(s) after record construction")
    missing_canonical = sorted(set(PRINT_FIRST_SOURCE_CLOSURE) - set(record_paths))
    if missing_canonical:
        raise ValueError(
            "canonical print-first source closure is missing from SOURCE_GROUPS: "
            + ", ".join(missing_canonical)
        )
    return records, groups


def _scan_text(paths: Iterable[str]) -> dict:
    # Keep the public purchase-history vocabulary in one place.  The import is
    # deliberately local because this file is itself one of the scanner
    # generators and contains the policy regex literals.
    sys.path.insert(0, str(ROOT / "tools/issues"))
    from public_purchase_ledger import (  # type: ignore
        FORBIDDEN_PUBLIC_PATTERNS,
        find_forbidden,
        normalized_text_variants,
    )

    findings: dict[str, list[str]] = {}
    scanned: list[str] = []
    for relative in _unique(paths):
        path = ROOT / relative
        # These generators contain policy vocabulary (body/order/token), but
        # path values still pass the same scanner.  Only path-pattern policy
        # literals may use the narrow line exceptions below; a real absolute
        # path in source content remains a finding.
        scanner_generator_paths = {
            Path(__file__).resolve(),
            (ROOT / "tools/issues/make_print_first_update_plan.py").resolve(),
            (ROOT / "tools/issues/public_purchase_ledger.py").resolve(),
        }
        scanner_generator = path.resolve() in scanner_generator_paths
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        scanned.append(relative)
        text = path.read_text(encoding="utf-8", errors="replace")
        hits: list[str] = []
        for name, pattern in SENSITIVE_VALUE_PATTERNS.items():
            if scanner_generator and name not in PATH_VALUE_PATTERN_NAMES:
                # The three scanner/generator files must contain the words
                # they reject.  Their absolute-path syntax is still checked
                # below and cannot be hidden by this vocabulary exception.
                continue
            matched = False
            for variant in normalized_text_variants(text):
                for match in pattern.finditer(variant):
                    if not _is_sensitive_path_match(variant, match, name, relative=relative):
                        continue
                    matched = True
                    break
                if matched:
                    break
            if matched:
                hits.append(name)
        # A text document is scanned with the same purchase-history patterns
        # as the public purchase ledger.  For structured JSON, inspect keys as
        # well as values; for source text, only policy-generator files are
        # exempt because they necessarily contain the rejection vocabulary.
        if path.suffix.lower() == ".json":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if not scanner_generator and parsed is not None and find_forbidden(parsed):
                hits.append("public_purchase_history")
        elif not scanner_generator and any(pattern.search(text) for pattern in FORBIDDEN_PUBLIC_PATTERNS):
            hits.append("public_purchase_history")
        if hits:
            findings[relative] = hits
    return {
        "scanned_relative_paths": scanned,
        "findings_by_path": findings,
        "status": "PASS_NO_VALUE_LEAK_FINDINGS" if not findings else "FAIL_VALUE_PATTERN_FOUND",
        "method": "actual value patterns only; explanatory policy words are not findings",
    }


PATH_VALUE_PATTERN_NAMES = frozenset({
    "absolute_workspace_path",
    "home_path",
    "windows_absolute_path",
    "file_uri",
})

# These exact source lines define the policy patterns themselves.  They are
# allowed to contain the syntax being rejected, while every other line in
# these files is scanned normally.  The marker is line-local and never
# permits a value supplied by a user or an API response.
SOURCE_POLICY_LITERAL_MARKERS = {
    "tools/make_print_first_publication_allowlist.py": "# source-policy-literal: allowlist-path-patterns",
    "tools/issues/public_purchase_ledger.py": "# source-policy-literal: purchase-path-patterns",
    "tools/issues/make_print_first_update_plan.py": "# source-policy-literal: update-plan-path-pattern",
}


def _is_sensitive_path_match(
    text: str,
    match: re.Match[str],
    name: str,
    *,
    relative: str | None = None,
) -> bool:
    """Ignore URL/repository syntax while retaining real absolute path values."""
    if name not in PATH_VALUE_PATTERN_NAMES:
        return True
    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.end())
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end]
    value = match.group(0)
    if relative in SOURCE_POLICY_LITERAL_MARKERS and SOURCE_POLICY_LITERAL_MARKERS[relative] in line:
        # The marker is attached to one fixed policy-regex/fixture line.  It
        # is intentionally line-local; a path appended on any other line is
        # still rejected by this scan.
        return False
    # A Unicode NFD combining mark immediately before a slash is the tail of
    # a decomposed word (for example ``サーボ/UBEC``), not an absolute-path
    # boundary.  The NFC variant is scanned separately and still catches any
    # actual NFD path value.
    if match.start() > 0 and unicodedata.combining(text[match.start() - 1]):
        return False
    if line.lstrip().startswith("#!"):
        return False
    if name == "file_uri":
        return True
    if name == "home_path":
        return True
    if name == "windows_absolute_path":
        return True
    if match.start() > 0 and text[match.start() - 1] == "<":
        return False
    if '"json_pointer"' in line or "'json_pointer'" in line:
        return False
    if match.start() > 0 and text[match.start() - 1] == "#":
        return False
    if match.start() > 0 and text[match.start() - 1] == "*":
        return False
    if value == "/dev/null":
        return False
    # URL path fragments such as ``'/blob/main/'`` are not filesystem values.
    # This exception is deliberately line-local and only applies when the
    # same quoted line also contains the URL marker.
    if "/blob/" in value and ("https://" in line or "BASE" in line):
        return False
    first_segment = value.lstrip("/").split("/", 1)[0]
    first_segment = first_segment.rstrip(".,:;)]}>")
    if not first_segment or first_segment in {".", ".."}:
        return False
    # Regex and expression syntax can contain a slash followed by bracket or
    # punctuation.  Such syntax is not a filesystem value; a real POSIX path
    # component starts with a name/underscore/Unicode letter or a dotfile.
    if first_segment[0] in "([{?*+|\\`'\"":
        return False
    common_roots = {
        "etc", "Volumes", "opt", "Library", "Users", "System",
        "Applications", "private", "tmp", "var", "home", "root",
        "usr", "bin", "sbin", "dev", "proc", "run", "workspace",
        "secret", "secrets", "password", "passwords",
    }
    has_multiple_segments = "/" in value.lstrip("/")
    clean_value = value.rstrip(".,;:)]}`'\"。、")
    has_file_suffix = bool(re.search(r"\.[A-Za-z0-9][A-Za-z0-9_-]{0,15}$", clean_value))
    if first_segment not in common_roots and not (has_multiple_segments and has_file_suffix):
        # Source code contains URL routes, regular-expression fragments and
        # arithmetic slash expressions.  Public documents and self blobs do
        # not get this relaxation: an arbitrary absolute path such as
        # an arbitrary multi-segment file path remains a finding there.
        source_paths = {
            path
            for paths in SOURCE_GROUPS.values()
            for path in paths
        }
        if relative in source_paths or (relative or "").startswith("firmware/tests/"):
            return False
        if not has_multiple_segments:
            return False
        if not first_segment.isascii() or not first_segment.replace("_", "").isalnum():
            return False
    # Identifiers such as ``CAM2_SPLIT_*/CAM2_BASE_*`` are configuration
    # expressions, not filesystem roots.  A root beginning with an uppercase
    # identifier and containing an underscore is unambiguously this syntax.
    if first_segment[:1].isupper() and "_" in first_segment:
        return False
    # A formatter/template expression followed by a slash is repository or
    # URL syntax (for example ``{D}/audits/...``), not an absolute value.
    before = text[max(line_start, match.start() - 1):match.start()]
    if before.endswith(("}", ")", "]")) and first_segment not in {
        "etc", "Volumes", "opt", "Library", "Users", "private", "tmp",
        "var", "home", "root", "System", "Applications",
    }:
        return False
    return True


ALLOWLIST_ARTIFACT_PATHS = frozenset({
    "docs/audits/20260905-round2/print-first-publication-allowlist-20260906.json",
    "docs/audits/20260905-round2/print-first-publication-allowlist-20260906.md",
})
PRIVATE_PATH_PREFIXES = ("outputs/private/",)
EXPLICITLY_EXCLUDED_PREFIXES = (
    "outputs/",
    "hardware/urdf-print-first/",
    "docs/audits/20260905-round2/",
)
FORBIDDEN_STAGED_SUFFIXES = {
    ".npz", ".bin", ".elf", ".dylib", ".so", ".exe", ".pyc", ".pyo",
}
PUBLIC_GATE_POLICY_PATHS = frozenset({
    "tools/make_print_first_publication_allowlist.py",
    "tools/issues/make_print_first_update_plan.py",
    "tools/issues/public_purchase_ledger.py",
    "tools/issues/publication_gate.py",
})

PUBLICATION_SELECTED_KEY = "publication_selected_paths"
FINAL_ALLOWLIST_STATUS = "FINAL_FROZEN"
CANDIDATE_ALLOWLIST_STATUS = "CANDIDATE_ALLOWLIST_ROOT_REVIEW_REQUIRED"

COMMIT_SHA_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
DRAFT_PR_URL_RE = re.compile(
    r"^https://github\.com/hapx2yuki/Tachikoma/pull/[1-9][0-9]*$"
)
PUBLICATION_STAGE_ORDER = (
    "evidence_commit_A",
    "draft_pr_from_A",
    "plan_allowlist_commit_B",
    "issue_project_apply_after_B",
)

ALLOWLIST_TOP_LEVEL_KEYS = frozenset({
    "schema_version",
    "checked_at",
    "status",
    "staging",
    "candidate_documents",
    "candidate_evidence_files",
    "final_freeze2_selected_files",
    "candidate_source_files",
    "candidate_source_groups",
    "explicit_print_first_stl_candidates",
    "print_first_stl_candidate_records",
    "new_head_and_pod_stl_candidates",
    "holder_candidate_stl_paths",
    "holder_candidate_stl_records",
    PUBLICATION_SELECTED_KEY,
    "publication_selection_status",
    "publication_workflow",
    "worktree_coverage",
    "tracked_dirty_cache_exclusions",
    "collision_cache_exclusion_audit",
    "content_scan",
    "public_purchase_ledger_validation",
    "public_safety",
    "generated_output_inventory",
    "excluded_patterns",
    "review_actions",
})

RECORD_KEYS = frozenset({"path", "role", "exists", "size_bytes", "sha256"})
FINAL_RECORD_KEYS = RECORD_KEYS | frozenset({"status", "selected_explicitly"})
STL_RECORD_KEYS = frozenset({
    "id", "label", "path", "role", "material", "design_quantity",
    "currently_printable_quantity", "status", "exists", "size_bytes", "sha256",
})
STAGING_KEYS = frozenset({
    "staged_file_count", "staged_numstat_units", "staged_collision_cache_file_count",
    "external_commit_or_push_performed", "read_only_stage_gate",
})
STAGE_AUDIT_KEYS = frozenset({
    "status", "staged_paths", "classification_counts", "classifications",
    "sha_checked", "self_sha_exempt_paths", "self_structure_checked_paths",
    PUBLICATION_SELECTED_KEY, "exact_selected", "require_final",
})
WORKTREE_KEYS = frozenset({
    "status", "changed_paths", "classification_counts", "classifications",
    "private_tree_files",
})
DIR_INVENTORY_KEYS = frozenset({"files", "bytes", "policy"})
PUBLICATION_WORKFLOW_KEYS = frozenset({
    "schema_version", "status", "stage_order", "evidence_commit",
    "draft_pr", "plan_allowlist_commit", "issue_project_update",
    "self_reference_policy",
})


def _publication_workflow_contract(
    status: str,
    *,
    evidence_commit_sha: str | None = None,
    draft_pr_url: str | None = None,
) -> dict[str, Any]:
    """Describe the two-commit publication boundary without self-references.

    Evidence commit A is fixed before the draft PR and final plan.  Commit B
    contains the final plan/allowlist, so its SHA is deliberately absent from
    the plan input and is only read back after the commit exists.
    """
    if status not in {CANDIDATE_ALLOWLIST_STATUS, FINAL_ALLOWLIST_STATUS}:
        raise ValueError(f"unknown allowlist status for publication workflow: {status}")
    if status == CANDIDATE_ALLOWLIST_STATUS:
        if evidence_commit_sha is not None or draft_pr_url is not None:
            raise ValueError("candidate allowlist may not contain final publication references")
    else:
        if not isinstance(evidence_commit_sha, str) or not COMMIT_SHA_RE.fullmatch(evidence_commit_sha):
            raise ValueError("FINAL_FROZEN requires the lowercase evidence commit A SHA")
        if not isinstance(draft_pr_url, str) or not DRAFT_PR_URL_RE.fullmatch(draft_pr_url):
            raise ValueError("FINAL_FROZEN requires the canonical draft PR URL from commit A")
    return {
        "schema_version": 1,
        "status": status,
        "stage_order": list(PUBLICATION_STAGE_ORDER),
        "evidence_commit": {
            "id": "A",
            "sha": evidence_commit_sha,
            "role": "freeze2 evidence and final evidence index",
            "must_not_include_plan_allowlist_commit_sha": True,
        },
        "draft_pr": {
            "depends_on": "evidence_commit_A",
            "url": draft_pr_url,
            "created_from_commit_sha": evidence_commit_sha,
        },
        "plan_allowlist_commit": {
            "id": "B",
            "depends_on": ["evidence_commit_A", "draft_pr_from_A"],
            "sha": None,
            "sha_embedded_in_its_own_plan": False,
        },
        "issue_project_update": {
            "depends_on": [
                "evidence_commit_A", "draft_pr_from_A", "plan_allowlist_commit_B",
            ],
            "issue_count": 107,
            "input_commit_sha_source": "evidence_commit_A",
            "allowlist_commit_sha_readback_only": True,
        },
        "self_reference_policy": {
            "final_plan_commit_sha_embedded": False,
            "allowlist_commit_sha_embedded": False,
            "plan_allowlist_commit_sha_is_readback_only": True,
        },
    }


def _base_declared_public_paths(data: Mapping[str, Any]) -> set[str]:
    """Return the exact path set a freeze may publish or stage.

    The set is intentionally assembled from the generated records rather than
    from a directory glob.  A staged path not in this set is an error even if
    it happens to live below a reviewed directory.
    """
    allowed: set[str] = set(ALLOWLIST_ARTIFACT_PATHS)
    for key in (
        "candidate_documents", "candidate_source_files", "candidate_evidence_files",
        "final_freeze2_selected_files", "print_first_stl_candidate_records",
        "holder_candidate_stl_records",
    ):
        rows = data.get(key) or []
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, Mapping) and row.get("path"):
                    allowed.add(public_relative_path(str(row["path"]), label="declared public path"))
    for key in ("explicit_print_first_stl_candidates", "holder_candidate_stl_paths"):
        rows = data.get(key) or []
        if isinstance(rows, list):
            for path in rows:
                allowed.add(public_relative_path(str(path), label="declared public path"))
    return allowed


def _publication_selected_paths(
    data: Mapping[str, Any], *, require_final: bool = False
) -> set[str] | None:
    """Validate the explicit final publication selection, if one exists.

    A selected path must already be represented by a reviewed record.  This
    prevents the selection field from becoming an escape hatch that declares
    an otherwise unknown path publishable.  Candidate ledgers may omit the
    field (or use an empty list); the exact stage gate requires a non-empty
    final selection containing both self-describing allowlist artifacts.
    """
    raw = data.get(PUBLICATION_SELECTED_KEY)
    if raw is None:
        if require_final:
            raise ValueError(
                "final allowlist must declare publication_selected_paths"
            )
        return None
    if not isinstance(raw, list):
        raise ValueError("publication_selected_paths must be an array")
    base_allowed = _base_declared_public_paths(data)
    selected: list[str] = []
    seen: set[str] = set()
    for value in raw:
        path = public_relative_path(str(value), label="publication selected path")
        if path in seen:
            raise ValueError(f"publication_selected_paths contains duplicate: {path}")
        if path not in base_allowed:
            raise ValueError(
                f"publication selected path has no reviewed allowlist record: {path}"
            )
        seen.add(path)
        selected.append(path)
    result = set(selected)
    if require_final:
        if data.get("status") != FINAL_ALLOWLIST_STATUS:
            raise ValueError(
                "exact publication stage audit requires status FINAL_FROZEN"
            )
        missing_self = sorted(ALLOWLIST_ARTIFACT_PATHS - result)
        if missing_self:
            raise ValueError(
                "publication_selected_paths must include both allowlist artifacts: "
                + ", ".join(missing_self)
            )
        if not result:
            raise ValueError("publication_selected_paths must not be empty in final mode")
    return result


def _declared_public_paths(data: Mapping[str, Any]) -> set[str]:
    """Return the reviewed path set plus a validated explicit selection."""
    allowed = _base_declared_public_paths(data)
    selected = _publication_selected_paths(data)
    if selected:
        allowed.update(selected)
    return allowed


def _declared_sha256(data: Mapping[str, Any]) -> dict[str, str]:
    """Index content SHAs recorded by the allowlist for staged comparison."""
    result: dict[str, str] = {}
    for key in (
        "candidate_documents", "candidate_source_files", "candidate_evidence_files",
        "final_freeze2_selected_files", "print_first_stl_candidate_records",
        "holder_candidate_stl_records",
    ):
        rows = data.get(key) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping) or not row.get("path"):
                continue
            path = public_relative_path(str(row["path"]), label="declared SHA path")
            sha = row.get("sha256")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
                continue
            previous = result.get(path)
            if previous is not None and previous != sha:
                raise ValueError(f"allowlist records conflicting SHA values for {path}")
            result[path] = sha
    return result


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    allowed: Iterable[str],
    label: str,
    *,
    required: Iterable[str] | None = None,
) -> None:
    allowed_set = set(allowed)
    actual = set(value)
    unknown = sorted(actual - allowed_set)
    if unknown:
        raise ValueError(f"{label} contains unknown field(s): {', '.join(unknown)}")
    missing = sorted(set(required if required is not None else allowed_set) - actual)
    if missing:
        raise ValueError(f"{label} is missing field(s): {', '.join(missing)}")


def _require_nonnegative_int(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")


def _require_sha(value: Any, label: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{label} must be a lowercase SHA-256")


def _validate_record_rows(
    rows: Any,
    label: str,
    *,
    keys: frozenset[str] = RECORD_KEYS,
    required: frozenset[str] | None = None,
) -> list[str]:
    if not isinstance(rows, list):
        raise ValueError(f"{label} must be an array")
    required_keys = required or keys
    paths: list[str] = []
    for index, row in enumerate(rows):
        item = _require_mapping(row, f"{label}[{index}]")
        _require_exact_keys(item, keys, f"{label}[{index}]")
        path = public_relative_path(str(item["path"]), label=f"{label}[{index}].path")
        if not isinstance(item["role"], str) or not item["role"].strip():
            raise ValueError(f"{label}[{index}].role must be a non-empty string")
        if not isinstance(item["exists"], bool):
            raise ValueError(f"{label}[{index}].exists must be boolean")
        if item["exists"]:
            _require_nonnegative_int(item.get("size_bytes"), f"{label}[{index}].size_bytes")
            _require_sha(item.get("sha256"), f"{label}[{index}].sha256")
        paths.append(path)
        missing_required = sorted(required_keys - set(item))
        if missing_required:
            raise ValueError(
                f"{label}[{index}] is missing field(s): {', '.join(missing_required)}"
            )
    duplicates = sorted(path for path, count in Counter(paths).items() if count > 1)
    if duplicates:
        raise ValueError(f"{label} contains duplicate path(s): {', '.join(duplicates)}")
    return paths


def _validate_dangerous_field_names(value: Any, path: str = "$") -> None:
    """Reject appended fields that could smuggle raw/private data.

    The allowlist schema below already rejects unknown fields at every fixed
    object.  This recursive check covers dynamic maps such as path/status maps,
    where an attacker could otherwise choose a new key without changing the
    surrounding object shape.
    """
    safe_policy_fields = {
        "raw_issue_bodies_included",
        "token_or_order_details_included",
        "absolute_workspace_paths_included",
        "temporary_reproduction_or_collision_cache_included",
        "private_tree_files",
        "private_source_excluded",
        # Classification maps use these fixed policy labels as keys.  They
        # are not raw private fields and remain covered by the exact schema
        # checks above.
        "private",
        "stage_order",
    }
    dangerous = re.compile(
        r"(?:body|comment|secret|password|api[_-]?key|authorization|cookie|"
        r"order|asin|purchase_lot|delivery_status|file_uri|absolute_path|private)",
        re.IGNORECASE,
    )
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            # ``classifications`` is an intentionally dynamic map whose keys
            # are validated repository-relative paths by the schema below;
            # filenames such as ``make_print_first_body.py`` must not be
            # mistaken for an appended raw body field.
            path_map_key = path.endswith(".classifications") or path.endswith(".findings_by_path")
            if key_text not in safe_policy_fields and dangerous.search(key_text) and not path_map_key:
                raise ValueError(f"allowlist contains dangerous field name at {path}.{key_text}")
            _validate_dangerous_field_names(child, f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_dangerous_field_names(child, f"{path}[{index}]")


def _validate_publication_workflow(value: Any, status: str) -> None:
    workflow = _require_mapping(value, "publication_workflow")
    _require_exact_keys(workflow, PUBLICATION_WORKFLOW_KEYS, "publication_workflow")
    if workflow.get("schema_version") != 1 or workflow.get("status") != status:
        raise ValueError("publication_workflow schema/status does not match allowlist")
    if workflow.get("stage_order") != list(PUBLICATION_STAGE_ORDER):
        raise ValueError("publication_workflow stage order is not the two-stage order")
    evidence = _require_mapping(workflow.get("evidence_commit"), "publication_workflow.evidence_commit")
    _require_exact_keys(
        evidence,
        {"id", "sha", "role", "must_not_include_plan_allowlist_commit_sha"},
        "publication_workflow.evidence_commit",
    )
    if evidence.get("id") != "A" or evidence.get("must_not_include_plan_allowlist_commit_sha") is not True:
        raise ValueError("publication_workflow evidence commit A boundary is invalid")
    draft = _require_mapping(workflow.get("draft_pr"), "publication_workflow.draft_pr")
    _require_exact_keys(
        draft,
        {"depends_on", "url", "created_from_commit_sha"},
        "publication_workflow.draft_pr",
    )
    if draft.get("depends_on") != "evidence_commit_A":
        raise ValueError("draft PR must depend on evidence commit A")
    plan_commit = _require_mapping(
        workflow.get("plan_allowlist_commit"),
        "publication_workflow.plan_allowlist_commit",
    )
    _require_exact_keys(
        plan_commit,
        {"id", "depends_on", "sha", "sha_embedded_in_its_own_plan"},
        "publication_workflow.plan_allowlist_commit",
    )
    if (
        plan_commit.get("id") != "B"
        or plan_commit.get("depends_on") != ["evidence_commit_A", "draft_pr_from_A"]
        or plan_commit.get("sha") is not None
        or plan_commit.get("sha_embedded_in_its_own_plan") is not False
    ):
        raise ValueError("plan/allowlist commit B must remain self-reference free")
    issue_update = _require_mapping(
        workflow.get("issue_project_update"),
        "publication_workflow.issue_project_update",
    )
    _require_exact_keys(
        issue_update,
        {"depends_on", "issue_count", "input_commit_sha_source", "allowlist_commit_sha_readback_only"},
        "publication_workflow.issue_project_update",
    )
    if (
        issue_update.get("depends_on")
        != ["evidence_commit_A", "draft_pr_from_A", "plan_allowlist_commit_B"]
        or issue_update.get("issue_count") != 107
        or issue_update.get("input_commit_sha_source") != "evidence_commit_A"
        or issue_update.get("allowlist_commit_sha_readback_only") is not True
    ):
        raise ValueError("Issue/Project update ordering or source is invalid")
    self_reference = _require_mapping(
        workflow.get("self_reference_policy"),
        "publication_workflow.self_reference_policy",
    )
    _require_exact_keys(
        self_reference,
        {
            "final_plan_commit_sha_embedded",
            "allowlist_commit_sha_embedded",
            "plan_allowlist_commit_sha_is_readback_only",
        },
        "publication_workflow.self_reference_policy",
    )
    if any(self_reference.get(key) is not False for key in (
        "final_plan_commit_sha_embedded", "allowlist_commit_sha_embedded",
    )) or self_reference.get("plan_allowlist_commit_sha_is_readback_only") is not True:
        raise ValueError("publication workflow permits a commit SHA self-reference")
    evidence_sha = evidence.get("sha")
    draft_url = draft.get("url")
    created_from = draft.get("created_from_commit_sha")
    if status == CANDIDATE_ALLOWLIST_STATUS:
        if evidence_sha is not None or draft_url is not None or created_from is not None:
            raise ValueError("candidate publication workflow contains final references")
    else:
        if not isinstance(evidence_sha, str) or not COMMIT_SHA_RE.fullmatch(evidence_sha):
            raise ValueError("FINAL_FROZEN workflow lacks evidence commit A SHA")
        if not isinstance(draft_url, str) or not DRAFT_PR_URL_RE.fullmatch(draft_url):
            raise ValueError("FINAL_FROZEN workflow lacks canonical draft PR URL")
        if created_from != evidence_sha:
            raise ValueError("draft PR source SHA must equal evidence commit A")


def _validate_allowlist_json_structure(data: Mapping[str, Any], *, require_final: bool) -> None:
    """Validate the complete generated JSON schema and its cross-record invariants."""
    _require_exact_keys(data, ALLOWLIST_TOP_LEVEL_KEYS, "allowlist JSON")
    if data.get("schema_version") != 2:
        raise ValueError("allowlist JSON schema_version must be 2")
    status = data.get("status")
    if status not in {CANDIDATE_ALLOWLIST_STATUS, FINAL_ALLOWLIST_STATUS}:
        raise ValueError(f"unknown allowlist JSON status: {status!r}")
    if require_final and status != FINAL_ALLOWLIST_STATUS:
        raise ValueError("final self allowlist must have status FINAL_FROZEN")
    if not isinstance(data.get("checked_at"), str) or not data["checked_at"].strip():
        raise ValueError("allowlist JSON checked_at must be a non-empty string")
    _validate_dangerous_field_names(data)
    _validate_publication_workflow(data.get("publication_workflow"), status)

    document_paths = _validate_record_rows(data.get("candidate_documents"), "candidate_documents")
    source_paths = _validate_record_rows(data.get("candidate_source_files"), "candidate_source_files")
    evidence_paths = _validate_record_rows(data.get("candidate_evidence_files"), "candidate_evidence_files")
    final_paths = _validate_record_rows(
        data.get("final_freeze2_selected_files"),
        "final_freeze2_selected_files",
        keys=FINAL_RECORD_KEYS,
    )
    holder_paths = _validate_record_rows(
        data.get("holder_candidate_stl_records"),
        "holder_candidate_stl_records",
    )
    stl_rows = data.get("print_first_stl_candidate_records")
    if not isinstance(stl_rows, list):
        raise ValueError("print_first_stl_candidate_records must be an array")
    stl_paths: list[str] = []
    for index, row in enumerate(stl_rows):
        item = _require_mapping(row, f"print_first_stl_candidate_records[{index}]")
        _require_exact_keys(item, STL_RECORD_KEYS, f"print_first_stl_candidate_records[{index}]")
        stl_paths.extend(_validate_record_rows(
            [{"path": item["path"], "role": item["role"], "exists": item["exists"],
              "size_bytes": item["size_bytes"], "sha256": item["sha256"]}],
            f"print_first_stl_candidate_records[{index}]",
        ))
        if not isinstance(item.get("id"), str) or not isinstance(item.get("label"), str):
            raise ValueError(f"print_first_stl_candidate_records[{index}] id/label must be strings")
        if not isinstance(item.get("status"), str):
            raise ValueError(f"print_first_stl_candidate_records[{index}].status must be a string")
        if item.get("currently_printable_quantity") is not None:
            _require_nonnegative_int(
                item.get("currently_printable_quantity"),
                f"print_first_stl_candidate_records[{index}].currently_printable_quantity",
            )
    if len(stl_paths) != len(set(stl_paths)):
        raise ValueError("print_first_stl_candidate_records contains duplicate paths")

    groups_value = _require_mapping(data.get("candidate_source_groups"), "candidate_source_groups")
    expected_groups = {name: list(paths) for name, paths in SOURCE_GROUPS.items()}
    expected_groups["firmware_contract_tests"] = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "firmware/tests").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
        and path.suffix.lower() not in {".pyc", ".pyo"}
    )
    if set(groups_value) != set(expected_groups):
        raise ValueError("candidate_source_groups has unknown or missing group(s)")
    flattened_groups: list[str] = []
    for group, expected in expected_groups.items():
        actual = groups_value.get(group)
        if not isinstance(actual, list) or actual != expected:
            raise ValueError(f"candidate_source_groups.{group} differs from SOURCE_GROUPS")
        flattened_groups.extend(actual)
    if len(flattened_groups) != len(set(flattened_groups)):
        duplicate_paths = sorted(
            path for path, count in Counter(flattened_groups).items() if count > 1
        )
        raise ValueError(
            "candidate_source_groups contains duplicate source record path(s): "
            + ", ".join(duplicate_paths)
        )
    if source_paths != flattened_groups:
        raise ValueError("candidate_source_files do not match ordered source groups")

    for key in (
        "explicit_print_first_stl_candidates", "new_head_and_pod_stl_candidates",
        "holder_candidate_stl_paths", PUBLICATION_SELECTED_KEY,
        "excluded_patterns", "review_actions",
    ):
        values = data.get(key)
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise ValueError(f"allowlist JSON field must be a string array: {key}")
        if len(values) != len(set(values)):
            raise ValueError(f"allowlist JSON field contains duplicate entries: {key}")
    if data["holder_candidate_stl_paths"] != holder_paths:
        raise ValueError("holder_candidate_stl_paths do not match holder records")
    if data["explicit_print_first_stl_candidates"] != stl_paths:
        raise ValueError("explicit_print_first_stl_candidates do not match STL records")
    if data["new_head_and_pod_stl_candidates"] != [
        path for path in stl_paths
        if path.endswith("pf_head_top_clearanced.stl")
        or path.endswith("pf_eye_pod_camera_clearanced.stl")
    ]:
        raise ValueError("new_head_and_pod_stl_candidates do not match STL records")

    if data.get("publication_selection_status") != (
        "FINAL_EXACT_REQUIRED" if status == FINAL_ALLOWLIST_STATUS else "CANDIDATE_SUBSET_ALLOWED"
    ):
        raise ValueError("publication_selection_status does not match allowlist status")
    selected = _publication_selected_paths(data, require_final=require_final)
    if status == CANDIDATE_ALLOWLIST_STATUS and selected:
        raise ValueError("candidate allowlist must keep publication_selected_paths empty")

    staging = _require_mapping(data.get("staging"), "staging")
    _require_exact_keys(staging, STAGING_KEYS, "staging")
    for key in ("staged_file_count", "staged_numstat_units", "staged_collision_cache_file_count"):
        _require_nonnegative_int(staging.get(key), f"staging.{key}")
    if staging.get("external_commit_or_push_performed") is not False:
        raise ValueError("allowlist must record no external commit/push")
    stage_gate = _require_mapping(staging.get("read_only_stage_gate"), "staging.read_only_stage_gate")
    _require_exact_keys(stage_gate, STAGE_AUDIT_KEYS, "staging.read_only_stage_gate")
    if stage_gate.get("status") != "PASS":
        raise ValueError("staging read-only gate is not PASS")
    if stage_gate.get("exact_selected") is not False:
        raise ValueError("generated allowlist must record candidate stage audit, not a write")
    for key in ("staged_paths", "self_sha_exempt_paths", "self_structure_checked_paths"):
        if not isinstance(stage_gate.get(key), list) or not all(isinstance(item, str) for item in stage_gate[key]):
            raise ValueError(f"staging.read_only_stage_gate.{key} must be a string array")
    if stage_gate.get("publication_selected_paths") != sorted(selected or set()):
        raise ValueError("staging gate selection does not match publication selection")
    class_counts = _require_mapping(stage_gate.get("classification_counts"), "staging.read_only_stage_gate.classification_counts")
    _require_exact_keys(class_counts, {"public", "explicit_excluded", "private", "unregistered"}, "staging.read_only_stage_gate.classification_counts")
    for key in class_counts:
        _require_nonnegative_int(class_counts[key], f"staging.read_only_stage_gate.classification_counts.{key}")

    worktree = _require_mapping(data.get("worktree_coverage"), "worktree_coverage")
    _require_exact_keys(worktree, WORKTREE_KEYS, "worktree_coverage")
    if worktree.get("status") != "PASS":
        raise ValueError("worktree coverage is not PASS")
    _require_nonnegative_int(worktree.get("changed_paths"), "worktree_coverage.changed_paths")
    coverage_counts = _require_mapping(worktree.get("classification_counts"), "worktree_coverage.classification_counts")
    _require_exact_keys(coverage_counts, {"public", "explicit_excluded", "private", "unregistered"}, "worktree_coverage.classification_counts")
    classifications = _require_mapping(worktree.get("classifications"), "worktree_coverage.classifications")
    if worktree.get("changed_paths") != len(classifications):
        raise ValueError("worktree_coverage changed_paths does not match classifications")
    for path, kind in classifications.items():
        public_relative_path(str(path), label="worktree coverage path")
        if kind not in {"public", "explicit_excluded", "private", "unregistered"}:
            raise ValueError(f"unknown worktree coverage class: {kind}")
    expected_counts = {kind: sum(value == kind for value in classifications.values()) for kind in coverage_counts}
    if dict(coverage_counts) != expected_counts:
        raise ValueError("worktree_coverage classification counts do not match paths")
    private_inventory = _require_mapping(worktree.get("private_tree_files"), "worktree_coverage.private_tree_files")
    _require_exact_keys(private_inventory, {"files", "bytes"}, "worktree_coverage.private_tree_files")
    _require_nonnegative_int(private_inventory.get("files"), "worktree_coverage.private_tree_files.files")
    _require_nonnegative_int(private_inventory.get("bytes"), "worktree_coverage.private_tree_files.bytes")

    for key in ("tracked_dirty_cache_exclusions", "collision_cache_exclusion_audit"):
        block = _require_mapping(data.get(key), key)
        if key == "tracked_dirty_cache_exclusions":
            _require_exact_keys(block, {"tracked_file_count", "tracked_modified_file_count", "modified_numstat", "file_paths_embedded_in_public_record", "decision", "reason"}, key)
            numstat = _require_mapping(block.get("modified_numstat"), f"{key}.modified_numstat")
            _require_exact_keys(numstat, {"added", "deleted"}, f"{key}.modified_numstat")
            for field in ("tracked_file_count", "tracked_modified_file_count", "added", "deleted"):
                _require_nonnegative_int(block.get(field) if field in block else numstat.get(field), f"{key}.{field}")
            if block.get("file_paths_embedded_in_public_record") is not False:
                raise ValueError("tracked cache paths must stay out of public record")
        else:
            _require_exact_keys(block, {"root", "roots", "filesystem_file_count", "filesystem_bytes", "suffix_counts", "all_files_explicitly_excluded", "file_names_embedded_in_public_record", "decision", "reason", "staged_file_count"}, key)
            if block.get("all_files_explicitly_excluded") is not True or block.get("file_names_embedded_in_public_record") is not False:
                raise ValueError("collision cache exclusion flags are unsafe")
            for field in ("filesystem_file_count", "filesystem_bytes", "staged_file_count"):
                _require_nonnegative_int(block.get(field), f"{key}.{field}")
            if block.get("staged_file_count") != 0:
                raise ValueError("collision cache must have zero staged files")

    content_scan = _require_mapping(data.get("content_scan"), "content_scan")
    _require_exact_keys(content_scan, {"scanned_relative_paths", "findings_by_path", "status", "method"}, "content_scan")
    if content_scan.get("status") != "PASS_NO_VALUE_LEAK_FINDINGS" or content_scan.get("findings_by_path") != {}:
        raise ValueError("allowlist content scan is not clean")
    if not isinstance(content_scan.get("scanned_relative_paths"), list) or not all(isinstance(item, str) for item in content_scan["scanned_relative_paths"]):
        raise ValueError("allowlist content scan paths must be a string array")

    purchase = _require_mapping(data.get("public_purchase_ledger_validation"), "public_purchase_ledger_validation")
    _require_exact_keys(purchase, {"status", "path", "markdown_path", "item_count", "category_counts", "immediate_purchase_required", "private_source_excluded"}, "public_purchase_ledger_validation")
    if purchase.get("status") != "PASS" or purchase.get("item_count") != 134 or purchase.get("immediate_purchase_required") != 0 or purchase.get("private_source_excluded") is not True:
        raise ValueError("public purchase ledger validation is unsafe or incomplete")
    public_safety = _require_mapping(data.get("public_safety"), "public_safety")
    _require_exact_keys(public_safety, {"raw_issue_bodies_included", "token_or_order_details_included", "absolute_workspace_paths_included", "temporary_reproduction_or_collision_cache_included"}, "public_safety")
    if any(public_safety.get(key) is not False for key in public_safety):
        raise ValueError("public_safety contains a true leak flag")

    inventory = _require_mapping(data.get("generated_output_inventory"), "generated_output_inventory")
    _require_exact_keys(inventory, {"outputs", "hardware_urdf_print_first", "public_reproduction", "collision_cache"}, "generated_output_inventory")
    for name, block in inventory.items():
        item = _require_mapping(block, f"generated_output_inventory.{name}")
        _require_exact_keys(item, DIR_INVENTORY_KEYS, f"generated_output_inventory.{name}")
        _require_nonnegative_int(item.get("files"), f"generated_output_inventory.{name}.files")
        _require_nonnegative_int(item.get("bytes"), f"generated_output_inventory.{name}.bytes")


def _assert_exact_records(actual: Any, expected: list[dict], label: str) -> None:
    if actual != expected:
        raise ValueError(f"{label} differs from the current reviewed definition")


def _validate_current_allowlist_records(
    data: Mapping[str, Any], *, require_final: bool
) -> None:
    """Rebuild every mutable allowlist input and compare every recorded field."""
    documents = [
        _record(path, role="candidate_document") for path in CANDIDATE_DOCUMENTS
    ]
    if not all(row["exists"] for row in documents):
        raise ValueError("current candidate document definition contains a missing file")
    _assert_exact_records(data.get("candidate_documents"), documents, "candidate_documents")

    sources, groups = _source_records()
    _assert_exact_records(data.get("candidate_source_files"), sources, "candidate_source_files")
    if data.get("candidate_source_groups") != groups:
        raise ValueError("candidate_source_groups differs from current SOURCE_GROUPS")

    evidence = _candidate_evidence_records()
    _assert_exact_records(data.get("candidate_evidence_files"), evidence, "candidate_evidence_files")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    stl_paths, stl_records = _candidate_stls(manifest)
    _assert_exact_records(
        data.get("print_first_stl_candidate_records"),
        stl_records,
        "print_first_stl_candidate_records",
    )
    if data.get("explicit_print_first_stl_candidates") != stl_paths:
        raise ValueError("explicit_print_first_stl_candidates differs from current manifest")
    expected_head = [
        path for path in stl_paths
        if path.endswith("pf_head_top_clearanced.stl")
        or path.endswith("pf_eye_pod_camera_clearanced.stl")
    ]
    if data.get("new_head_and_pod_stl_candidates") != expected_head:
        raise ValueError("new_head_and_pod_stl_candidates differs from current manifest")

    xiao_plan = json.loads(XIAO_PLAN.read_text(encoding="utf-8"))
    holder_paths = _holder_paths(xiao_plan)
    holder_records = [_record(path, role="holder_candidate_stl") for path in holder_paths]
    _assert_exact_records(
        data.get("holder_candidate_stl_records"),
        holder_records,
        "holder_candidate_stl_records",
    )
    if data.get("holder_candidate_stl_paths") != holder_paths:
        raise ValueError("holder_candidate_stl_paths differs from current XIAO plan")

    final_rows = data.get("final_freeze2_selected_files")
    if data.get("status") == CANDIDATE_ALLOWLIST_STATUS:
        if final_rows != []:
            raise ValueError("candidate allowlist may not auto-add final evidence")
    elif data.get("status") == FINAL_ALLOWLIST_STATUS:
        if not isinstance(final_rows, list) or not final_rows:
            raise ValueError("FINAL_FROZEN allowlist requires explicit final evidence rows")
        manifest_rows = [row for row in final_rows if row.get("role") == "final_freeze2_manifest"]
        if len(manifest_rows) != 1:
            raise ValueError("FINAL_FROZEN allowlist requires exactly one freeze manifest row")
        manifest_row = manifest_rows[0]
        manifest_path = public_relative_path(str(manifest_row.get("path")), label="final freeze manifest")
        manifest_file = ROOT / manifest_path
        if not manifest_file.is_file():
            raise ValueError("FINAL_FROZEN freeze manifest is missing")
        manifest_payload = json.loads(manifest_file.read_text(encoding="utf-8"))
        if manifest_payload.get("status") != FINAL_ALLOWLIST_STATUS:
            raise ValueError("FINAL_FROZEN freeze manifest status changed")
        expected_manifest = {
            **_record(manifest_path, role="final_freeze2_manifest"),
            "status": FINAL_ALLOWLIST_STATUS,
            "selected_explicitly": True,
        }
        if manifest_row != expected_manifest:
            raise ValueError("final freeze manifest record differs from current file")
        expected_final_rows = [expected_manifest]
        for row in final_rows:
            if row is manifest_row or row.get("role") == "final_freeze2_manifest":
                continue
            path = public_relative_path(str(row.get("path")), label="final evidence path")
            if path in ALLOWLIST_ARTIFACT_PATHS:
                raise ValueError("allowlist self artifact cannot become final evidence")
            if row.get("role") not in {"final_freeze2_evidence", "final_urdf"}:
                raise ValueError("final evidence row has an unapproved role")
            expected_role = "final_urdf" if path.endswith(".urdf") else "final_freeze2_evidence"
            expected_final_rows.append({
                **_record(path, role=expected_role),
                "status": FINAL_ALLOWLIST_STATUS,
                "selected_explicitly": True,
            })
        if final_rows != expected_final_rows:
            raise ValueError("final evidence rows differ from explicit current files")
    else:
        raise ValueError("allowlist status is invalid for current record comparison")

    # ``require_final`` is a stage-audit policy, while the record comparison
    # also runs for candidate inputs.  Keep the two decisions explicit.
    if require_final and data.get("status") != FINAL_ALLOWLIST_STATUS:
        raise ValueError("final audit requires FINAL_FROZEN current records")


def _allowlist_scan_paths(data: Mapping[str, Any]) -> list[str]:
    """Return the same ordered text inputs used when the allowlist was built."""
    paths: list[str] = []
    for key in (
        "candidate_documents",
        "candidate_source_files",
        "candidate_evidence_files",
        "final_freeze2_selected_files",
    ):
        rows = data.get(key) or []
        if isinstance(rows, list):
            paths.extend(
                str(row["path"])
                for row in rows
                if isinstance(row, Mapping) and row.get("path")
            )
    return paths


def _validate_allowlist_runtime_contents(data: Mapping[str, Any], *, source: str) -> None:
    """Recompute mutable scan/ledger inputs before trusting a self blob."""
    actual_scan = _scan_text(_allowlist_scan_paths(data))
    if actual_scan != data.get("content_scan"):
        raise ValueError(f"{source} content_scan does not match current reviewed inputs")
    actual_purchase = _validate_public_purchase_documents()
    if actual_purchase != data.get("public_purchase_ledger_validation"):
        raise ValueError(
            f"{source} public purchase ledger validation does not match current documents"
        )


def _excluded_path_kind(path: str) -> str | None:
    """Classify an unlisted path for the worktree coverage report."""
    if path.startswith(PRIVATE_PATH_PREFIXES):
        return "private"
    parts = set(PurePosixPath(path).parts)
    if "collision-cache" in parts:
        return "explicit_excluded"
    if path.startswith(EXPLICITLY_EXCLUDED_PREFIXES):
        return "explicit_excluded"
    return None


def classify_changed_path(path: str, allowed: set[str]) -> str:
    """Classify exactly one changed path as public, excluded, or private."""
    normalized = public_relative_path(path, label="changed path")
    if normalized in allowed:
        return "public"
    kind = _excluded_path_kind(normalized)
    if kind is not None:
        return kind
    return "unregistered"


def _git_staged_paths() -> list[str]:
    result = git("diff", "--cached", "--name-only").stdout.splitlines()
    return [path for path in result if path]


def _git_staged_blob(path: str) -> bytes:
    result = subprocess.run(
        ["git", "cat-file", "blob", f":{path}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(f"staged blob is unavailable for {path}")
    return result.stdout


def _staged_text_findings(path: str, content: bytes) -> list[str]:
    """Run value/history/placeholder checks against the staged blob itself."""
    if Path(path).suffix.lower() not in TEXT_SUFFIXES:
        return []
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return ["invalid_utf8"]
    findings: list[str] = []
    for name, pattern in SENSITIVE_VALUE_PATTERNS.items():
        matched = False
        from public_purchase_ledger import normalized_text_variants  # type: ignore
        for variant in normalized_text_variants(text):
            if any(
                _is_sensitive_path_match(variant, match, name, relative=path)
                for match in pattern.finditer(variant)
            ):
                matched = True
                break
        if matched:
            findings.append(name)
    scanner_generator_paths = {
        Path(__file__).resolve().as_posix(),
        (ROOT / "tools/issues/make_print_first_update_plan.py").as_posix(),
        (ROOT / "tools/issues/public_purchase_ledger.py").as_posix(),
    }
    if (ROOT / path).resolve().as_posix() not in scanner_generator_paths \
            and path not in ALLOWLIST_ARTIFACT_PATHS:
        sys.path.insert(0, str(ROOT / "tools/issues"))
        from public_purchase_ledger import FORBIDDEN_PUBLIC_PATTERNS, find_forbidden  # type: ignore
        if Path(path).suffix.lower() == ".json":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                findings.append("invalid_json")
            else:
                if find_forbidden(parsed):
                    findings.append("public_purchase_history")
        elif any(pattern.search(text) for pattern in FORBIDDEN_PUBLIC_PATTERNS):
            findings.append("public_purchase_history")
    return sorted(set(findings))


def _assert_self_blob_safe(
    text: str,
    parsed: Any | None,
    *,
    source: str,
) -> None:
    """Apply the public value/history gate to the self-describing artifacts."""
    findings: list[str] = []
    for name, pattern in SENSITIVE_VALUE_PATTERNS.items():
        from public_purchase_ledger import normalized_text_variants  # type: ignore
        variants = normalized_text_variants(text)
        if any(
            _is_sensitive_path_match(variant, match, name)
            for variant in variants
            for match in pattern.finditer(variant)
        ):
            findings.append(name)
    sys.path.insert(0, str(ROOT / "tools/issues"))
    from public_purchase_ledger import FORBIDDEN_PUBLIC_PATTERNS, find_forbidden  # type: ignore

    if any(pattern.search(text) for pattern in FORBIDDEN_PUBLIC_PATTERNS):
        findings.append("public_purchase_history")
    if parsed is not None and find_forbidden(parsed):
        findings.append("public_purchase_history")
    if findings:
        raise ValueError(
            f"{source} self allowlist value/history gate failed: "
            + ", ".join(sorted(set(findings)))
        )
    assert_no_placeholders(parsed if parsed is not None else text, source=source)
    assert_current_issue_publication(parsed if parsed is not None else text, source=source)


def _validate_self_allowlist_blob(
    path: str,
    content: bytes,
    data: Mapping[str, Any],
    *,
    require_final: bool,
) -> None:
    """Validate an allowlist artifact without comparing its self-SHA.

    The allowlist records their own paths, so a direct content SHA comparison
    would be recursive.  Instead, the staged bytes must match the current
    worktree artifact, have the expected schema/state, and contain no raw
    Issue body/comment fields.  The ordinary selected records are rescanned
    independently so a self-edited ``content_scan`` cannot claim a clean set.
    """
    worktree_path = ROOT / path
    if not worktree_path.is_file():
        raise ValueError(f"self allowlist artifact is missing from worktree: {path}")
    if content != worktree_path.read_bytes():
        raise ValueError(
            f"staged self allowlist artifact differs from worktree input: {path}"
        )

    if path.endswith(".json"):
        try:
            text = content.decode("utf-8")
            parsed = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"staged self allowlist JSON is invalid: {path}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"staged self allowlist JSON must be an object: {path}")
        _assert_self_blob_safe(text, parsed, source=f"staged:{path}")
        _validate_allowlist_json_structure(parsed, require_final=require_final)
        _validate_current_allowlist_records(parsed, require_final=require_final)
        _validate_allowlist_runtime_contents(parsed, source=f"staged:{path}")
        # Semantic equality permits harmless formatting changes while stopping
        # an arbitrary self blob from passing merely because its SHA is exempt.
        if parsed != dict(data):
            raise ValueError(f"staged self allowlist JSON differs from loaded input: {path}")
    else:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"staged self allowlist Markdown is not UTF-8: {path}") from exc
        _assert_self_blob_safe(text, None, source=f"staged:{path}")
        _validate_allowlist_json_structure(data, require_final=require_final)
        json_path = worktree_path.with_suffix(".json")
        if not json_path.is_file():
            raise ValueError(f"self allowlist Markdown has no sibling JSON: {path}")
        try:
            sibling_text = json_path.read_text(encoding="utf-8")
            sibling = json.loads(sibling_text)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"self allowlist sibling JSON is invalid: {json_path}: {exc}") from exc
        if not isinstance(sibling, dict):
            raise ValueError(f"self allowlist sibling JSON must be an object: {json_path}")
        _assert_self_blob_safe(sibling_text, sibling, source=f"sibling:{json_path}")
        _validate_allowlist_json_structure(sibling, require_final=require_final)
        _validate_current_allowlist_records(sibling, require_final=require_final)
        _validate_allowlist_runtime_contents(sibling, source=f"sibling:{json_path}")
        if sibling != dict(data):
            raise ValueError(f"self allowlist Markdown sibling JSON differs from input: {path}")
        if text != render_markdown(sibling):
            raise ValueError(
                f"self allowlist Markdown is not the deterministic rendering of sibling JSON: {path}"
            )
        expected_status = data.get("status")
        if f"状態: `{expected_status}`" not in text:
            raise ValueError(f"staged self allowlist Markdown state is missing: {path}")
        if "詳細JSON: `print-first-publication-allowlist-20260906.json`" not in text:
            raise ValueError(f"staged self allowlist Markdown JSON link is missing: {path}")
        if require_final and expected_status != FINAL_ALLOWLIST_STATUS:
            raise ValueError(f"staged self allowlist Markdown is not FINAL_FROZEN: {path}")


def audit_staged_paths(
    data: Mapping[str, Any],
    *,
    staged_paths: Iterable[str] | None = None,
    blob_reader=None,
    require_final: bool = True,
    exact_selected: bool = False,
    validate_structure: bool = True,
) -> dict[str, Any]:
    """Read-only stage gate with path, blob-SHA, and value checks.

    Candidate audits accept a staged subset of reviewed paths.  Final audits
    pass ``exact_selected=True`` and require the index to equal the explicit
    ``publication_selected_paths`` set, including both allowlist artifacts.

    ``blob_reader`` is injectable for contract tests and must return staged
    bytes.  The production default uses ``git cat-file blob :path``; it never
    changes the index, worktree, or allowlist files.
    """
    allowed = _declared_public_paths(data)
    if validate_structure and data.get("schema_version") == 2 and "candidate_documents" in data:
        _validate_allowlist_json_structure(data, require_final=require_final)
        _validate_current_allowlist_records(data, require_final=require_final)
    selected = _publication_selected_paths(data, require_final=exact_selected)
    expected_sha = _declared_sha256(data)
    staged = list(staged_paths) if staged_paths is not None else _git_staged_paths()
    read_blob = blob_reader or _git_staged_blob
    classifications: dict[str, str] = {}
    sha_checked: dict[str, str] = {}
    findings: dict[str, list[str]] = {}
    self_checked: list[str] = []
    for raw_path in staged:
        path = public_relative_path(str(raw_path), label="staged path")
        if path in classifications:
            raise ValueError(f"staged path appears more than once: {path}")
        kind = classify_changed_path(path, allowed)
        classifications[path] = kind
        if kind != "public":
            raise ValueError(f"staged path is {kind} and cannot be published: {path}")
        if Path(path).suffix.lower() in FORBIDDEN_STAGED_SUFFIXES:
            raise ValueError(f"generated/binary artifact is staged: {path}")
        blob = read_blob(path)
        if not isinstance(blob, (bytes, bytearray)):
            raise ValueError(f"staged blob reader did not return bytes: {path}")
        blob = bytes(blob)
        # The two allowlist files contain the records that describe their own
        # path.  Comparing those self-SHAs would create a regeneration cycle;
        # all other public paths must match their recorded content SHA.
        if path not in ALLOWLIST_ARTIFACT_PATHS:
            expected = expected_sha.get(path)
            if expected is None:
                raise ValueError(f"staged public path has no allowlist SHA: {path}")
            actual = sha256_bytes(blob)
            sha_checked[path] = actual
            if actual != expected:
                raise ValueError(
                    f"staged blob SHA differs from allowlist for {path}: {actual} != {expected}"
                )
        else:
            sha_checked[path] = sha256_bytes(blob)
        if path in ALLOWLIST_ARTIFACT_PATHS:
            _validate_self_allowlist_blob(
                path, blob, data, require_final=require_final
            )
            self_checked.append(path)
        else:
            text_findings = _staged_text_findings(path, blob)
            if text_findings:
                findings[path] = text_findings
        # Policy/generator sources contain the literal rejection patterns and
        # unresolved template examples that implement the gate itself.  They
        # are reviewed as source code; generated/public documents still pass
        # the full placeholder and stale-reference checks below.
        # Placeholder/stale-reference checks apply to publishable documents.
        # Source files and contract tests intentionally contain policy regexes
        # and negative-test literals; their source-level review is covered by
        # the allowlist SHA, while generated Markdown/JSON must be concrete.
        if require_final and Path(path).suffix.lower() in {".md", ".json"} and path not in PUBLIC_GATE_POLICY_PATHS:
            try:
                value = json.loads(blob.decode("utf-8")) if Path(path).suffix.lower() == ".json" else blob.decode("utf-8")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"staged public text is not readable: {path}: {exc}") from exc
            assert_no_placeholders(value, source=f"staged:{path}")
            assert_current_issue_publication(value, source=f"staged:{path}")
    staged_set = set(classifications)
    if exact_selected:
        assert selected is not None
        missing = sorted(selected - staged_set)
        extra = sorted(staged_set - selected)
        if missing or extra:
            details = []
            if missing:
                details.append("missing selected paths: " + ", ".join(missing[:20]))
            if extra:
                details.append("staged paths not selected: " + ", ".join(extra[:20]))
            raise ValueError("exact final publication stage mismatch: " + "; ".join(details))
    if findings:
        detail = "; ".join(f"{path}: {', '.join(values)}" for path, values in findings.items())
        raise ValueError("staged public value/history gate failed: " + detail)
    counts = {kind: sum(value == kind for value in classifications.values()) for kind in ("public", "explicit_excluded", "private", "unregistered")}
    return {
        "status": "PASS",
        "staged_paths": sorted(classifications),
        "classification_counts": counts,
        "classifications": classifications,
        "sha_checked": sha_checked,
        "self_sha_exempt_paths": sorted(set(staged) & ALLOWLIST_ARTIFACT_PATHS),
        "self_structure_checked_paths": sorted(self_checked),
        "publication_selected_paths": sorted(selected) if selected is not None else None,
        "exact_selected": exact_selected,
        "require_final": require_final,
    }


def _worktree_changed_paths() -> list[str]:
    """Read changed paths without modifying the index or worktree."""
    lines = git("status", "--porcelain=v1", "--untracked-files=all").stdout.splitlines()
    paths: list[str] = []
    for line in lines:
        if len(line) < 4:
            continue
        raw = line[3:]
        # Rename records are ``old -> new``; the destination is the changed
        # path that must be covered by the allowlist.
        if " -> " in raw:
            raw = raw.rsplit(" -> ", 1)[1]
        if raw:
            paths.append(raw)
    return paths


def audit_worktree_coverage(data: Mapping[str, Any], paths: Iterable[str] | None = None) -> dict[str, Any]:
    """Classify every visible worktree change exactly once.

    Unselected generated files are explicitly excluded, while an ordinary
    source/document path missing from the allowlist is an immediate failure.
    This report is independent from the stage check so it remains useful
    before root performs any ``git add``.
    """
    allowed = _declared_public_paths(data)
    rows: dict[str, str] = {}
    for raw_path in paths if paths is not None else _worktree_changed_paths():
        path = public_relative_path(str(raw_path), label="worktree path")
        if path in rows:
            raise ValueError(f"worktree path appears more than once: {path}")
        rows[path] = classify_changed_path(path, allowed)
    unknown = sorted(path for path, kind in rows.items() if kind == "unregistered")
    if unknown:
        raise ValueError("changed path is missing from publication/exclusion classification: " + ", ".join(unknown[:20]))
    counts = {kind: sum(value == kind for value in rows.values()) for kind in ("public", "explicit_excluded", "private", "unregistered")}
    return {
        "status": "PASS",
        "changed_paths": len(rows),
        "classification_counts": counts,
        "classifications": rows,
        "private_tree_files": _inventory("outputs/private"),
    }


def _cached_dirty_summary() -> dict:
    """Record preserved cache dirtiness without publishing cache member names."""
    tracked_paths = [
        line for line in git("ls-files").stdout.splitlines()
        if "collision-cache" in Path(line).parts
    ]
    numstat_rows = [
        line for line in git("diff", "--numstat").stdout.splitlines()
        if len(line.split("\t")) >= 3
        and "collision-cache" in Path(line.split("\t", 2)[2]).parts
    ]
    added = 0
    deleted = 0
    modified_file_count = 0
    for line in numstat_rows:
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        modified_file_count += 1
        if fields[0].isdigit():
            added += int(fields[0])
        if fields[1].isdigit():
            deleted += int(fields[1])
    return {
        "tracked_file_count": len(tracked_paths),
        "tracked_modified_file_count": modified_file_count,
        "modified_numstat": {"added": added, "deleted": deleted},
        "file_paths_embedded_in_public_record": False,
        "decision": "preserve_uncommitted_exclude_from_publication_allowlist",
        "reason": "既存作業ツリー差分を消さず、衝突計算キャッシュ配下の全ファイル名を公開候補へ含めない",
    }


def _inventory(path: str) -> dict:
    root = ROOT / path
    files = [item for item in root.rglob("*") if item.is_file()] if root.is_dir() else []
    return {"files": len(files), "bytes": sum(item.stat().st_size for item in files)}


def _candidate_evidence_records() -> list[dict]:
    """Rebuild the fixed candidate evidence definition, without discovery."""
    records: list[dict] = []
    for path, role in CANDIDATE_EVIDENCE_FILES:
        item = _record(path, role=role)
        if not item["exists"]:
            raise ValueError(f"candidate evidence is missing: {path}")
        records.append(item)
    return records


def _final_evidence_records(
    paths: Iterable[Path] | None,
    final_freeze_manifest: Path | None = None,
) -> list[dict]:
    """Validate explicitly selected final evidence without recursive discovery.

    Final freeze evidence is intentionally opt-in.  A directory, a candidate
    result, a cache member, or a large raw output must fail instead of being
    silently copied into the publication set.
    """
    records: list[dict] = []
    seen: set[str] = set()
    if final_freeze_manifest is None:
        raise ValueError("FINAL_FROZEN evidence requires an explicit freeze manifest")
    manifest_path = final_freeze_manifest if final_freeze_manifest.is_absolute() else ROOT / final_freeze_manifest
    manifest_relative = root_relative(manifest_path)
    manifest_path = ROOT / manifest_relative
    if not manifest_path.is_file():
        raise ValueError(f"final freeze2 manifest is missing: {manifest_path}")
    try:
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"final freeze2 manifest is not readable: {manifest_path}: {exc}") from exc
    if manifest_payload.get("status") != FINAL_ALLOWLIST_STATUS:
        raise ValueError("final freeze2 manifest must have status FINAL_FROZEN")
    seen.add(manifest_relative)
    records.append({
        **_record(manifest_relative, role="final_freeze2_manifest"),
        "status": FINAL_ALLOWLIST_STATUS,
        "selected_explicitly": True,
    })
    for value in paths or ():
        path = value if value.is_absolute() else ROOT / value
        relative = root_relative(path)
        path = ROOT / relative
        if relative in seen:
            raise ValueError(f"duplicate final evidence path: {relative}")
        if relative in ALLOWLIST_ARTIFACT_PATHS:
            raise ValueError(f"allowlist self artifact cannot be final evidence: {relative}")
        seen.add(relative)
        if not path.is_file():
            raise ValueError(f"final evidence is missing: {relative}")
        if path.suffix.lower() not in FINAL_EVIDENCE_SUFFIXES:
            raise ValueError(f"final evidence extension is not publishable: {relative}")
        parts = {part.lower() for part in path.relative_to(ROOT).parts}
        if parts & FINAL_EVIDENCE_FORBIDDEN_PARTS:
            raise ValueError(f"final evidence is in an excluded tree: {relative}")
        if any("candidate" in part for part in parts):
            raise ValueError(f"candidate output cannot be final evidence: {relative}")
        size = path.stat().st_size
        if size > FINAL_EVIDENCE_MAX_BYTES:
            raise ValueError(
                f"final evidence is larger than {FINAL_EVIDENCE_MAX_BYTES} bytes: {relative}"
            )
        role = "final_freeze2_evidence"
        if path.suffix.lower() == ".urdf":
            role = "final_urdf"
        records.append({
            **_record(relative, role=role),
            "status": "FINAL_FROZEN",
            "selected_explicitly": True,
        })
    return records


def root_relative(path: Path) -> str:
    """Return a repository-relative path or fail closed for external input."""
    candidate = path if path.is_absolute() else ROOT / path
    _assert_no_symlink_components(candidate, label="repository path")
    try:
        relative = candidate.relative_to(ROOT).as_posix()
    except ValueError as exc:
        raise ValueError(f"final freeze2 manifest must be inside repository: {path}") from exc
    resolved = candidate.resolve(strict=False)
    try:
        resolved_relative = resolved.relative_to(ROOT).as_posix()
    except ValueError as exc:
        raise ValueError(f"repository path resolves outside repository: {path}") from exc
    if resolved_relative != relative:
        raise ValueError(f"repository path resolves to a non-canonical path: {path}")
    _assert_no_symlink_components(resolved, label="repository path")
    return relative


def _cache_exclusion_inventory() -> dict:
    """Count every cache file without copying its generated filenames into the public record."""
    paths: set[str] = set()
    audit_root = ROOT / CACHE_ROOT
    if audit_root.is_dir():
        paths.update(
            item.relative_to(ROOT).as_posix()
            for item in audit_root.rglob("*")
            if item.is_file()
        )
    outputs_root = ROOT / "outputs"
    if outputs_root.is_dir():
        paths.update(
            item.relative_to(ROOT).as_posix()
            for item in outputs_root.rglob("*")
            if item.is_file() and "collision-cache" in item.relative_to(ROOT).parts
        )
    paths = sorted(paths)
    if not paths:
        raise ValueError(f"collision cache trees are missing or empty: {CACHE_ROOT}, {CACHE_GLOB}")
    suffix_counts = Counter(Path(path).suffix.lower() or "<no_suffix>" for path in paths)
    return {
        "roots": [CACHE_ROOT, "outputs/**/collision-cache/**"],
        "filesystem_file_count": len(paths),
        "filesystem_bytes": sum((ROOT / path).stat().st_size for path in paths),
        "suffix_counts": dict(sorted(suffix_counts.items())),
        "all_files_explicitly_excluded": True,
        "file_names_embedded_in_public_record": False,
        "decision": "exclude_every_cache_file_from_publication_and_staging",
        "reason": "衝突計算キャッシュ配下の生成物は公開根拠へ採用せず、個別stageも禁止する",
    }


def _validate_public_purchase_documents() -> dict:
    """公開購入台帳の機械ゲートをallowlist生成にも接続する。"""
    sys.path.insert(0, str(ROOT / "tools/issues"))
    from public_purchase_ledger import assert_public_safe  # type: ignore

    payload = json.loads((ROOT / "docs/additional-purchases.json").read_text(encoding="utf-8"))
    markdown = (ROOT / "docs/additional-purchases.md").read_text(encoding="utf-8")
    assert_public_safe(payload, markdown)
    return {
        "status": "PASS",
        "path": "docs/additional-purchases.json",
        "markdown_path": "docs/additional-purchases.md",
        "item_count": len(payload["items"]),
        "category_counts": payload["current_plan"]["coverage"]["category_counts"],
        "immediate_purchase_required": payload["current_plan"]["immediate_purchase_required"],
        "private_source_excluded": True,
    }


def build(
    final_freeze_manifest: Path | None = None,
    final_evidence_paths: Iterable[Path] | None = None,
    publication_selected_paths: Iterable[str] | None = None,
    evidence_commit_sha: str | None = None,
    draft_pr_url: str | None = None,
) -> dict:
    if final_evidence_paths and final_freeze_manifest is None:
        raise ValueError(
            "explicit final evidence requires a FINAL_FROZEN freeze manifest"
        )
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    xiao_plan = json.loads(XIAO_PLAN.read_text(encoding="utf-8"))
    stl_paths, stl_records = _candidate_stls(manifest)
    source_records, source_groups = _source_records()
    document_records = [_record(path, role="candidate_document") for path in CANDIDATE_DOCUMENTS]
    purchase_ledger_validation = _validate_public_purchase_documents()
    evidence_records = _candidate_evidence_records()
    final_evidence_records = (
        _final_evidence_records(final_evidence_paths, final_freeze_manifest)
        if final_freeze_manifest is not None
        else []
    )
    holder_paths = _holder_paths(xiao_plan)
    holder_records = [_record(path, role="holder_candidate_stl") for path in holder_paths]
    if not all(row["exists"] for row in holder_records):
        raise ValueError(
            f"XIAO holder candidate is missing: {[row['path'] for row in holder_records if not row['exists']]}"
        )
    if not all(row["exists"] for row in document_records):
        raise ValueError(f"candidate document is missing: {[row['path'] for row in document_records if not row['exists']]}")
    text_scan = _scan_text(
        [row["path"] for row in document_records]
        + [row["path"] for row in source_records]
        + [row["path"] for row in evidence_records]
        + [row["path"] for row in final_evidence_records]
    )
    current_status = (
        FINAL_ALLOWLIST_STATUS
        if final_freeze_manifest is not None
        else CANDIDATE_ALLOWLIST_STATUS
    )
    publication_workflow = _publication_workflow_contract(
        current_status,
        evidence_commit_sha=evidence_commit_sha,
        draft_pr_url=draft_pr_url,
    )
    # Build a path/SHA view before writing either allowlist artifact.  The
    # stage gate is read-only and deliberately compares against this
    # provisional record, so regenerating the allowlist cannot make an
    # unrelated staged file pass by changing its own ledger.
    provisional = {
        "schema_version": 2,
        "status": current_status,
        "candidate_documents": document_records,
        "candidate_evidence_files": evidence_records,
        "final_freeze2_selected_files": final_evidence_records,
        "candidate_source_files": source_records,
        "explicit_print_first_stl_candidates": stl_paths,
        "print_first_stl_candidate_records": stl_records,
        "holder_candidate_stl_paths": holder_paths,
        "holder_candidate_stl_records": holder_records,
    }
    base_selected = _base_declared_public_paths(provisional)
    if final_freeze_manifest is not None:
        if publication_selected_paths is None:
            selected = sorted(base_selected)
        else:
            selected = []
            seen_selected: set[str] = set()
            for value in publication_selected_paths:
                path = public_relative_path(str(value), label="publication selected path")
                if path in seen_selected:
                    raise ValueError(f"publication selected path is duplicated: {path}")
                if path not in base_selected:
                    raise ValueError(
                        f"publication selected path has no reviewed record: {path}"
                    )
                seen_selected.add(path)
                selected.append(path)
            selected.sort()
        provisional[PUBLICATION_SELECTED_KEY] = selected
        _publication_selected_paths(provisional, require_final=True)
    else:
        # Candidate stages intentionally have no fixed publication selection;
        # the final freeze must create one before exact staging is permitted.
        provisional[PUBLICATION_SELECTED_KEY] = []
    stage_audit = audit_staged_paths(
        provisional,
        require_final=final_freeze_manifest is not None,
        exact_selected=False,
        validate_structure=False,
    )
    worktree_coverage = audit_worktree_coverage(provisional)
    staged_paths = stage_audit["staged_paths"]
    staged_numstat = git("diff", "--cached", "--numstat").stdout.strip().splitlines()
    staged_bytes = 0
    for line in staged_numstat:
        fields = line.split("\t")
        if len(fields) >= 2 and fields[0].isdigit():
            staged_bytes += int(fields[0]) + (int(fields[1]) if fields[1].isdigit() else 0)
    staged_cache_paths = [
        path for path in staged_paths if "collision-cache" in Path(path).parts
    ]
    cache_inventory = _cache_exclusion_inventory()
    result = {
        "schema_version": 2,
        "checked_at": CANDIDATE_CHECKED_AT,
        "status": current_status,
        "staging": {
            "staged_file_count": len(staged_paths),
            "staged_numstat_units": staged_bytes,
            "staged_collision_cache_file_count": len(staged_cache_paths),
            "external_commit_or_push_performed": False,
            "read_only_stage_gate": stage_audit,
        },
        "candidate_documents": document_records,
        "candidate_evidence_files": evidence_records,
        "final_freeze2_selected_files": final_evidence_records,
        "candidate_source_files": source_records,
        "candidate_source_groups": source_groups,
        "explicit_print_first_stl_candidates": stl_paths,
        "print_first_stl_candidate_records": stl_records,
        "new_head_and_pod_stl_candidates": [
            path for path in stl_paths
            if path.endswith("pf_head_top_clearanced.stl") or path.endswith("pf_eye_pod_camera_clearanced.stl")
        ],
        "holder_candidate_stl_paths": holder_paths,
        "holder_candidate_stl_records": holder_records,
        PUBLICATION_SELECTED_KEY: provisional[PUBLICATION_SELECTED_KEY],
        "publication_workflow": publication_workflow,
        "publication_selection_status": (
            "FINAL_EXACT_REQUIRED"
            if final_freeze_manifest is not None
            else "CANDIDATE_SUBSET_ALLOWED"
        ),
        "worktree_coverage": worktree_coverage,
        "tracked_dirty_cache_exclusions": _cached_dirty_summary(),
        "collision_cache_exclusion_audit": {
            "root": CACHE_ROOT,
            **cache_inventory,
            "staged_file_count": len(staged_cache_paths),
        },
        "content_scan": text_scan,
        "public_purchase_ledger_validation": purchase_ledger_validation,
        "public_safety": {
            "raw_issue_bodies_included": False,
            "token_or_order_details_included": bool(text_scan["findings_by_path"]),
            "absolute_workspace_paths_included": any(
                "absolute_workspace_path" in values for values in text_scan["findings_by_path"].values()
            ),
            "temporary_reproduction_or_collision_cache_included": False,
        },
        "generated_output_inventory": {
            "outputs": {
                **_inventory("outputs"),
                "policy": "exclude recursive output tree; select final STL/JSON/PNG/logs individually after freeze2",
            },
            "hardware_urdf_print_first": {
                **_inventory("hardware/urdf-print-first"),
                "policy": "重複mesh・実行形式・ビルド生成物を除外し、必要な最終URDF/固有meshだけを確認する",
            },
            "public_reproduction": {
                **_inventory("docs/audits/20260905-round2/public-reproduction"),
                "policy": "exclude temporary reproduction directory",
            },
            "collision_cache": {
                "files": cache_inventory["filesystem_file_count"],
                "bytes": cache_inventory["filesystem_bytes"],
                "policy": "全階層のcollision-cache配下を全件除外し、追跡済みの差分だけ別記録する",
            },
        },
        "excluded_patterns": [
            "outputs/** except explicitly selected final files listed after freeze2",
            "outputs/**/collision-cache/**",
            "docs/audits/**/simulation/collision-cache/** (every tracked/untracked file; staging forbidden)",
            "数値キャッシュ・ネイティブ実行形式・共有ライブラリ・中間バイトコードの全拡張子",
            "docs/audits/20260905-round2/public-reproduction/**",
            "outputs/private/** (full source backup; never publish or stage)",
            "hardware/urdf-print-first/meshes/** until unique final assets are reviewed",
            "raw manufacturer STEP/STL, candidate freeze ledgers, and caches",
            "絶対パス、ファイルURI、外部識別子、住所等の個人情報",
        ],
        "review_actions": [
            "機構・simulation freeze2後に同一SHA集合で本ツールとmanifest/orientation/xiao planを再実行し、FINAL_FROZENの台帳を--final-freeze-manifestで明示する。",
            "rootレビュー後にpublication_selected_pathsだけを個別git addし、staged集合との完全一致・容量・秘密・絶対pathを再監査する。",
            "collision-cache配下の全ファイルは内容を消さず、freeze2の採用根拠が確立するまでstageしない。stage監査は0件を要求する。",
            "正常push/draft PR/全107 Issue/Project更新はroot最終レビュー後にだけ実行する。",
        ],
    }
    _validate_allowlist_json_structure(
        result,
        require_final=final_freeze_manifest is not None,
    )
    _validate_current_allowlist_records(
        result,
        require_final=final_freeze_manifest is not None,
    )
    return result


def render_markdown(data: dict) -> str:
    groups = data["candidate_source_groups"]
    scan = data["content_scan"]
    inv = data["generated_output_inventory"]
    workflow = data["publication_workflow"]
    lines = [
        "# 印刷優先公開候補の選別記録（再生成）",
        "",
        f"**状態: `{data['status']}`。stage済み{data['staging']['staged_file_count']}件、commit/push/Issue/Project外部更新は行っていない。**",
        "",
        "## 候補の範囲",
        "",
        f"- `docs/print-first-manifest.json`の採用/保留行から、印刷STL候補 **{len(data['explicit_print_first_stl_candidates'])}件**を再構築した。新しい頭部・中央カメラ逃がしは **{len(data['new_head_and_pod_stl_candidates'])}件**（`pf_head_top_clearanced.stl` / `pf_eye_pod_camera_clearanced.stl`）を含む。",
        "- これはfreeze2前の候補であり、STLの存在・SHA・設計個数を記録する。`currently_printable_quantity=0`は設計個数を隠す意味ではなく、機構・在庫ゲート未通過を示す。",
        "- 現行候補には旧freeze台帳を収録しない。`--final-freeze-manifest`へ`status=FINAL_FROZEN`の実ファイルを明示した場合だけ、最終台帳を個別に追加する。",
        f"- 明示された最終根拠は **{len(data['final_freeze2_selected_files'])}件**。最終URDF・小さいJSON/PNG/ログは`--final-evidence`で個別指定したものだけを追加し、候補結果・大容量raw出力は自動採用しない。",
        f"- `publication_selected_paths` は **{len(data.get(PUBLICATION_SELECTED_KEY) or [])}件**。`FINAL_FROZEN`ではこの集合（allowlist自身のJSON/MDを含む）とstaged集合を完全一致させる。候補段階は空集合で、部分stageだけを許可する。",
        f"- XIAO保持台の比較用STLは **{len(data['holder_candidate_stl_paths'])}件**。without-SD/with-SDは比較候補で、holder unionと3部品を同時に数えない。",
        f"- freeze2・t0・全シムの小さい根拠JSONは **{len(data['candidate_evidence_files'])}件**を個別記録した。大きい時系列/raw結果は候補へ含めず、各JSONの存在・サイズ・SHAだけを固定する。",
        "- 本番数は現行assemblyからbody16 + legs20 + TPU靴4 + PLAスペーサー16 = 56、初回31、残25。既存脛殻4の局所加工と新規殻4は排他的で、最大60。LD適合治具3種各1は本番56へ含めない。",
        "",
        "## 担当ソースの個別列挙",
        "",
        f"担当ソースは **{len(data['candidate_source_files'])}件**。各行に役割・存在・サイズ・SHAをJSONへ保存した。",
    ]
    for group, paths in groups.items():
        lines.append(f"- `{group}`: {len(paths)}件")
    lines += [
        "",
        "## 個別に収録する小さい根拠JSON",
        "",
    ]
    for item in data["candidate_evidence_files"]:
        lines.append(
            f"- `{item['path']}` ({item['role']}): {item['size_bytes']} bytes / SHA `{item['sha256']}`。freeze2後に内容を再確認する。"
        )
    lines += [
        "",
        "## 最終freeze2根拠（明示指定時のみ）",
        "",
    ]
    if data["final_freeze2_selected_files"]:
        for item in data["final_freeze2_selected_files"]:
            lines.append(
                f"- `{item['path']}` ({item['role']}): {item['size_bytes']} bytes / SHA `{item['sha256']}`。最終入力集合の実ファイル。"
            )
    else:
        lines.append("- 0件。機構・simulation freeze2後に最終URDFと小さい根拠を個別指定する。")
    lines += [
        "",
        "対象には印刷優先の全生成器、XIAO/LD機構、t0契約・自己干渉・初期診断・材料/強度検査、URDF検査、AGENTS.md指定の設計監査実行器と歩容検査、必要な回帰試験、変更済みfirmwareとfirmware試験を含めた。`tools/export_urdf.py`はlegacy参照と質量式の根拠として収録するが、最終print-first生成を単独実行しない。",
        "",
        "## 追跡済みcacheの扱い",
        "",
        f"- collision-cacheの全階層（`outputs/**/collision-cache/**` と監査用cache）: filesystem **{data['collision_cache_exclusion_audit']['filesystem_file_count']} files / {data['collision_cache_exclusion_audit']['filesystem_bytes']} bytes**を全件明示除外。ファイル名は公開記録へ展開せず、staged collision-cacheは **{data['staging']['staged_collision_cache_file_count']}件**でなければ生成器が失敗する。",
    ]
    dirty = data["tracked_dirty_cache_exclusions"]
    lines.append(
        f"- 追跡済みcache差分: {dirty['tracked_modified_file_count']} files、差分 +{dirty['modified_numstat']['added']}/-{dirty['modified_numstat']['deleted']}。既存差分は保持するが、cache配下の個別ファイル名・SHAは公開候補へ展開しない。"
    )
    lines += [
        "",
        "## 大きな生成物",
        "",
        f"- `outputs/`: {inv['outputs']['files']} files / {inv['outputs']['bytes']} bytes。再帰追加せず、freeze2後に最終STL・小さいJSON/PNG・要約ログを個別選択する。",
        f"- `hardware/urdf-print-first/`: {inv['hardware_urdf_print_first']['files']} files / {inv['hardware_urdf_print_first']['bytes']} bytes。重複mesh・実行形式・ビルド生成物を除外し、必要な最終URDF/固有meshだけを確認する。",
        f"- 公開用一時再現束: {inv['public_reproduction']['files']} files / {inv['public_reproduction']['bytes']} bytes。除外する。",
        f"- 衝突計算キャッシュ（全階層）: {inv['collision_cache']['files']} files / {inv['collision_cache']['bytes']} bytes。配下全件を数値キャッシュとして除外する。",
        "",
        "## 値の漏えい検査",
        "",
        f"候補文書・候補ソースの実値パターン検出: **{scan['status']}**。絶対パス・ファイルURI・認証情報・外部記録識別子・郵便番号の実値を記録していない。",
        "",
        "## rootレビュー後の順序",
        "",
        f"1. `{workflow['stage_order'][0]}`: 機構とsimulationのfreeze2結果を受領し、本ツール・印刷manifest・orientation・XIAO planを同一SHA集合で再生成する。",
        f"2. `{workflow['stage_order'][1]}`: 証拠commit Aからdraft PRを作成し、AのSHAを最終計画の入力へ固定する。",
        f"3. `{workflow['stage_order'][2]}`: `FINAL_FROZEN`のpublication_selected_pathsを個別stageし、staged集合との完全一致・容量・秘密・絶対path・一時生成物を再監査して計画/allowlist commit Bを作成する。BのSHAは自分の内容へ埋め込まない。",
        f"4. `{workflow['stage_order'][3]}`: Bのreadback後に、全107 IssueとProject #2を更新し、Issue本文へAの根拠とBのreadback情報を付す。",
        "5. 候補段階は部分stageの確認に留め、最終段階だけ `--exact-selected` で選択集合とstaged集合の完全一致を要求する。",
        "",
        "詳細JSON: `print-first-publication-allowlist-20260906.json`。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--md", type=Path, default=DEFAULT_MD)
    parser.add_argument(
        "--audit-staged",
        action="store_true",
        help="既存allowlistを入力に、index/worktreeを変更せずstaged path/SHA/公開範囲を監査する",
    )
    parser.add_argument(
        "--exact-selected",
        action="store_true",
        help="最終監査としてstaged集合をpublication_selected_pathsと完全一致させる",
    )
    parser.add_argument(
        "--final-freeze-manifest",
        type=Path,
        default=None,
        help="最終freeze2台帳（JSON status=FINAL_FROZEN）のみを明示した場合に公開候補へ追加する",
    )
    parser.add_argument(
        "--final-evidence",
        type=Path,
        action="append",
        default=[],
        help="最終freeze2後に個別選択した小さい根拠JSON/PNG/ログ/URDF（再帰探索しない）",
    )
    parser.add_argument(
        "--publication-selected-path",
        action="append",
        default=None,
        help="最終公開へ明示選択するリポジトリ相対path（複数可）",
    )
    parser.add_argument(
        "--evidence-commit-sha",
        default=None,
        help="FINAL_FROZEN時の証拠commit Aのlowercase SHA（B自身のSHAは入力しない）",
    )
    parser.add_argument(
        "--draft-pr-url",
        default=None,
        help="FINAL_FROZEN時のcommit A由来draft PR URL",
    )
    args = parser.parse_args()
    output_json = args.json if args.json.is_absolute() else ROOT / args.json
    output_md = args.md if args.md.is_absolute() else ROOT / args.md
    if args.audit_staged:
        data = json.loads(output_json.read_text(encoding="utf-8"))
        require_final = bool(args.exact_selected)
        stage_audit = audit_staged_paths(
            data,
            require_final=require_final,
            exact_selected=args.exact_selected,
        )
        coverage = audit_worktree_coverage(data)
        result = {
            "status": "PASS",
            "stage": stage_audit,
            "worktree_coverage": coverage,
            "writes_performed": False,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    final_freeze_manifest = None
    if args.final_freeze_manifest is not None:
        final_freeze_manifest = (
            args.final_freeze_manifest
            if args.final_freeze_manifest.is_absolute()
            else ROOT / args.final_freeze_manifest
        )
    final_evidence_paths = [
        value if value.is_absolute() else ROOT / value
        for value in args.final_evidence
    ]
    data = build(
        final_freeze_manifest,
        final_evidence_paths,
        args.publication_selected_path,
        evidence_commit_sha=args.evidence_commit_sha,
        draft_pr_url=args.draft_pr_url,
    )
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_md.write_text(render_markdown(data), encoding="utf-8")
    print(json.dumps({
        "status": data["status"],
        "documents": len(data["candidate_documents"]),
        "sources": len(data["candidate_source_files"]),
        "print_first_stls": len(data["explicit_print_first_stl_candidates"]),
        "final_freeze2_selected_files": len(data["final_freeze2_selected_files"]),
        "new_head_and_pod_stls": data["new_head_and_pod_stl_candidates"],
        "content_scan": data["content_scan"]["status"],
        "staged_file_count": data["staging"]["staged_file_count"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
