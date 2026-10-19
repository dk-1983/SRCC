[**English**](CHANGELOG.md) · [**Русский**](CHANGELOG_RU.md)

# Changelog

## 0.8.0 — downward-only permanent adaptation

- At WARNING during confirmed AC operation, propose a one-degree downward setpoint step, aligned to device limits/resolution.
- Save the confirmed permanent target into the existing ac_target field without reloading the integration. Preserve the processed episode across restart; rearm at WARNING−1 °C before another episode.
- Keep temporary cold-inlet assistance separate from the permanent target. Never learn the temporary lower value. No automatic upward adaptation.
- Add adaptation diagnostics and messages. Respect the device minimum, outdoor restriction, power, and sequential confirmation.
- 201 software tests passed (95 standalone + 106 HA); hassfest: 0 invalid. Installed on the field system; permanent adaptation still awaits a naturally occurring field acceptance event.
- English-default documentation with Russian counterparts, related-project diagrams, and installable release archive. Voice message text remains Russian.

## 0.7.3 — early temporary assistance

Temporary setpoint assistance starts above configured WARNING rather than waiting for CRITICAL. Hold until the rack recovery target, not merely until WARNING disappears. Superseded in part by permanent adaptation in 0.8.0. 199 tests passed.

## 0.7.2 — temporary target at CRITICAL

Calculate a one-shot colder target from fresh AC inlet temperature, bounded by device limits and step. Keep separate command confirmation and restore the normal target after rack recovery. 197 tests passed.

## 0.7.1 — rack recovery and ventilation retry lock

Use the rack sensor and configured offset for recovery, not the AC inlet. Persist a ventilation retry lock after thermal incidents; explicit OFF→ON permits a controlled retry. Escalate warming ventilation at WARNING after a full trend window. 192 tests passed.

## 0.7.0 — negative seasonal thresholds

Allow negative seasonal thresholds, default the AC lower limit to −15 °C, and allow equality. Remove recovery_hold and the elapsed-recovery sensor. This version initially used AC inlet temperature in recovery; 0.7.1 superseded that design. 188 tests passed.

## 0.6.0 — protect rack cooling

Prioritize available AC during critical rack heat and remove the redundant external compressor restart delay. Avoid seasonal waits when restoring from IDLE or maintaining a confirmed mode. Keep interlocks and fresh feedback. 184 tests passed.

## 0.5.0 — informative voice events

Identify equipment/input sources, selected backup, power states, blockers, and failed actions. Debounce unused-backup loss and recovery, group related issues, and add diagnostic voice context. 177 tests passed.

## 0.4.1 — Haier command-conflict recovery

Bounded reconciliation for conflicting/superseded command IDs instead of permanent conflict lockout. Preserve initial cause and restore COOL then target through separate commands. Generic failures remain latched. 163 tests passed.

## 0.4.0 — voice-source client

Add one process client for the public Home-Control v1 interface, preferences, persistent notification maintenance, registration/lease, explicit resolution, and diagnostic isolation. The original receiver snapshot was not yet implemented; mock receiver tests validated the client. 149 tests passed.

## 0.3.1 — HA MQTT attribute compatibility

Handle HA filtering of the reserved available attribute using the schema-1 fault_valid contract; never override explicit false availability. 133 tests passed.

## 0.3.0 — Haier ESP schema-1 profile

Add UART sample/heartbeat freshness, session and command-result correlation, shared fault evidence, and profile diagnostics. Generic climate remains supported.

## 0.2.6 — sequential AC commands

Send mode and temperature separately, with individual readbacks. Field correction of the Haier target succeeded after the combined command path failed.

## 0.2.5 — delayed AC confirmation

Correct confirmation handling for slow AC feedback and retain unresolved commands when policy changes.

## 0.2.4 — running AC target correction

Correct an already running AC target without seasonal dwell.

## 0.2.3 — separate AC publication age

Add ac_max_age, initially 180 s, independently of other input age budgets.

## 0.2.2 — restore control preference

Persist Automatic control ON/OFF and reconcile current inputs after restart instead of replaying prior commands.

## 0.2.1 — bounded diagnostics

Add bounded quality, decision, and command events for field observation.

## 0.2.0 — command profile

Add explicit equipment command capability with verified transitions, power conditions, and interlocks.

## 0.1.0 — observation

Add the HA UI, entities, diagnostics, decision core, optional temperature backups, and initial architecture. No equipment commands in the initial observation release.
