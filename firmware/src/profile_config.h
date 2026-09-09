#pragma once
// LED/DFPlayer の搭載構成を切り替える。PRINT_FIRST_PROFILE=1 のときだけ、
// 生成済みの候補歩容と固定体高の操作範囲も選ぶ。これは採用済み設定や
// 実機の安全・歩行実証を表さず、未指定時は従来の通常構成を維持する。
#ifndef TACHIKOMA_STATUS_LEDS
#define TACHIKOMA_STATUS_LEDS 1
#endif
#ifndef TACHIKOMA_DFPLAYER
#define TACHIKOMA_DFPLAYER 1
#endif
#ifndef TACHIKOMA_AUDIO_VOLUME_PERCENT
#define TACHIKOMA_AUDIO_VOLUME_PERCENT 100
#endif
#ifndef TACHIKOMA_PRINT_FIRST_PROFILE
#define TACHIKOMA_PRINT_FIRST_PROFILE 0
#endif
#if (TACHIKOMA_STATUS_LEDS != 0 && TACHIKOMA_STATUS_LEDS != 1) || \
    (TACHIKOMA_DFPLAYER != 0 && TACHIKOMA_DFPLAYER != 1) || \
    (TACHIKOMA_PRINT_FIRST_PROFILE != 0 && TACHIKOMA_PRINT_FIRST_PROFILE != 1)
#error "Peripheral feature flags must be 0 or 1; walking profile flag must be 0 or 1"
#endif
#if TACHIKOMA_AUDIO_VOLUME_PERCENT < 0 || TACHIKOMA_AUDIO_VOLUME_PERCENT > 100
#error "Audio volume percent must be within 0..100"
#endif

constexpr bool STATUS_LEDS_ENABLED = TACHIKOMA_STATUS_LEDS != 0;
constexpr bool DFPLAYER_ENABLED = TACHIKOMA_DFPLAYER != 0;
constexpr bool PRINT_FIRST_PROFILE_ENABLED = TACHIKOMA_PRINT_FIRST_PROFILE != 0;
constexpr const char* HARDWARE_PROFILE =
    PRINT_FIRST_PROFILE_ENABLED ? "print-first" :
    STATUS_LEDS_ENABLED && DFPLAYER_ENABLED ? "standard" :
    !STATUS_LEDS_ENABLED && !DFPLAYER_ENABLED ? "reduced-peripherals" : "custom";
