#!/usr/bin/env python3
"""Check package hashes, the P36 prototype archive, and PWM-to-terminal mapping."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest() -> int:
    manifest_path = ROOT / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checked = 0
    for entry in manifest["artifacts"]:
        path = REPO / entry["package_path"]
        if not path.is_file():
            fail(f"manifest file missing: {entry['package_path']}")
        actual = sha256(path)
        if actual != entry["package_sha256"]:
            fail(f"package hash mismatch: {entry['package_path']}")
        checked += 1

    sums_path = ROOT / "SHA256SUMS.txt"
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        path = REPO / relative
        if not path.is_file() or sha256(path) != expected:
            fail(f"SHA256SUMS mismatch: {relative}")
    return checked


def verify_zip() -> int:
    package = ROOT / "p36/P36_SILK_R1_JLCPCB_PROTOTYPE.zip"
    gerber_dir = ROOT / "p36/gerber"
    with zipfile.ZipFile(package) as archive:
        bad = archive.testzip()
        if bad is not None:
            fail(f"ZIP CRC failure: {bad}")
        members = {Path(name).name for name in archive.namelist() if not name.endswith("/")}
        source_files = {path.name for path in gerber_dir.iterdir() if path.is_file()}
        if members != source_files:
            fail(f"ZIP/Gerber member mismatch: ZIP={sorted(members)}, files={sorted(source_files)}")
        for member in archive.namelist():
            name = Path(member).name
            if member.endswith("/"):
                continue
            if archive.read(member) != (gerber_dir / name).read_bytes():
                fail(f"ZIP payload differs from Gerber copy: {name}")
        return len(members)


def verify_pwm() -> tuple[int, int]:
    netlist_path = ROOT / "p36/fab-prep/NETLIST.csv"
    with netlist_path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    nets = {row["terminal"]: row["net"] for row in rows}

    for channel in range(12):
        expected = f"PWM_L{channel}"
        terminals = (f"J{channel}.S", f"J_IN.PWM{channel}")
        if any(nets.get(terminal) != expected for terminal in terminals):
            fail(f"PWM net mismatch for channel {channel}: {terminals}")
        if nets.get(f"J{channel}.V") != "LEG_VBUS":
            fail(f"servo V terminal mismatch for channel {channel}")
        if nets.get(f"J{channel}.G") != "GND":
            fail(f"servo G terminal mismatch for channel {channel}")

    connectivity = json.loads(
        (ROOT / "p36/pwm-review/PWM_PHYSICAL_CONNECTIVITY.json").read_text(encoding="utf-8")
    )
    channels = connectivity["channels"]
    if connectivity["PWM_independent_components"] != 12 or len(channels) != 12:
        fail("physical PWM connectivity record does not contain 12 independent channels")
    if connectivity["PWM_to_GND_connection"] is not False:
        fail("PWM-to-GND isolation record is not false")
    for channel in channels:
        ch = channel["ch"]
        if set(channel["connected_terminals"]) != {f"J{ch}.S", f"J_IN.PWM{ch}"}:
            fail(f"physical connectivity terminal mismatch for channel {ch}")
    return len(rows), len(channels)


def main() -> int:
    manifest_count = verify_manifest()
    zip_count = verify_zip()
    netlist_rows, channel_count = verify_pwm()
    print(f"OK package SHA-256 records: {manifest_count}")
    print(f"OK prototype ZIP: {zip_count} files, CRC and payloads match packaged Gerbers")
    print(f"OK PWM/terminal mapping: {netlist_rows} rows, {channel_count} independent channels")
    print("Scope: copied-file consistency only; no electrical rating or physical validation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
