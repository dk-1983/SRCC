# Каталог выполненных автоматических тестов

Дополнение 0.4.0: [клиент голосового центра и таблица событий](VOICE_NOTIFICATIONS.md), [публичный контракт v1](VOICE_PROTOCOL_V1.md). Приёмник Home Control ещё не реализован. Прогон 2026-09-29: **149 PASS (73 автономных + 76 HA)**, 16 новых методов в tests/ha/test_voice.py; hassfest: 0 invalid. Ниже сохранены исторические срезы.

Дополнение 0.3.0: [профиль Haier ESP v1.3.0](HAIER_TELEMETRY.md) описывает новые настройки, подтверждение команд и границы свежести. Текущий прогон 0.3.1 от 2026-09-27: **133 PASS (73 автономных + 60 HA)**. Исторические срезы ниже сохранены; совместная проверка нового профиля с рабочим MQTT/ESP ещё не выполнена.

Срез: **SRCC 0.2.1**, код `0cb8100`, проверка 2026-09-22. Этот каталог описывает конкретный прогон, не результат будущих запусков CI. 94 отдельных test-метода прошли; параметризованные подпроверки не посчитаны отдельными тестами. Статус PASS относится к программной проверке, не к оборудованию.

61 автономный тест: Python 3.11.4. 33 интеграционных: Home Assistant 2026.9.2, Python 3.14.5, локальный образ hassfest. Команды и границы проверки — [DEVELOPMENT.md](DEVELOPMENT.md), сопоставление с требованиями и оставшиеся испытания — [VALIDATION_PLAN.md](VALIDATION_PLAN.md).

## tests/test_core.py

Пройдено: **38**.

