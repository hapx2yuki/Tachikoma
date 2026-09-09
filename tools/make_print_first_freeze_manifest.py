#!/usr/bin/env python3
"""最終印刷優先モデルの凍結台帳を生成する。

最終URDF/メッシュと生成元入力を一度だけ列挙し、台帳自身は行へ含めない。
したがって、出力後に台帳自身のSHAをケースへ設定しても循環参照にならない。
実行順は geometry/URDF/STL 生成、print-first header生成、このスクリプト、
最後に ``sim_print_first.py`` である。
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from print_first_source_closure import (  # noqa: E402
    PRINT_FIRST_FREEZE_ADDITIONAL_INPUTS,
    PRINT_FIRST_SOURCE_CLOSURE,
)
from sim_print_first import runtime_input_paths  # noqa: E402

FREEZE_REQUIRED_SOURCES = tuple(dict.fromkeys(
    (*PRINT_FIRST_SOURCE_CLOSURE, *PRINT_FIRST_FREEZE_ADDITIONAL_INPUTS)
))

DEFAULT_URDF = ROOT / "hardware/urdf-print-first/tachikoma.urdf"
DEFAULT_OUTPUT = ROOT / "outputs/print-first-20260905/final-simulation/freeze-manifest.json"
DEFAULT_ASSEMBLIES = (
    ROOT / "outputs/print-first-20260905/body/assembly.json",
    ROOT / "outputs/print-first-20260905/legs/assembly.json",
    ROOT / "outputs/print-first-20260905/feet/assembly.json",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_output_path(output: Path, *, input_paths=()) -> Path:
    """書き込み先を先に検証し、入力やsymlink経由の置換を拒否する。"""
    output = Path(output)
    if not output.is_absolute():
        output = ROOT / output
    output = output.absolute()
    try:
        relative = output.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"凍結台帳の出力先はROOT内に置く: {output}") from exc
    if output.suffix.lower() != ".json":
        raise ValueError(f"凍結台帳の出力先はJSONである必要がある: {output}")
    input_set = set()
    for path in input_paths:
        try:
            input_set.add(Path(path).resolve(strict=False))
        except OSError:
            input_set.add(Path(path).absolute())
    if output.resolve(strict=False) in input_set:
        raise ValueError(f"出力先が凍結入力と同じ: {output}")

    current = ROOT.resolve()
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"凍結台帳の出力親にsymlinkを使えない: {current}")
    if output.is_symlink():
        raise ValueError(f"凍結台帳の出力先にsymlinkを使えない: {output}")
    if output.exists() and not output.is_file():
        raise ValueError(f"凍結台帳の出力先が通常ファイルではない: {output}")
    return output


def _stable_snapshot(path: Path, *, attempts: int = 3) -> dict:
    """SHA計算中の入力変更を検出した安定スナップショットを返す。"""
    path = Path(path).resolve()
    last = None
    for _ in range(attempts):
        before = path.stat()
        digest = sha256(path)
        after = path.stat()
        last = (before, after)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            return {
                "path": path,
                "sha256": digest,
                "size_bytes": after.st_size,
                "mtime_ns": after.st_mtime_ns,
            }
    before, after = last
    raise RuntimeError(
        f"凍結対象入力がSHA計算中に変更された: {path} "
        f"({before.st_size}/{after.st_size} bytes, "
        f"{before.st_mtime_ns}/{after.st_mtime_ns} mtime_ns)"
    )


def _snapshot_inputs(paths) -> dict[Path, dict]:
    snapshots = {}
    for path in sorted({Path(path).resolve() for path in paths}, key=str):
        if not path.is_file():
            raise FileNotFoundError(f"凍結対象ファイルが無い: {path}")
        snapshots[path] = _stable_snapshot(path)
    return snapshots


def _assert_manifest_inputs_current(manifest: dict) -> None:
    """書き込み直前に、台帳行の入力が同じSHAか再確認する。"""
    rows = manifest.get("files")
    if not isinstance(rows, list):
        raise ValueError("凍結台帳のfiles列が無い")
    expected = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("凍結台帳のfiles行が不正")
        path = resolve_manifest_path(row["path"], base=ROOT)
        expected[path] = row.get("sha256")
    current = _snapshot_inputs(expected)
    changed = [
        root_key(path) for path, snapshot in current.items()
        if snapshot["sha256"] != expected[path]
    ]
    if changed:
        raise RuntimeError(
            "凍結台帳を書き込む前に入力が変更された: " + ", ".join(changed)
        )


@contextmanager
def _output_lock(output: Path):
    """同じ出力先への凍結生成を直列化する一時ロック。"""
    key = hashlib.sha256(str(Path(output).resolve()).encode("utf-8")).hexdigest()
    lock_path = Path(tempfile.gettempdir()) / f"tachikoma-print-first-freeze-{key}.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _atomic_write_json(destination: Path, payload: dict) -> None:
    """fsync済み一時ファイルを同一ディレクトリから原子的に置換する。"""
    destination = Path(destination)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        try:
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # The file replacement is still atomic on filesystems that do not
            # permit directory fsync; retain the durable file itself.
            pass
    finally:
        if temporary.exists():
            temporary.unlink()


def canonical_utc_freeze_time(value: str) -> str:
    """凍結時刻をtimezone付きUTCの秒精度へ正規化する。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("凍結時刻はISO 8601のUTC時刻で指定する")
    text = value.strip()
    parsed_text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(parsed_text)
    except ValueError as exc:
        raise ValueError(f"凍結時刻がISO 8601ではない: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("凍結時刻にはUTC offsetが必要")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z")


