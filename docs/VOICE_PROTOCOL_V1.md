[**English**](VOICE_PROTOCOL_V1.md) · [**Русский**](VOICE_PROTOCOL_V1_RU.md)

# Public voice-source contract v1

This is the external interface used by SRCC with [Home-Control](https://github.com/dk-1983/Home-Control). SRCC does not import center internals, modify its hass.data, call speakers, or require the center for cooling. An installed receiver must implement this contract; the repository link alone is not a compatibility check. The original Home-Control 0.11.0 snapshot did not implement it. Later field observations of speech do not establish every receiver edge case below.

## Identity and envelope

A source is exactly `{domain, config_entry_id, process_id}`. Each value is a nonempty string up to 128 characters. The receiver validates that the loaded config entry belongs to the domain. Internal/external namespaces must not collide. Services are a trusted internal HA interface, not device authentication.

SRCC registers one source:

```json
{"domain": "fourvrs_server_room", "config_entry_id": "ENTRY_ID", "process_id": "climate"}
```

Renaming, replacing equipment, or changing area does not change this identity. Recreating the configuration entry does. `name` and `area_id` are metadata, not identity. An omitted SRCC voice_area uses the SRCC device's HA area. Unknown areas must not suppress ERROR: the center applies its INFO fallback policy and routes WARNING/ERROR to its configured available speakers.

Every request contains:

| Field | Requirement |
|---|---|
| protocol_version | Integer exactly 1, never boolean |
| source | Exactly the three strings above |
| producer_session | Nonempty string up to 128 characters; new UUID hex per SRCC runtime |
| revision | Positive integer, never boolean; increasing across operations within the session |

A new producer_session replaces the prior one for a source. Retired sessions cannot later register, publish, or unregister. The receiver retains retirement evidence for its lifetime; overflow cannot silently authorize an old session.

The receiver generates a new nonempty center_session (up to 128 characters) on startup. It is required on publish/unregister and comes only from an accepted registration. Old center sessions are rejected with registration_required. Receiver restart clears registrations and queues; producers register current snapshots again.

## home_control.register_voice_source

In addition to the envelope, require:

| Field | Requirement |
|---|---|
| name | Nonempty string, up to 200 characters |
| area_id | HA area ID string up to 128 characters, or null |
| preferences | Exactly voice_info, voice_warning, voice_error, voice_repeat; strict booleans |
| maintenance | Strict boolean; true cancels queued speech/reminders |
| active_issues | Complete current list, maximum 64; each `{key, level, message}` |

Issue keys are unique, stable, nonempty strings up to 80 characters. Active levels are WARNING or ERROR. Messages are nonempty strings up to 500 characters. Maintenance requires an empty active_issues list. The center may prepend the source name once without changing identity.

```json
{
  "protocol_version": 1,
  "source": {"domain": "fourvrs_server_room", "config_entry_id": "ENTRY_ID", "process_id": "climate"},
  "producer_session": "PRODUCER_SESSION",
  "revision": 1,
  "name": "Server Room Climate Controller",
  "area_id": "server_room",
  "preferences": {"voice_info": false, "voice_warning": true, "voice_error": true, "voice_repeat": false},
  "maintenance": false,
  "active_issues": [{"key": "temperature_high", "level": "WARNING", "message": "Rack temperature has reached the warning threshold."}]
}
```

Required successful service response:

```json
{"accepted": true, "protocol_version": 1, "center_session": "CENTER_SESSION", "lease_seconds": 180}
```

Registration atomically replaces metadata and active issues. Missing keys resolve previous issues and cancel their pending speech/reminders. Reduced level permissions or maintenance also cancel affected queued work. An unchanged snapshot is not a new event and must not reset reminder timing. Speech eligibility is rechecked immediately before delivery.

## home_control.publish_voice_event

Require the envelope, center_session, and all five event fields:

```json
{"key": "temperature_high", "level": "ERROR", "message": "The critical rack temperature alarm is active.", "active": true, "resolved": false}
```

- level is INFO, WARNING, or ERROR, case-sensitive.
- active/resolved are strict booleans and cannot both be true.
- active=true is allowed only for WARNING/ERROR and updates one source+key issue.
- resolved=true requires active=false. The message may be empty. It removes the issue and cancels its queue/reminders; resolution itself is silent. Resolving an absent key is harmless.
- active=false/resolved=false is a one-shot event with a nonempty message. It neither creates nor clears an issue.
- key/message limits remain 80/500 characters.

On recovery, SRCC sends explicit resolved first and, if INFO is enabled, one informational event using the same key. A recurring problem invalidates queued recovery INFO for that key. Repeated keys do not create independent queues.

Return accepted=true or accepted=false with a reason, such as registration_required. Additional response fields are permitted. Accepted means the event was accepted, not that speech occurred. Schema errors may be HA service errors but must not corrupt accepted state.

## home_control.unregister_voice_source

Require the envelope and center_session. Remove only the matching producer session and cancel its queued speech/reminders. A delayed unregister from a replaced session cannot remove a newer source. Retire the ended producer session. If unregister cannot be delivered, lease expiry provides cleanup.

## Discovery, lease, and retry

1. Subscribe to home_control_voice_ready before firing home_control_voice_discover with protocol_version=1. Ready includes protocol_version=1 and center_session. Readiness of services is not proof that speech is enabled.
2. Check all three services at startup as well, so a center already running need not emit another ready event. Center absence never stops equipment control.
3. SRCC calls services sequentially in a separate task, with a 3-second timeout. Registration renewal is every 60 s; lease is 180 s. These are transport constants, not reminder intervals, and do not poll equipment.
4. Only accepted registration permits publish. The center may accept state while speech is disabled; SRCC never enables it automatically.
5. After exceptions, rejection, or timeout, clear queued INFO and wait at least 60 s before retry. A new center-session ready may accelerate registration. Retry uses a new revision and current snapshot, not old events.
6. The contract permits an identical session/revision/body retry as a no-op: no repeated speech, state change, lease extension, or reminder reset. A different body at the same revision is rejected. Older revisions cannot roll state back. SRCC does not independently replay such operations.
7. Lease expiry removes registration/queues but does not retire the producer session. That live producer may re-register with a newer revision. Expired duplicate registration does not revive the lease. Publish does not renew it. Already delivered speech may finish; SRCC does not send a stop-speaker command.
8. New producer or center sessions restore active issues only from the current snapshot. Historical INFO and old speech attempts are not restored.
9. Maintenance suppresses new local INFO, clears pending local notices, and registers an empty maintenance snapshot. An in-flight service cannot be withdrawn; the next accepted snapshot cancels center queues. With the center unavailable, cancellation is bounded by lease expiry. Leaving maintenance reports current conditions, not events accumulated during maintenance.

Speaker queues, deduplication, TTL, quiet hours, volume, presence, alternate areas, priorities, and reminder intervals belong to Home-Control. Reminders apply only to active ERROR when both voice_error and voice_repeat are true. SRCC has no periodic speech-reminder timer.

## Receiver acceptance

Verify strict schemas, two producer sessions, stale center sessions, duplicate/revision collisions, lease expiry, disabled levels/center, routing, quiet hours, and queue cancellation at resolution. Speaker delivery must recheck source/permission/activity/routing. The mock receiver in tests/ha/test_voice.py verifies the SRCC client boundary; it is not the production center implementation.
