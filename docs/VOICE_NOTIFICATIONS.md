[**English**](VOICE_NOTIFICATIONS.md) · [**Русский**](VOICE_NOTIFICATIONS_RU.md)

# Voice notifications

SRCC publishes one process through the external v1 interface of [Home-Control](https://github.com/dk-1983/Home-Control), my apartment automation project containing the voice center. Equipment control does not depend on the center. See the [protocol contract](VOICE_PROTOCOL_V1.md).

Configure voice_info, voice_warning, voice_error, and voice_repeat independently; all default OFF. Repeats require voice_error and voice_repeat. voice_area identifies the process area, otherwise the SRCC device area is used. **Notification maintenance (cooling unchanged)** is persistent and does not change Automatic control. Failed restoration of maintenance state keeps speech disabled.

The documentation is bilingual. Generated spoken text in 0.8.0 remains Russian; changing documentation language does not localize speech.

## Event levels

| Key | Meaning |
|---|---|
| temperature_high | WARNING at the rack warning threshold; ERROR while the critical latch is active. Missing measurements do not prove recovery. |
| input_<role> | Named input with stale, unavailable, unknown, invalid, or unverified quality |
| temperature_source | Identifies primary failure and the selected backup/average; ERROR when no usable rack reading remains with automation ON |
| ac_fault | Confirmed AC fault, with device and code where available; unknown does not clear an existing fault |
| ventilation_ineffective | Temperature-trend evidence, not proof of a broken fan |
| vent_retry_locked | Automatic return to ventilation is held after a thermal incident/warming trend |
| equipment_conflict | Contradictory cooling/ventilation feedback |
| power_supply | UPS battery operation, unknown power, or conflicting signals with sources identified |
| cooling_unavailable | No usable cooling method; ERROR for an urgent decision |
| action_deferred | Real interlock/external-command wait and its reason; urgent conditions use ERROR |
| execution_failure | Failed action and execution reason; execution stopped |
| normal_wait | One INFO on entering a normal seasonal wait, not a message every countdown tick |
| mode_changed | INFO after confirmed command and completed transition |
| target_restored | INFO after setpoint confirmation; permanent adaptation reports previous/new values and persistence |
| adaptation_limit | WARNING that no further permanent downward step is available |
| Resolved issue | Explicit resolution first, then an optional one-shot INFO |

Recovery, reset, or removal of a blocker is not described as proof of repair or equipment startup. Unknown reasons point to diagnostics rather than copying arbitrary exception text.

## Duplicate suppression

Unused backup sensor loss is delayed for 20 seconds; loss affecting control is reported immediately. Input recovery and return to the primary sensor require 20 seconds of stable recovery for notifications only. Cooling reacts without that notification delay.

Haier climate/fault/telemetry share one input_ac_telemetry event. Primary failure is grouped with the backup-source message; UPS input failures are grouped with power state. Regrouping is not reported as physical recovery.

Active messages do not embed a changing temperature/age/countdown every second. Unchanged issues are not republished on every poll. ERROR reminder intervals, quiet hours, routing, queues, and volume belong to Home-Control. Disable voice_repeat to stop periodic reminders, not first occurrences or escalation from WARNING to ERROR.

An active critical latch can persist below the threshold until the configured rack recovery target is reached. A later spoken critical message is not evidence of a new threshold crossing. Use timestamped decision/voice_issue records and the receiver's playback history to distinguish a new event, a reminder, and delayed speech.

## Isolation and diagnostics

Calls run in a separate task with a 3-second timeout. Rejection, timeout, or center outage clears queued INFO and triggers a later registration with the current snapshot. Historical INFO is not replayed. Maintenance cancels pending speech through the center but cannot retract a service already in flight.

INFO-level `voice_issue` log records contain a semantic level, active flag, and bounded context: source integrations, input qualities, selected temperature source, decision, and execution phase/reason. The logger's INFO level does not turn an ERROR voice issue into an informational event. Source IDs and raw attributes are not copied into this context.

The mock receiver tests the producer contract, not actual speaker output. Real speech latency and receiver queue behavior require joint observation.
