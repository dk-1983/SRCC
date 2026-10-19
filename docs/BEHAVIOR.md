[**English**](BEHAVIOR.md) · [**Русский**](BEHAVIOR_RU.md)

# Implemented behavior — SRCC 0.8.0

This describes current behavior. [Changelog](../CHANGELOG.md) records superseded designs; older values must not be treated as current defaults. Field evidence and remaining work are in [Validation](VALIDATION_PLAN.md).

## Control and observation

New entries start in observation mode. Commands require the command profile, confirmed equipment feedback semantics, and Automatic control ON. ON/OFF survives reload/restart; each startup reconciles current inputs rather than replaying old commands. Disabling the command profile clears stored ON.

Turning Automatic control OFF stops new commands. It does not turn devices OFF, restore C.dr=1, or cancel a command already delivered.

## Temperature and input quality

The primary rack sensor and outdoor sensor are required. Backup sensors are optional and sampled once per minute; loss of availability is processed immediately. A valid primary takes priority. Two valid time-aligned backups are averaged. A single valid backup can be used with a degradation warning. Missing all usable rack readings blocks decisions; no synthetic temperature is substituted.

Temperature units are normalized. Unknown, unavailable, restored, invalid, and stale values are not trusted readback. The one-second watchdog checks age; it does not poll hardware or call update_entity. Generic HA publication time is evidence of publication, not proof of physical measurement. Haier uses its own [UART freshness contract](HAIER_TELEMETRY.md).

## Normal selection

Defaults are vent_to_ac=+1 °C, ac_to_vent=−1 °C, and ac_hard_min=−15 °C. Negative thresholds are allowed, with strict ordering `ac_hard_min < ac_to_vent < vent_to_ac`. AC is allowed at its lower boundary and blocked below it. Existing installation settings are preserved on upgrade.

At/above vent_to_ac, choose usable AC; at/below ac_to_vent, choose usable ventilation if not locked out. Inside the band, preserve a usable current mode. With unknown mode, both methods usable, and temperature strictly inside the band, initial preference is ventilation.

A lower outdoor temperature alone does not prove ventilation can cool the rack. The backup-ventilation outdoor limit of +10 °C is separate from seasonal thresholds.

## Commands and readback

AC transition: C.dr=0 → confirm → fan output OFF → confirm → COOL → confirm → setpoint correction if needed → confirm. Mode and temperature are separate commands. Ventilation transition: AC OFF → confirmation or valid unpowered evidence → enable automatic ventilation. Backup ventilation uses C.dr=0 and direct output commands.

The TRM output switch provides feedback in automatic mode as well as manual control in C.dr=0. Electrical output feedback does not prove airflow. A different controller needs equivalent normalized semantics.

A completed HA service call is not equipment confirmation. Missing readback produces an execution error. Outstanding confirmation is retained across requested-mode/power changes. Generic errors require investigation and a new OFF→ON reconciliation; Haier command-ID conflicts have their own bounded reconciliation path.

Normal selection uses condition_hold and minimum_dwell. Urgent recovery bypasses seasonal delays, but never power checks, the outdoor minimum, faults, fresh inputs, or interlocks. Maintaining a confirmed mode and restoring from confirmed IDLE do not wait for normal seasonal dwell. There is no additional SRCC compressor restart timer.

## Power and backup ventilation

In the documented installation the fan/TRM are UPS-backed; AC and its communication controller are not. Their mains supply is shared with the UPS input. Only with this wiring explicitly confirmed, consistent fresh UPS Online=off and On battery=on, and unavailable climate may AC be inferred unpowered. Fresh telemetry reporting an active AC overrides that inference. Availability alone never proves absence of an AC fault.

Returning mains invalidates unpowered evidence immediately. AC auto-restart may overlap with ventilation briefly; software does not provide a physical interlock.

Initial backup thresholds are rack ON at 24 °C and OFF at 20 °C. Outdoor temperature must be below 10 °C and strictly below rack temperature, also meeting the configured minimum difference. This does not control UPS load shedding, EPO, battery charge, or remaining runtime.

