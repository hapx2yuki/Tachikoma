"""印刷優先の最終検査で共有する入力ソース閉包。

凍結台帳、実行時検査、生成器、公開候補台帳がそれぞれ似た手書き一覧を
持つと、検査対象から生成器やnative trace検査器が抜けたままでも気付けない。
このモジュールは、最終印刷優先構成を再生成・検査するための明示的な閉包を
一箇所に置く。生成物や衝突キャッシュは含めず、同じ実体SHAを各利用側で
照合する。
"""
from __future__ import annotations


# Keep this tuple ordered for reproducible manifests and human review.  New
# source inputs must be added here before they are consumed by a final run.
PRINT_FIRST_SOURCE_CLOSURE = (
    "hardware/src/config.py",
    "firmware/platformio.ini",
    "firmware/src/arms.h",
    "firmware/src/audio.h",
    "firmware/src/config.h",
    "firmware/src/control.h",
    "firmware/src/gait.h",
    "firmware/src/main.cpp",
    "firmware/src/peripherals.h",
    "firmware/src/print_first_gait.h",
    "firmware/src/profile_config.h",
    "firmware/src/servos.h",
    "firmware/src/web_ui.h",
    "tools/print_first_assembly.py",
    "tools/sim_collision.py",
    "tools/sim_physics.py",
    "tools/sim_print_first.py",
    "tools/sim_stress.py",
    "tools/export_urdf.py",
    "tools/print_first_components.py",
    "tools/generate_print_first_profile.py",
    "tools/export_print_first_native_trace.py",
    "tools/generate_print_first.py",
    "tools/check_print_first_native_trace.py",
    "tools/diagnose_print_first_initial.py",
    "tools/check_print_first_body.py",
    "tools/check_print_first_t0_mesh.py",
    "tools/sim_self_collision.py",
    "tools/check_print_first_proxy_exclusions.py",
    "tools/check_print_first_feet.py",
    "tools/xiao_retention_plan.py",
    "tools/check_print_first_motion_quality.py",
    "tools/make_print_first_freeze_manifest.py",
    "tools/tests/simulation_output_trace.cpp",
    "tools/tests/simulation_firmware_trace.cpp",
    "tools/tests/firmware_stubs/ESPAsyncWebServer.h",
    "tools/tests/firmware_stubs/WiFi.h",
    "tools/tests/firmware_stubs/driver/i2s.h",
)

PRINT_FIRST_SOURCE_CLOSURE_SET = frozenset(PRINT_FIRST_SOURCE_CLOSURE)

# These generated/runtime files are part of the final freeze ledger but are
# intentionally outside the immutable source list used by the serial
# generator and publication source groups.
PRINT_FIRST_FREEZE_ADDITIONAL_INPUTS = (
    "firmware/src/eyes.h",
    "firmware/src/ik.h",
    "firmware/src/leg_output.h",
    "docs/audits/20260905-round2/xiao-retention-plan.json",
    "outputs/print-first-20260905/final-simulation/cases/motion-quality-cases.json",
    "tools/print_first_source_closure.py",
    "tools/tests/firmware_stubs/Adafruit_NeoPixel.h",
    "tools/tests/firmware_stubs/Adafruit_PWMServoDriver.h",
    "tools/tests/firmware_stubs/Arduino.h",
    "tools/tests/firmware_stubs/DFRobotDFPlayerMini.h",
    "tools/tests/firmware_stubs/ESPmDNS.h",
    "tools/tests/firmware_stubs/HardwareSerial.h",
    "tools/tests/firmware_stubs/Preferences.h",
    "tools/tests/firmware_stubs/Wire.h",
    "tools/tests/firmware_stubs/esp_task_wdt.h",
)

# The serial geometry generator runs before same-run URDF/header artifacts and
# motion-quality case data exist.  It uses this source-only view while the
# final freeze uses the complete closure above.  Keeping the derivation here
# prevents the generator and final validators from silently dropping a newly
# added source tool.
_GENERATOR_OUTPUTS = frozenset({
    "firmware/src/print_first_gait.h",
    "firmware/src/profile_config.h",
    "docs/audits/20260905-round2/xiao-retention-plan.json",
})
PRINT_FIRST_GENERATOR_SOURCE_CLOSURE = tuple(dict.fromkeys((
    *(
        path for path in PRINT_FIRST_SOURCE_CLOSURE
        if path not in _GENERATOR_OUTPUTS and not path.startswith("outputs/")
    ),
    "tools/print_first_source_closure.py",
)))


if len(PRINT_FIRST_SOURCE_CLOSURE_SET) != len(PRINT_FIRST_SOURCE_CLOSURE):
    raise RuntimeError("PRINT_FIRST_SOURCE_CLOSURE contains duplicate paths")
