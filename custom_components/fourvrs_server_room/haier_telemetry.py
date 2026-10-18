"""Haier schema-1 observations and conservative, serialized command evidence.

ESP uptime and local receipt time give a lower bound on source age, not a
network-delay guarantee. Heartbeats never create a new UART measurement.
"""
from dataclasses import dataclass
from math import isfinite
import re

from .core.models import Quality, Sample

MODES = {"OFF": "off", "COOL": "cool", "HEAT": "heat", "DRY": "dry",
         "FAN_ONLY": "fan_only", "HEAT_COOL": "auto", "AUTO": "auto"}


def integer(value):
    return type(value) is int and 0 <= value <= 2**64 - 1


def number(value):
    return type(value) in (int, float) and isfinite(value)


@dataclass(frozen=True)
class Frame:
    boot: str
    count: int
    sample_ms: int
    published_ms: int
    available: bool
    mode: str | None
    target: float | None
    fault_valid: bool
    fault: bool | None
    fault_code: int | None
    request_id: str
    command_state: str
    current_temperature: float | None = None

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1:
            raise ValueError("UNSUPPORTED_SCHEMA")
        boot = data.get("boot_id")
        if not isinstance(boot, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", boot):
            raise ValueError("INVALID_BOOT")
        times = [data.get(k) for k in ("status_frames", "sample_uptime_ms", "published_uptime_ms")]
        if not all(integer(v) for v in times) or times[1] > times[2]:
            raise ValueError("INVALID_CLOCK")
        count, sample, published = times
        age = data.get("age_ms")
        if count == 0:
            if age is not None or sample != 0:
                raise ValueError("INVALID_INITIAL_SAMPLE")
        elif not integer(age) or abs(age - (published - sample)) > 1000:
            # Firmware builds the two clock fields in adjacent operations.
            raise ValueError("INCONSISTENT_AGE")
        available, valid, fault, code = (data.get(k) for k in ("available", "fault_valid", "fault", "fault_code"))
        if type(available) is not bool or type(valid) is not bool:
            raise ValueError("INVALID_FLAGS")
        if valid:
            if not available or count == 0 or type(fault) is not bool or type(code) is not int or not 0 <= code <= 255 or fault != (code != 0):
                raise ValueError("INVALID_FAULT")
        elif fault is not None or code is not None:
            raise ValueError("UNTRUSTED_FAULT")
        mode, target = data.get("mode"), data.get("target_temperature")
        if available:
            if count == 0 or not isinstance(mode, str) or mode not in MODES or not number(target):
                raise ValueError("INVALID_STATE")
            mode, target = MODES[mode], float(target)
        else:
            mode, target = None, None
        request, command = data.get("request_id"), data.get("command_state")
        if not isinstance(request, str) or len(request) > 80 or not isinstance(command, str) or len(command) > 40:
            raise ValueError("INVALID_COMMAND_STATE")
        current = data.get("current_temperature")
        current = float(current) if available and number(current) else None
        return cls(boot.lower(), count, sample, published, available, mode, target, valid, fault, code, request, command, current)


class HaierTelemetry:
    HEARTBEAT_LIMIT = 15.0
    SOURCE_LIMIT = 30.0

    def __init__(self):
        self.frame = None
        self.received_at = None
        self.measured_at = None
        self.anchor = None
        self.invalid_reason = "WAIT_LIVE_TELEMETRY"
        self.retired_boots = set()
        self.command = None
        self.command_error = None
        self.command_error_detail = None
        self.last_result_at = None

    def fail_command(self, reason, detail):
        # Keep the first cause; later telemetry must not hide the original race.
        if self.command_error is None:
            self.command_error = reason
            self.command_error_detail = detail

    def invalidate(self, reason):
        self.invalid_reason = reason

    def receive(self, data, now):
        try:
            frame = Frame.parse(data)
            old = self.frame
            if old and frame.boot == old.boot:
                if frame.published_ms <= old.published_ms or frame.count < old.count or frame.sample_ms < old.sample_ms:
                    raise ValueError("OUT_OF_ORDER_TELEMETRY")
                if (frame.count == old.count) != (frame.sample_ms == old.sample_ms):
                    raise ValueError("INCONSISTENT_SAMPLE_ID")
                if frame.count == old.count and frame.available and old.available and (frame.mode, frame.target, frame.current_temperature) != (old.mode, old.target, old.current_temperature):
                    raise ValueError("STATE_CHANGED_WITHOUT_SAMPLE")
                if frame.count == old.count and frame.fault_valid and old.fault_valid and (frame.fault, frame.fault_code) != (old.fault, old.fault_code):
                    raise ValueError("FAULT_CHANGED_WITHOUT_SAMPLE")
            elif old:
                if frame.boot in self.retired_boots:
                    raise ValueError("RETIRED_BOOT")
                if len(self.retired_boots) >= 64:
                    raise ValueError("SESSION_LIMIT_RELOAD_REQUIRED")
                self.retired_boots.add(old.boot)
                self.anchor = None
                if self.command:
                    self.fail_command("HAIER_RESTART_DURING_COMMAND", "boot_changed")
            candidate = now - frame.published_ms / 1000
            self.anchor = candidate if self.anchor is None else min(self.anchor, candidate)
            measured = self.anchor + frame.sample_ms / 1000
            if old and old.boot == frame.boot and old.count == frame.count:
                measured = min(self.measured_at, measured)
            self.frame, self.received_at, self.measured_at = frame, now, measured
            self.invalid_reason = None
            if self.command and self.command.get("request_id"):
                rid = self.command["request_id"]
                if frame.request_id not in (rid, self.command["baseline_request"]):
                    self.fail_command("HAIER_COMMAND_SUPERSEDED", "telemetry_request_changed")
                if frame.request_id == rid and frame.command_state == "timeout_unconfirmed":
                    self.fail_command("HAIER_COMMAND_UNCONFIRMED", "firmware_timeout")
            return True
        except (ValueError, TypeError, OverflowError) as error:
            self.invalidate(str(error))
            return False

    def quality(self, now):
        if self.invalid_reason or self.frame is None:
            return Quality.UNVERIFIED if self.invalid_reason == "WAIT_LIVE_TELEMETRY" else Quality.INVALID
        if now < self.received_at or now < self.measured_at:
            return Quality.INVALID
        if now - self.received_at >= self.HEARTBEAT_LIMIT:
            return Quality.STALE
        if not self.frame.available:
            return Quality.UNAVAILABLE
        if now - self.measured_at >= self.SOURCE_LIMIT:
            return Quality.STALE
        return Quality.VALID

    def temperature_sample(self, now):
        value = self.frame.current_temperature if self.frame else None
        quality = self.quality(now) if value is not None else Quality.UNKNOWN
        return Sample(value, "haier_telemetry", self.measured_at or 0.0, quality, self.SOURCE_LIMIT)

    def samples(self, now):
        quality = self.quality(now)
        frame = self.frame
        stamp = self.measured_at if self.measured_at is not None else now
        mode = Sample(frame.mode if frame else None, "haier_telemetry", stamp, quality, self.SOURCE_LIMIT)
        target = Sample(frame.target if frame else None, "haier_telemetry", stamp, quality, self.SOURCE_LIMIT)
        fault_quality = quality if frame and frame.fault_valid else Quality.UNKNOWN
        fault = Sample(frame.fault if frame and frame.fault_valid else None, "haier_telemetry", stamp, fault_quality, self.SOURCE_LIMIT)
        return mode, target, fault

    def arm(self, action, now):
        if self.quality(now) is not Quality.VALID or not self.frame.fault_valid:
            raise ValueError("HAIER_TELEMETRY_UNTRUSTED")
        if self.frame.command_state == "pending":
            raise ValueError("HAIER_COMMAND_ALREADY_PENDING")
        self.command_error = None
        self.command_error_detail = None
        self.command = dict(action=action, boot=self.frame.boot, count=self.frame.count,
                            published_ms=self.frame.published_ms, sent_at=now,
                            baseline_request=self.frame.request_id, request_id=None, confirmed=False)

    def result(self, payload, now, retained=False):
        if retained or not isinstance(payload, dict):
            return
        if payload.get("status") in ("accepted", "confirmed", "rejected", "timeout_unconfirmed"):
            self.last_result_at = now
        if not self.command:
            return
        command = self.command
        if now < command["sent_at"]:
            return
        status, rid = payload.get("status"), payload.get("request_id")
        expected = "target" if command["action"] == "ac_set_target" else "hvac_mode"
        if status == "rejected":
            self.fail_command("HAIER_COMMAND_REJECTED", "firmware_rejected")
        elif status == "accepted":
            if payload.get("field") != expected:
                self.fail_command("HAIER_COMMAND_CONFLICT", "unexpected_field")
            elif not isinstance(rid, str) or not rid or len(rid) > 80:
                self.fail_command("HAIER_COMMAND_CONFLICT", "invalid_request_id")
            elif rid == command["baseline_request"]:
                self.fail_command("HAIER_COMMAND_CONFLICT", "baseline_request_repeated")
            elif command["request_id"] and rid != command["request_id"]:
                self.fail_command("HAIER_COMMAND_CONFLICT", "second_request_accepted")
            else:
                command["request_id"] = rid
        elif command["request_id"] and rid == command["request_id"]:
            if status == "confirmed":
                command["confirmed"] = True
            elif status == "timeout_unconfirmed":
                self.fail_command("HAIER_COMMAND_UNCONFIRMED", "firmware_timeout")

    def confirmed(self, now):
        c, f = self.command, self.frame
        return bool(c and f and not self.command_error and self.quality(now) is Quality.VALID
                    and f.fault_valid and f.boot == c["boot"] and f.count > c["count"]
                    and f.sample_ms > c["published_ms"] and self.measured_at > c["sent_at"]
                    and c["confirmed"] and c["request_id"] == f.request_id
                    and f.command_state == "confirmed")

    def diagnostics(self, now):
        f = self.frame
        return {"profile": "haier_schema_1", "quality": self.quality(now).value,
                "reason": self.invalid_reason,
                "heartbeat_age_s": round(now-self.received_at, 3) if self.received_at is not None else None,
                "source_age_lower_bound_s": round(now-self.measured_at, 3) if self.measured_at is not None else None,
                "status_frames": f.count if f else None,
                "fault_valid": f.fault_valid if f else False,
                "fault": f.fault if f else None, "fault_code": f.fault_code if f else None,
                "command_state": f.command_state if f else None,
                "command_error": self.command_error,
                "command_error_detail": self.command_error_detail,
                "command_accepted": bool(self.command and self.command.get("request_id")),
                "result_confirmed": bool(self.command and self.command.get("confirmed"))}