## Alarms, recovery, and ventilation retry

WARNING defaults to rack ≥26 °C; CRITICAL to ≥28 °C. Critical rack heat prioritizes usable AC regardless of the seasonal choice. The critical latch clears only on a fresh rack reading newer than critical entry and at/below `min(critical_recovery, ac_target + rack_recovery_offset)`. Initial critical_recovery is 27 °C; offset is 0 °C. The configured target plus offset must remain below WARNING. There is no 120-second recovery timer and the AC inlet sensor cannot clear rack overheating.

A critical thermal incident sets a persistent ventilation-retry lock. It is also set before CRITICAL when automatic ventilation is confirmed active, a full five-minute trend is ready, both slope and total change are positive, and rack temperature reaches WARNING. This early rule does not require minimum_vent_rise.

After recovery, `VENT_RETRY_LOCKED` keeps usable AC selected. Reload does not clear the lock. Explicit Automatic control OFF→ON permits another attempt, but a current critical condition can immediately reinstate the lock. This does not prohibit usable backup ventilation if AC cannot run.

The optional minimum_vent_rise separately defines meaningful rise over the observation window and can mark ventilation ineffective. Without it, a trend can still be displayed and the early WARNING rule above still operates. No active ventilation history means the trend remains unknown.

## Downward-only adaptation

During confirmed AC operation, reaching WARNING proposes one permanent step down from the configured AC setpoint. It is nominally 1 °C, aligned to the device setpoint grid and limited by min_temp. Commands still pass normal eligibility and confirmation checks. Only confirmation of that proposed permanent target updates `ac_target` in the existing options field. There is no extra adapted-target setting and no upward learning.

One uninterrupted episode produces only one permanent step. Rearm requires a trusted rack reading at/below WARNING−1 °C, with no pending proposal, followed by reaching WARNING again. Missing data does not rearm. Episode state is persisted before the command. A restart cannot cause another decrement for the same episode; an unfinished proposal is not replayed.

At the device minimum, no further permanent decrement is made and adaptation_limit is reported. The field remains manually editable.

## Temporary assistance for a cold AC inlet

Ventilation may cool the inlet sensor while the rack remains hot. At WARNING or active CRITICAL, with AC selected, SRCC can calculate a one-shot temporary target at least 1 °C (or one device step if larger) below a fresh AC reading, never above the configured target and never below min_temp. It is rounded down on the device grid. The temporary value does not chase every subsequent falling reading.

Example: permanent target 22, proposed learned target 21, AC reading 18.5, step 1, minimum 16 → temporary command 17 °C. After the rack reaches the proposed permanent target plus rack offset, capped by critical_recovery, SRCC requests 21 °C. Only confirmed 21 is saved; temporary 17 is not learned. Without a permanent proposal, recovery uses the configured target instead. A critical episode alone does not create permanent learning.

Missing valid temperature or device limits/resolution leaves the ordinary target in use. For Haier with a 16 °C minimum, neither temporary nor permanent requests go below 16. If the inlet is colder than that, a setpoint command cannot guarantee compressor start. The appliance retains its internal controls.

## Diagnostics and notifications

Decision reason exposes execution_phase/reason/error, blockers, last_command, input_issues, rack_recovery_target, boost_recovery_target, active_ac_target, emergency_ac_target, adaptation_pending, adaptation_episode_closed, and vent_retry_locked.

INFO events distinguish command_sent, service_completed, command_confirmed, adaptation_requested, adaptation_saved, and temporary-target changes. WARNING reports quality loss, conflicts, and limits. Voice ERROR denotes critical/execution conditions; it is separate from the Python logger level of the diagnostic voice_issue line.

An active critical latch can remain at a temperature below the entry threshold. A critical notification is not proof that the current reading is ≥28 °C. Actual recovery emits a resolution and an optional INFO. Voice queue/reminder behavior belongs to the receiving center; see [notifications](VOICE_NOTIFICATIONS.md).
