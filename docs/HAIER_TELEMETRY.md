[**English**](HAIER_TELEMETRY.md) · [**Русский**](HAIER_TELEMETRY_RU.md)

# Haier ESP telemetry and command confirmation

The optional schema-1 profile consumes the telemetry contract introduced by Haier ESP v1.3.0. It uses MQTT entities and command results without replacing normal climate service calls. Firmware documentation: [Haier-ESP32-Modbus](https://github.com/dk-1983/Haier-ESP32-Modbus). Generic climate bindings remain available when this profile is not selected.

## Configure

1. Turn Automatic control OFF while first checking a new binding.
2. Select the Haier climate entity and its Telemetry sensor.
3. Both must be registered MQTT entities belonging to the same HA device. The telemetry unique_id must end in `_telemetry`.
4. Set the firmware's MQTT result topic ending in `/result`; wildcards are not allowed.
5. Initially use **Haier telemetry command confirmation timeout = 35 s**.
6. Save, wait for live telemetry, inspect quality, then enable Automatic control.

A separate fault sensor is unnecessary for this profile; fault comes from the same UART snapshot. Clearing the telemetry binding returns to generic climate/fault processing. Saving options preserves the Automatic control setting, so leaving it ON can resume commands after reload.

## Snapshot and freshness

Mode, target, measured temperature, and fault are read from one snapshot. Telemetry temperatures are °C; outgoing climate service temperatures are converted to HA units when required. Ordinary climate updates do not replace selected telemetry evidence.

The adapter requires a live sensor event after startup; restored/cached states are not accepted as live measurements. The one-second watchdog only checks age and does not poll UART or refresh entities.

| Evidence | Interpretation |
|---|---|
| No live telemetry for at least 15 s | Heartbeat stale; cannot confirm commands |
| UART sample age at least 30 s | Measurement stale even if heartbeat continues |
| Same status_frames/sample time | Heartbeat only; does not renew measurement time |
| New UART samples with unchanged values | Fresh evidence; a temperature change is not required |
| Unknown schema, invalid types/clocks, time reversal | Snapshot rejected |
| Changed status values without a new UART measurement | Snapshot rejected |
| available=false | No usable active-state evidence; not proof of OFF |
| fault_valid=false or incomplete status | Fault unknown, not assumed clear |
| Valid fault code 0 | No reported error in that snapshot |
| Valid fault code 1–255 | Fault; native-code interpretation is not claimed |
| New boot_id during a command | HAIER_RESTART_DURING_COMMAND |

HA MQTT removes the reserved JSON attribute `available`. Since 0.3.1, when it is absent, strict fault_valid=true is used under the schema-1 firmware contract, which requires fresh valid complete UART status. Explicit available=false is never overridden. A numeric sensor state or fault=false alone is insufficient.

Source age is inferred conservatively from ESP monotonic times and HA receipt time. It is a lower bound: unknown transport delay cannot be removed. Repeated frames cannot move measurement time forward. Firmware publishes telemetry without retain; HA's sensor event API does not expose its retain flag, so this remains a firmware contract. Result messages are subscribed directly and retained results are ignored.

## Ordered commands

SRCC calls climate.set_hvac_mode and climate.set_temperature separately. For each action it:

1. Has a result subscription before dispatch.
2. Captures the source session, sample counter, and measurement time.
3. Receives accepted for the expected field and records the firmware request_id.
4. Requires confirmed for that ID plus a later matching UART snapshot with command_state=confirmed and the expected value.
5. Only then advances to the next command.

Result and telemetry can arrive in either order. Heartbeat, service completion, an ordinary climate update, or an advancing counter alone is insufficient. The firmware requires matching subsequent UART responses. The 35-second initial timeout allows for the firmware's 30-second confirmation budget; it is not a measured latency guarantee.

## External commands and conflicts

An external pending command causes WAIT_HAIER_COMMAND / EXTERNAL_COMMAND_PENDING. SRCC waits and then reconciles mode and setpoint if automation remains enabled.

Since 0.4.1, HAIER_COMMAND_CONFLICT / HAIER_COMMAND_SUPERSEDED enters bounded reconciliation instead of permanent conflict lockout. It waits for the configured Haier budget (minimum 5 s), at least 5 s without a new result, valid telemetry from the same session, a newer UART sample, and confirmed command state. Then it abandons ambiguous attribution and builds a new plan; another command's result is not accepted as SRCC success.

Repeated conflicts restart the wait rather than causing a command storm. Generic timeouts, service/firmware errors, and ESP restarts remain latched execution errors. Investigate and use Automatic control OFF→ON for a new reconciliation. Firmware assigns request IDs; schema 1 cannot guarantee sender attribution across simultaneous writers.

When AC is selected, COOL is restored before correcting the target. All power, fault, outdoor-temperature, ventilation-OFF, and fresh-readback guards remain. Current SRCC has no extra compressor restart delay; older documentation mentioning it is superseded.

## Diagnostics

The ac_telemetry section includes quality, rejection reason, heartbeat/source age, counters, fault fields, and command acceptance/confirmation state. It omits raw boot/request IDs, source entity IDs, and the result topic. Input freshness_basis is haier_uart_sample; telemetry_reason explains invalid/unverified/stale states.

Enable the SRCC INFO logger to see command_sent, service_completed, command_confirmed, command_conflict, and command_reconciliation. The tests simulate MQTT and equipment. Field checks must separately verify real retained-message behavior, delayed results, restart, conflict handling, and command feedback. The profile is not automatically compatible with the Samsung bridge.
