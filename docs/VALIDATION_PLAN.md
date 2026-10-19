[**English**](VALIDATION_PLAN.md) · [**Русский**](VALIDATION_PLAN_RU.md)

# Validation status and acceptance plan

## Release 0.8.0 baseline

**201 software tests passed:** 95 standalone and 106 HA. Hassfest: one integration, zero invalid. HA checks use actual HA classes with simulated equipment/services/MQTT boundaries. The tested container included HA 2026.9.2 / Python 3.14.5. The integration has also been installed on the field HA system. No claim of universal equipment or HA-version compatibility follows from this.

[Test catalog](TEST_CATALOG.md) lists executable test methods; [Development](DEVELOPMENT.md) gives commands. A method count is not a count of physically verified scenarios.

## Field evidence

| Observation | Supported conclusion | Limit |
|---|---|---|
| TRM output switch changed automatically above its setpoint | The selected output entity can report automatic TRM output state | Does not prove airflow |
| Separate Haier COOL and setpoint commands succeeded | Sequential control works on the tested installation | Combined-command failure cause in the upstream driver was not independently established |
| Haier confirmation logged in about 2.082 s in one sample | That command received timely confirmation | Not a guaranteed maximum latency |
| Seasonal AC→ventilation and ventilation→AC transitions were observed | Both transition directions have worked on the installation | Does not prove every weather/load/power scenario |
| A one-hour older-profile trace showed intervals up to 160.29 s and repeated stale at 120 s | The old publication budget was too short for that observation | Recorder cannot expose every unchanged publication or physical poll |
| Cooling cycle after installing 0.8.0: rack 25.58→23.38 °C | Ordinary cooling handled this observed cycle below WARNING | Permanent adaptation did not trigger and remains unverified in the field |

The cold-air inlet near the AC can be much colder than the rack's hottest point after ventilation. This explains why COOL at a normal target may not immediately demand compressor operation. Observations motivated temporary assistance and rack-based recovery; they do not guarantee a future cycle will avoid CRITICAL.

The original fire-protection controller and its independent power/EPO behavior are installation context, not SRCC-controlled functions or software-test results.

## Software regression areas

- Temperature units, invalid/stale inputs, primary/two-backup/single-backup selection and timestamp skew.
- Negative seasonal thresholds and equality at the AC hard minimum; hysteresis and selection timing.
- Command ordering, fan/permission/AC interlocks, separate mode/target readbacks, timeout, power changes, and automation OFF.
- UPS mains/battery evidence and shared-supply inference; unavailable is not OFF.
- Rack-based CRITICAL recovery, no 120-second timer, persistent ventilation retry lock, and earlier WARNING/trend escalation.
- Haier schema/type/clock/session validation, heartbeat versus new UART samples, filtered MQTT attributes, result IDs/retain, external pending commands, and conflict reconciliation.
- Temporary target with device bounds and °F conversion; no chasing falling inlet readings; pending confirmations survive target changes.
- Permanent downward adaptation: no save before confirmation, one step per episode, hysteresis, stored episode state, no reload for internal options updates, no upward learning, device minimum, and cold-inlet assistance without permanently saving the temporary target.
- Voice identity, lifecycle, explicit resolution, grouping/debounce, maintenance, preferences, center outage, and isolation from cooling.

## Remaining field acceptance

| Scenario | Evidence to collect |
|---|---|
| WARNING during AC with normal inlet temperature | adaptation_requested, exact service, readback, adaptation_saved, updated settings, rack trajectory |
| Cold inlet after ventilation | Temporary target within limits; cooling response; later permanent target confirmation without learning the temporary value |
| Repeated warning episode | No second step while continuously high; one new step only after trusted hysteresis recovery and another threshold crossing |
| Reload during/after adaptation | No duplicate decrement; settings and episode state agree; no replayed command |
| Ventilation ineffective | Confirmed output and full trend; timely AC selection; retry lock prevents repeated failed cycling |
| Mains loss/return | Fresh paired UPS evidence, AC state, fan sequence, automatic restart behavior |
| Source loss and recovery | Correct backup or blocked state; named notification; no false OFF inference |
| Voice delivery | Compare SRCC events with center queue/playback timestamps, including reminders and resolution |
| Samsung alternative | Separate bench validation of climate commands/readback and unavailable/fault semantics; not yet confirmed with SRCC |

Do not intentionally overheat the production rack to test CRITICAL. Use isolated simulation/bench scenarios, and collect natural field observations where appropriate. If a test issues an unexpected command or evidence becomes ambiguous, stop the test and capture diagnostics. Disabling SRCC does not stop equipment or restore C.dr=1 automatically.

A useful trace includes synchronized rack/outdoor/AC temperatures, mode and target, C.dr and fan output, UPS states, command/readback events, and actual timestamps. HOLD_AC alone does not prove transition order. A clear WARNING log does not prove no INFO commands occurred.

## Known boundaries

The thermal latch and trend windows are not persisted. A permanent transaction replay journal is not implemented. No compressor-fault detection by temperature, independent airflow proof, UPS charge/runtime load control, or arbitrary multi-AC executor profile is claimed. Upward setpoint optimization is deliberately outside 0.8.0.

The Russian counterpart retains detailed historical release observations. They are historical evidence; current rules are in [Behavior](BEHAVIOR.md) and [Changelog](../CHANGELOG.md).
