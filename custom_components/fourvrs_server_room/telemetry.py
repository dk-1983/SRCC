"""Bounded, role-based diagnostic events; never copy raw HA attributes."""
from collections import deque
from datetime import datetime, timezone
import logging

_LOGGER = logging.getLogger(__name__)


class Telemetry:
    def __init__(self):
        self.events = deque(maxlen=100)
        self.sequence = 0
        self.inputs = {}
        self.decision = None
        self.execution = None

    def record(self, event, *, warning=False, **details):
        self.sequence += 1
        item = {"sequence": self.sequence, "time": datetime.now(timezone.utc).isoformat(),
                "event": event, **details}
        self.events.append(item)
        log = _LOGGER.warning if warning else _LOGGER.info
        log("SRCC %s #%s %s", event, self.sequence, details)
        return item

    def observe(self, runtime):
        for role, item in runtime.input_status.items():
            quality = item["quality"]
            previous = self.inputs.get(role)
            if previous != quality:
                self.inputs[role] = quality
                self.record("input_quality", warning=quality != "valid", role=role,
                            previous=previous, **item)
        status = runtime.status
        signature = (status.decision.reason, runtime.reported_mode,
                     status.decision.desired.value if status.decision.desired else None,
                     runtime.snapshot.power.value, status.alarm.severity.value)
        if signature != self.decision:
            self.decision = signature
            self.record("decision", reason=signature[0], reported_mode=signature[1],
                        desired_mode=signature[2], power=signature[3], severity=signature[4],
                        input_issues={key: value for key, value in runtime.input_status.items()
                                      if value["quality"] != "valid"})

    def execution_changed(self, executor):
        signature = (executor.step.phase, executor.step.reason, bool(executor.error))
        if signature != self.execution:
            self.execution = signature
            self.record("execution", warning=bool(executor.error), phase=signature[0],
                        reason=(executor.error if executor.error and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789" for c in executor.error)
                                else "EXECUTION_ERROR") if executor.error else signature[1],
                        pending_action=executor.planner.pending.value
                        if executor.planner and executor.planner.pending else None,
                        wait=dict(executor.wait_details))