def resolve_path(value: str | Path, *, base: Path = ROOT) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


ROOT_RELATIVE_TOPS = {"hardware", "firmware", "outputs", "docs", "tools", "model"}


def resolve_manifest_path(value: str | Path, *, base: Path) -> Path:
    """Resolve repo-root paths while allowing a manifest-local filename."""
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    if path.parts and path.parts[0] in ROOT_RELATIVE_TOPS:
        return (ROOT / path).resolve()
    return (base / path).resolve()


def root_key(path: Path) -> str:
    path = path.resolve()
    try:
        return str(path.relative_to(ROOT.resolve()))
    except ValueError as exc:
        raise ValueError(f"凍結台帳へROOT外の入力は指定できない: {path}") from exc


def referenced_paths(value: Path) -> list[Path]:
    """台帳の path/stl と source_sha256 のキーを再帰的に集める。"""
    value = value.resolve()
    paths = [value]
    if value.suffix.lower() != ".json":
        return paths
    try:
        data = json.loads(value.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"組立台帳を読めない: {value}: {exc}") from exc

    file_suffixes = {
        ".py", ".h", ".hpp", ".cpp", ".cc", ".ini", ".json",
        ".stl", ".obj", ".3mf", ".urdf",
    }

    def walk(item, *, base: Path):
        if isinstance(item, dict):
            for key, child in item.items():
                if isinstance(child, str) and str(key).lower() in {
                    "path", "stl", "mesh", "filename", "file"
                }:
                    candidate = Path(child)
                    if candidate.suffix.lower() in file_suffixes:
                        paths.append(resolve_manifest_path(candidate, base=base))
                if isinstance(key, str) and Path(key).suffix.lower() in file_suffixes:
                    candidate = Path(key)
                    paths.append(resolve_manifest_path(candidate, base=base))
                walk(child, base=base)
        elif isinstance(item, list):
            for child in item:
                walk(child, base=base)

    walk(data, base=value.parent)
    return paths