| Тест | Результат |
|---|---|
| [InputTests.test_primary_wins_over_hot_backup](../tests/test_core.py#L29) | PASS |
| [InputTests.test_both_required_and_average](../tests/test_core.py#L36) | PASS |
| [InputTests.test_mean_keeps_oldest_timestamp_and_stable_source](../tests/test_core.py#L49) | PASS |
| [InputTests.test_invalid_values_and_future_samples](../tests/test_core.py#L58) | PASS |
| [InputTests.test_duplicate_backup_entities_rejected](../tests/test_core.py#L65) | PASS |
| [AlarmTests.test_critical_at_equality_and_warning](../tests/test_core.py#L76) | PASS |
| [AlarmTests.test_two_minutes_of_falling_temperature](../tests/test_core.py#L81) | PASS |
| [AlarmTests.test_flat_or_rising_does_not_clear](../tests/test_core.py#L90) | PASS |
| [AlarmTests.test_missing_data_resets_recovery_not_latch](../tests/test_core.py#L98) | PASS |
| [AlarmTests.test_source_change_restarts_recovery](../tests/test_core.py#L105) | PASS |
| [AlarmTests.test_hot_fallback_critical_without_trend](../tests/test_core.py#L114) | PASS |
| [AlarmTests.test_other_critical_survives_temperature_recovery](../tests/test_core.py#L117) | PASS |
| [AlarmTests.test_old_repeated_reading_cannot_clear](../tests/test_core.py#L125) | PASS |
| [TrendTests.test_first_result_at_five_minutes](../tests/test_core.py#L137) | PASS |
| [TrendTests.test_unset_rise_threshold_is_not_failure](../tests/test_core.py#L148) | PASS |
| [TrendTests.test_off_or_duplicate_resets_window](../tests/test_core.py#L155) | PASS |
| [TrendTests.test_unchanged_new_measurements_are_valid](../tests/test_core.py#L164) | PASS |
| [PolicyTests.test_hysteresis_boundaries](../tests/test_core.py#L173) | PASS |
| [PolicyTests.test_failed_vent_reserve_and_hard_minimum](../tests/test_core.py#L184) | PASS |
| [PolicyTests.test_hot_outdoor_no_emergency_vent](../tests/test_core.py#L191) | PASS |
| [PolicyTests.test_battery_or_unknown_does_not_assume_mains](../tests/test_core.py#L196) | PASS |
| [PolicyTests.test_critical_marks_immediate_evaluation](../tests/test_core.py#L202) | PASS |
| [TransitionTests.test_vent_to_ac_readback_order](../tests/test_core.py#L215) | PASS |
| [TransitionTests.test_ac_off_before_vent_enable](../tests/test_core.py#L226) | PASS |
| [TransitionTests.test_unavailable_ac_is_not_off](../tests/test_core.py#L234) | PASS |
| [TransitionTests.test_manual_stops_pending_and_requires_reconciliation](../tests/test_core.py#L240) | PASS |
| [TransitionTests.test_timeout_no_retry](../tests/test_core.py#L246) | PASS |
| [TransitionTests.test_guard_rechecked_after_vent_disabled](../tests/test_core.py#L253) | PASS |
| [TransitionTests.test_unknown_or_optimistic_vent_is_not_off](../tests/test_core.py#L260) | PASS |
| [TransitionTests.test_all_heat_and_auto_modes_require_off](../tests/test_core.py#L266) | PASS |
| [TransitionTests.test_service_error_latches_block](../tests/test_core.py#L271) | PASS |
| [TransitionTests.test_no_start_before_ready_or_timing](../tests/test_core.py#L277) | PASS |
| [ConfigTests.test_invalid_configuration_rejected](../tests/test_core.py#L283) | PASS |
| [OptionalAcFaultTests.test_policy_and_planner_distinguish_absent_from_lost_fault_signal](../tests/test_core.py#L296) | PASS |
| [OptionalAcFaultTests.test_absent_fault_does_not_bypass_ac_availability_or_minimum](../tests/test_core.py#L309) | PASS |
| [ManualVentilationTests.test_reserve_outdoor_limit_and_power_evidence](../tests/test_core.py#L322) | PASS |
| [ManualVentilationTests.test_manual_output_off_requires_binding_off](../tests/test_core.py#L335) | PASS |
| [SeasonalSequenceTests.test_temperature_oscillations_keep_mode_until_opposite_boundary](../tests/test_core.py#L348) | PASS |

## tests/test_ha_inputs.py

Пройдено: **10**.

| Тест | Результат |
|---|---|
| [NormalizationTests.test_fahrenheit_and_kelvin](../tests/test_ha_inputs.py#L28) | PASS |
| [NormalizationTests.test_restored_and_missing_not_usable](../tests/test_ha_inputs.py#L33) | PASS |
| [NormalizationTests.test_bad_unit_nonfinite_and_bad_bool](../tests/test_ha_inputs.py#L38) | PASS |
| [NormalizationTests.test_on_battery_has_priority_over_online](../tests/test_ha_inputs.py#L44) | PASS |
| [ConfigurationTests.test_ages_require_explicit_settings](../tests/test_ha_inputs.py#L50) | PASS |
| [ConfigurationTests.test_backup_bindings_are_independently_optional](../tests/test_ha_inputs.py#L56) | PASS |
| [ConfigurationTests.test_unverified_freshness_and_duplicate_roles_rejected](../tests/test_ha_inputs.py#L64) | PASS |
| [ConfigurationTests.test_domain_validation](../tests/test_ha_inputs.py#L71) | PASS |
| [ConfigurationTests.test_fan_output_accepts_readback_switch](../tests/test_ha_inputs.py#L75) | PASS |
| [ConfigurationTests.test_execution_profile_requires_writable_sources_and_contract](../tests/test_ha_inputs.py#L79) | PASS |

## tests/test_observer.py

Пройдено: **8**.

| Тест | Результат |
|---|---|
| [BackupTests.test_single_value_no_average_and_warning](../tests/test_observer.py#L19) | PASS |
| [BackupTests.test_single_critical_is_not_downgraded_to_warning](../tests/test_observer.py#L28) | PASS |
| [BackupTests.test_both_missing_no_fabricated_temperature](../tests/test_observer.py#L35) | PASS |
| [BackupTests.test_backup_not_used_with_good_primary](../tests/test_observer.py#L42) | PASS |
| [BackupTests.test_mean_single_mean_resets_source_window](../tests/test_observer.py#L48) | PASS |
| [BackupTests.test_stale_backup_uses_valid_one](../tests/test_observer.py#L65) | PASS |
| [BackupTests.test_initial_dead_band_prefers_ventilation](../tests/test_observer.py#L72) | PASS |
| [OptionalBackupTests.test_zero_or_one_configured_backup](../tests/test_observer.py#L84) | PASS |

## tests/test_telemetry.py

Пройдено: **1**.

| Тест | Результат |
|---|---|
| [TelemetryTests.test_recent_history_is_bounded_and_ordered](../tests/test_telemetry.py#L7) | PASS |

## tests/test_timing.py

Пройдено: **4**.

| Тест | Результат |
|---|---|
| [TimingTests.test_continuous_condition_required](../tests/test_timing.py#L8) | PASS |
| [TimingTests.test_both_dwell_and_condition_must_expire](../tests/test_timing.py#L15) | PASS |
| [TimingTests.test_urgent_skips_only_optimization_timers](../tests/test_timing.py#L22) | PASS |
| [TimingTests.test_clock_reversal_restarts_timing](../tests/test_timing.py#L28) | PASS |

## tests/ha/test_execution.py

Пройдено: **21**.

| Тест | Результат |
|---|---|
| [ExecutionTests.test_startup_never_sends_and_enable_orders_cdr_fan_ac](../tests/ha/test_execution.py#L80) | PASS |
| [ExecutionTests.test_cool_to_automatic_vent_stops_ac_first](../tests/ha/test_execution.py#L92) | PASS |
| [ExecutionTests.test_missing_readback_times_out_without_next_command_or_retry](../tests/ha/test_execution.py#L103) | PASS |
| [ExecutionTests.test_battery_manual_fan_and_mains_return](../tests/ha/test_execution.py#L117) | PASS |
| [ExecutionTests.test_battery_fan_stops_at_ten_degrees](../tests/ha/test_execution.py#L136) | PASS |
| [ExecutionTests.test_unavailable_ac_on_mains_does_not_allow_fan_start](../tests/ha/test_execution.py#L145) | PASS |
| [ExecutionTests.test_disable_during_awaited_service_prevents_next_command](../tests/ha/test_execution.py#L155) | PASS |
| [ExecutionTests.test_service_exception_latches_and_does_not_retry](../tests/ha/test_execution.py#L169) | PASS |
| [ExecutionTests.test_shared_power_evidence_requires_explicit_profile_and_fresh_signals](../tests/ha/test_execution.py#L178) | PASS |
| [ExecutionTests.test_minimum_off_delays_ac_without_delaying_fan_stop](../tests/ha/test_execution.py#L193) | PASS |
| [ExecutionTests.test_source_loss_during_cdr_command_prevents_cool](../tests/ha/test_execution.py#L200) | PASS |
| [ExecutionTests.test_battery_threshold_hysteresis](../tests/ha/test_execution.py#L211) | PASS |
| [ExecutionTests.test_turning_off_does_not_undo_last_equipment_state](../tests/ha/test_execution.py#L225) | PASS |
| [ExecutionTests.test_no_output_control_before_permission_readback](../tests/ha/test_execution.py#L235) | PASS |
| [ExecutionTests.test_unconfigured_profile_cannot_be_armed](../tests/ha/test_execution.py#L243) | PASS |
| [ExecutionTests.test_unload_waits_for_inflight_command_and_sends_nothing_else](../tests/ha/test_execution.py#L250) | PASS |
| [ExecutionTests.test_control_form_and_fahrenheit_target](../tests/ha/test_execution.py#L270) | PASS |
| [ExecutionTests.test_switch_permission_profile](../tests/ha/test_execution.py#L283) | PASS |
| [ExecutionTests.test_out_of_range_target_blocks_ac_command](../tests/ha/test_execution.py#L297) | PASS |
| [ExecutionTests.test_command_journal_distinguishes_send_service_and_readback](../tests/ha/test_execution.py#L304) | PASS |
| [ExecutionTests.test_input_age_journal_deduplicates_and_records_recovery](../tests/ha/test_execution.py#L317) | PASS |

## tests/ha/test_runtime.py

Пройдено: **12**.

| Тест | Результат |
|---|---|
| [HaRuntimeTests.test_runtime_real_state_events_and_single_backup_warning](../tests/ha/test_runtime.py#L53) | PASS |
| [HaRuntimeTests.test_no_service_calls_and_unload_cleans_callbacks](../tests/ha/test_runtime.py#L66) | PASS |
| [HaRuntimeTests.test_sensor_values_and_diagnostics](../tests/ha/test_runtime.py#L81) | PASS |
| [HaRuntimeTests.test_watchdog_detects_old_data](../tests/ha/test_runtime.py#L89) | PASS |
| [HaRuntimeTests.test_schema_and_flow_validate_actual_states](../tests/ha/test_runtime.py#L96) | PASS |
| [HaRuntimeTests.test_bad_form_returns_error_without_commands](../tests/ha/test_runtime.py#L105) | PASS |
| [HaRuntimeTests.test_real_config_entry_lifecycle](../tests/ha/test_runtime.py#L111) | PASS |
| [HaRuntimeTests.test_full_platform_setup_creates_entities](../tests/ha/test_runtime.py#L122) | PASS |
| [HaRuntimeTests.test_same_publication_retains_sample_timestamp](../tests/ha/test_runtime.py#L155) | PASS |
| [HaRuntimeTests.test_switch_output_readback_never_sends_commands](../tests/ha/test_runtime.py#L162) | PASS |
| [HaRuntimeTests.test_optional_fault_binding_and_loss](../tests/ha/test_runtime.py#L178) | PASS |
| [HaRuntimeTests.test_independent_backup_slots_through_flow_and_runtime](../tests/ha/test_runtime.py#L213) | PASS |

## Дополнительный прогон 0.2.2

61 автономный и 37 HA-тестов прошли (98 всего), hassfest: 0 invalid. К базовому каталогу добавлены:

| Тест | Результат |
|---|---|
| [test_saved_on_survives_runtime_shutdown_and_reconciles](../tests/ha/test_execution.py#L344) | PASS |
| [test_explicit_off_is_preserved_on_restart](../tests/ha/test_execution.py#L353) | PASS |
| [test_restored_on_waits_for_missing_inputs](../tests/ha/test_execution.py#L362) | PASS |
| [test_disabled_profile_clears_saved_on](../tests/ha/test_execution.py#L373) | PASS |

## Дополнительный прогон 0.2.3

63 автономных и 38 HA-тестов: **101 PASS**. Hassfest: 0 invalid.

- [test_ac_limit_preserves_timestamp_and_other_sources](../tests/test_ha_inputs.py#L93): PASS.
- [test_ac_age_validation_and_migration_default](../tests/test_ha_inputs.py#L102): PASS.
- [test_ac_publication_limit_separate_from_other_roles](../tests/ha/test_execution.py#L383): PASS.

## Дополнительный прогон 0.2.4

63 автономных и 41 HA-тест: **104 PASS**. Регрессия до исправления воспроизводила WAIT_TIMING вместо COMPLETE при действующем COOL; после исправления проверяет единственную подтверждённую команду 23→22 без повторов.

- [test_running_ac_setpoint_drift_corrected_without_seasonal_dwell](../tests/ha/test_execution.py#L403): PASS.
- [test_setpoint_drift_does_not_override_manual_or_fault](../tests/ha/test_execution.py#L423): PASS.
- [test_seasonal_change_still_waits_for_condition_hold](../tests/ha/test_execution.py#L435): PASS.

## Дополнительный прогон 0.2.5

63 автономных и 43 HA-теста: **106 PASS**.

- [test_ac_readback_after_160_seconds_is_confirmed_without_retry](../tests/ha/test_execution.py#L468): PASS.
- [test_ac_timeout_after_180_seconds_latches_without_retry](../tests/ha/test_execution.py#L479): PASS.

## Дополнительный прогон 0.2.6

63 автономных и 45 HA-тестов: **108 PASS**. Проверяются точные payload, последовательное подтверждение режима и температуры, запрет второго шага без первого readback, тайм-аут 3 s.

- [test_auto_mode_then_target_are_separate_confirmed_commands](../tests/ha/test_execution.py#L489): PASS.
- [test_missing_mode_confirmation_never_sends_temperature](../tests/ha/test_execution.py#L500): PASS.
