[**English**](INSTALLATION.md) · [**Русский**](INSTALLATION_RU.md)

# Installation and configuration — 0.8.0

SRCC is a custom Home Assistant integration. The development checks used HA 2026.9.2 / Python 3.14.5; field operation has included HA 2026.9.3. Other builds require validation. HACS publication is not claimed.

## Install and update

Download the integration archive from [Releases](https://github.com/dk-1983/SRCC/releases). Extract its `custom_components/fourvrs_server_room` directory into the HA configuration directory, preserving that path. Restart HA. Add **Server Room Climate Controller** under Settings → Devices & services.

For an update, replace the integration files and restart HA. Do not delete the existing entry. Its entity bindings and settings remain. Automatic control ON/OFF is restored; command eligibility is checked again. A new installation starts OFF.

## Entity bindings

| Role | Meaning |
|---|---|
| Room/rack temperature (`trm_temperature`) | Required primary sensor at the protected hot point; °C, °F, or K |
| Outdoor temperature | Required outdoor sensor |
| Backup temperature 1 / 2 | Optional independent sensors; zero, one, or two allowed |
| Ventilation permission (`vent_enable`) | For control: number 0/1 or switch; 0 allows direct fan output control, 1 restores automatic regulation |
| Fan output (`vent_output`) | For control: switch with readback; observation also accepts binary_sensor |
| Air conditioner (`climate`) | HA climate entity supporting COOL/OFF and temperature setpoint |
| Air conditioner fault | Optional binary_sensor, ON=fault; not having it does not prove fault-free hardware |
| UPS mains online | binary_sensor ON=external mains present, not network connectivity |
| UPS on battery | binary_sensor ON=battery power |
| Haier telemetry / result topic | Optional special profile described in [Haier telemetry](HAIER_TELEMETRY.md) |

Observation allows read-only permission/output entities. Commands require writable supported domains. Internal role names retain historical TRM/Bolid naming, but sensor bindings are not manufacturer-locked. The 0/1 permission mapping matches the original TRM configuration; a different channel encoding needs an adapter.

Do not select SRCC's own outputs as inputs. Optional fields can be added or cleared later through integration options. The options form replaces the complete configuration, including cleared bindings.

## Freshness settings

Set these for the actual publication behavior of upstream integrations. Zero is not an appropriate usable maximum age.

| Setting | Meaning / example |
|---|---|
| max_age | Positive publication-age limit for generic inputs; 120 s was used for sources updating at least once a minute |
| ac_max_age | Separate generic climate publication limit; default 180 s |
| backup_max_skew | Allowed timestamp difference for averaging two backups; example 10 s |
| sample_tolerance | Timing tolerance for minute sampling; must be below 60 s; example 5 s |
| recovery_max_gap | Positive legacy timing/validation parameter; example 70 s; does not restore the removed recovery hold timer |

The HA publication-age acknowledgement does not certify physical measurement freshness. Confirm that upstream states actually represent readback. An unchanged value can still be newly reported, while a repeated old sample is not a fresh physical measurement. Haier profile limits are distinct from generic ac_max_age.

## Enable equipment commands

Enable the command profile only after checking feedback and permission semantics. Confirm the 0/1 behavior and the electrical output mapping. Confirm shared mains only if the AC has no backup supply, shares the UPS input supply, and powers its communication controller itself.

Then enable **Automatic control**. Switching it OFF leaves equipment in its last state; it does not restore the temperature controller automatically. Do not use it as an emergency equipment stop.

## Temperatures and timing

| Key | Initial value | Meaning |
|---|---:|---|
| ac_target | 22 °C | Normal setpoint; confirmed downward adaptation updates this same field |
| warning_on / critical_on | 26 / 28 °C | Protected rack thresholds |
| critical_recovery | 27 °C | Upper cap for the recovery target |
| rack_recovery_offset | 0 °C | Calibrated rack offset above normal AC target; target+offset must be below WARNING |
| ac_hard_min | −15 °C | AC allowed at equality, prohibited below |
| ac_to_vent / vent_to_ac | −1 / +1 °C | Seasonal hysteresis; negative values allowed |
| emergency_on / emergency_off | 24 / 20 °C | Backup fan demand |
| emergency_delta | 0 °C | Minimum rack-minus-outdoor difference; outdoors must still be strictly colder |
| emergency_outdoor_max | 10 °C | Backup ventilation prohibited at or above this temperature |
| minimum_vent_rise | Optional | Significant rise over the ventilation observation window |
| command_timeout | 30 s | HA service-call/general confirmation budget |
| ac_readback_timeout | 3 s | Generic AC confirmation budget; tune to actual feedback |
| haier_readback_timeout | 35 s | Haier telemetry/result confirmation budget |
| condition_hold / minimum_dwell | 30 / 300 s | Normal cooling-choice stability and dwell |

The removed minimum_ac_off and recovery_hold values are ignored. There is no external compressor delay or 120-second recovery hold. Do not reduce freshness or confirmation timeouts to disable a seasonal delay.

A configuration example used during the field investigation is AC minimum −6 °C, AC→vent −4 °C, vent→AC −2 °C. These are installation-specific, not new defaults. No upgrade rewrites saved thresholds.

Rack temperature is measured at a hot point and can differ substantially from the AC inlet. Calibrate rack_recovery_offset from stable measurements; a single snapshot is not a measured long-term offset. If the recovery target cannot be reached, the critical latch can remain while cooling continues. See [behavior](BEHAVIOR.md).

## Version 0.8.0 adaptation

At WARNING during confirmed AC operation, a downward step is proposed. After its command is confirmed, the existing AC target field is updated without a reload. A single episode cannot repeatedly decrement; a new episode needs cooling to WARNING−1 °C and a later rise. There is no automatic upward learning.

Cold-inlet assistance can temporarily command a lower value. That value is not the permanent learned target. Both paths respect device min_temp and setpoint resolution. Watch adaptation_requested, command_confirmed, and adaptation_saved; the latter proves the persistent setting update. A temporary command confirmation alone does not.

## Diagnostics and INFO logging

The device page groups the integration entities. Decision reason attributes contain the execution phase, reason, error, pending command, and input issues. Download diagnostics from the device page for a bounded, sanitized view.

```yaml
logger:
  default: warning
  logs:
    custom_components.fourvrs_server_room: info
```

A clean WARNING log does not show successful INFO-level commands. The controller logs dispatch, service completion, and equipment confirmation separately.

Voice preferences and reminders default OFF. Configure them through [Voice notifications](VOICE_NOTIFICATIONS.md). Notification maintenance affects speech only. Documentation language links do not change the current Russian speech strings.

## Limits

SRCC does not manage UPS battery load, EPO, or fire suppression. Fan output does not prove airflow. A working climate entity does not prove absence of internal AC faults. Generic timing must match upstream behavior. Seasonal transitions, power recovery, sensor failures, and learned setpoint changes need separate field acceptance.