def build_manifest(urdf: Path, assemblies: list[Path], freeze_time: str,
                   status: str = "FROZEN") -> dict:
    freeze_time = canonical_utc_freeze_time(freeze_time)
    urdf = urdf.resolve()
    if not urdf.is_file():
        raise FileNotFoundError(f"最終URDFが無い: {urdf}")
    try:
        root = ET.parse(urdf).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"最終URDFを読めない: {urdf}: {exc}") from exc

    files: dict[Path, str] = {}

    def add(path: Path, role: str):
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"凍結対象ファイルが無い ({role}): {path}")
        files.setdefault(path, role)

    add(urdf, "final_urdf")
    referenced_meshes: set[Path] = set()
    for mesh in root.findall(".//mesh"):
        filename = mesh.get("filename")
        if filename:
            mesh_path = resolve_manifest_path(filename, base=urdf.parent)
            referenced_meshes.add(mesh_path)
            # ``add`` is deliberately called for every URDF reference so a
            # missing mesh fails before a manifest can be published.
            add(mesh_path, "final_urdf_mesh")

    # The final bundle is exactly the URDF reference closure. An orphaned file
    # can be stale geometry from a previous generation and must never become a
    # frozen input merely because it happens to remain under meshes/.
    mesh_dir = urdf.parent / "meshes"
    bundle_meshes: set[Path] = set()
    if mesh_dir.is_dir():
        for path in sorted(mesh_dir.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".stl", ".obj", ".3mf"}:
                bundle_meshes.add(path.resolve())
    orphan_meshes = sorted(bundle_meshes - referenced_meshes)
    if orphan_meshes:
        rendered = ", ".join(root_key(path) for path in orphan_meshes)
        raise ValueError(
            f"URDF未参照の孤立メッシュを凍結できない ({len(orphan_meshes)}): {rendered}")
    referenced_outside_bundle = sorted(
        path for path in referenced_meshes
        if not path.is_relative_to(mesh_dir.resolve()))
    if referenced_outside_bundle:
        rendered = ", ".join(root_key(path) for path in referenced_outside_bundle)
        raise ValueError(
            f"URDF参照メッシュがfinal_mesh_bundle外にある ({len(referenced_outside_bundle)}): {rendered}")
    parts_manifest = urdf.parent / "parts_manifest.json"
    if parts_manifest.is_file():
        add(parts_manifest, "final_parts_manifest")

    for path in (ROOT / source for source in FREEZE_REQUIRED_SOURCES):
        add(path, "firmware_or_generator_input")
    runtime_paths, runtime_error = runtime_input_paths(urdf)
    if runtime_error:
        raise ValueError(f"実行時入力集合を取得できない: {runtime_error}")
    for path in runtime_paths:
        add(path, "runtime_input_fingerprint")
    for path in assemblies:
        path = path.resolve()
        for referenced in referenced_paths(path):
            add(referenced, "assembly_input")

    # Hash from a stable snapshot and immediately take a second snapshot.  A
    # generator writing a source/mesh while this ledger is being assembled
    # must fail instead of publishing a mixed SHA set.
    snapshots = _snapshot_inputs(files)
    after = _snapshot_inputs(files)
    changed = [
        root_key(path) for path in files
        if snapshots[path]["sha256"] != after[path]["sha256"]
    ]
    if changed:
        raise RuntimeError(
            "凍結対象入力が台帳生成中に変更された: " + ", ".join(changed)
        )
    rows = [
        {"path": root_key(path), "sha256": snapshots[path]["sha256"], "role": role}
        for path, role in sorted(files.items(), key=lambda item: root_key(item[0]))
    ]
    return {
        "schema_version": 2,
        "status": status,
        "geometry_freeze_time": freeze_time,
        "final_urdf": root_key(urdf),
        "final_urdf_mesh_count": len(referenced_meshes),
        "final_mesh_bundle": {
            "directory": root_key(mesh_dir) if mesh_dir.is_dir() else None,
            "referenced_count": len(referenced_meshes),
            "files_count": len(bundle_meshes),
            "orphan_count": len(orphan_meshes),
            "referenced_outside_bundle_count": len(referenced_outside_bundle),
            "orphan_policy": "error",
        },
        "files": rows,
        "file_count": len(rows),
        "interpretation": "実ファイルSHAの凍結台帳。台帳自身はfilesへ含めない。物理実証や印刷適合を意味しない。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--urdf", type=Path, default=DEFAULT_URDF)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--freeze-time", required=True,
                        help="geometry_freeze_time と同じISO時刻")
    parser.add_argument("--status", default="FROZEN",
                        choices=("FROZEN", "FINAL_FROZEN", "COMPLETE"))
    parser.add_argument("--assembly", type=Path, action="append",
                        help="追加の組立台帳JSON（省略時はbody/legs/feet）")
    args = parser.parse_args()
    # Keep the lexical destination here; resolving it first would hide an
    # existing symlink and allow atomic replace to redirect outside ROOT.
    output = (args.output if args.output.is_absolute() else ROOT / args.output).absolute()
    urdf = resolve_path(args.urdf)
    assemblies = [resolve_path(path) for path in (args.assembly or DEFAULT_ASSEMBLIES)]
    # Validate the destination before creating its parent or assembling any
    # manifest data.  The second validation below also catches an output path
    # that appears indirectly in an assembly/source closure.
    _safe_output_path(output, input_paths=[urdf, *assemblies])
    output.parent.mkdir(parents=True, exist_ok=True)
    with _output_lock(output):
        manifest = build_manifest(urdf, assemblies, args.freeze_time, args.status)
        manifest_inputs = [ROOT / row["path"] for row in manifest["files"]]
        _safe_output_path(output, input_paths=manifest_inputs)
        _assert_manifest_inputs_current(manifest)
        _atomic_write_json(output, manifest)
    print(json.dumps({"manifest": root_key(output), "sha256": sha256(output),
                      "geometry_freeze_time": manifest["geometry_freeze_time"],
                      "file_count": manifest["file_count"],
                      "status": manifest["status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
