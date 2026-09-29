"""Semantic process observations; no transports, thresholds or hardware actions."""
from .core.models import Mode, Power


def issue(level, message):
    return {"level": level, "message": message}


class VoiceProjection:
    def __init__(self):
        self.issues = {}
        self.mode = None
        self.command_sequence = None
        self.initialized = False

    def update(self, runtime):
        status, state, executor = runtime.status, runtime.snapshot, runtime.executor
        if status is None or state is None:
            return {}, []
        old = self.issues
        issues = {}
        if status.alarm.thermal_critical:
            issues["temperature_high"] = issue("ERROR", "Контроллер сообщает о критической температуре серверной.")
        elif status.temperature.sample is not None:
            if status.alarm.reason == "TEMPERATURE_WARNING":
                issues["temperature_high"] = issue("WARNING", "Температура серверной достигла предупредительного порога.")
        elif "temperature_high" in old:
            # Lost measurement is not proof of thermal recovery.
            issues["temperature_high"] = old["temperature_high"]
        if any(item["quality"] != "valid" for item in runtime.input_status.values()) or status.degraded:
            issues["input_quality"] = issue("WARNING", "Часть данных контроллера охлаждения недостоверна или используется резервный датчик.")
        if state.ac_fault is True:
            issues["ac_fault"] = issue("ERROR", "Кондиционер сообщает о неисправности.")
        elif state.ac_fault is None and "ac_fault" in old:
            issues["ac_fault"] = old["ac_fault"]
        if runtime.vent_failed:
            issues["ventilation_ineffective"] = issue("ERROR", "Контроллер признал вентиляцию неэффективной по температурному тренду.")
        if executor.error:
            issues["execution_failure"] = issue("ERROR", "Контроллер сообщает об ошибке выполнения команды. Проверьте диагностику.")
        if runtime.reported_mode == "conflict":
            issues["equipment_conflict"] = issue("ERROR", "Обратная связь оборудования противоречит взаимному исключению охлаждения и вентиляции.")
        elif runtime.reported_mode == "unknown" and "equipment_conflict" in old:
            issues["equipment_conflict"] = old["equipment_conflict"]
        if state.power is not Power.MAINS:
            issues["power_supply"] = issue("WARNING", "Серверная работает от батареи или состояние внешнего питания не подтверждено.")
        if status.decision.desired is Mode.NO_COOLING_AVAILABLE:
            issues["cooling_unavailable"] = issue("WARNING", "Контроллер не нашёл пригодного способа охлаждения.")
        elif status.decision.desired is None and "cooling_unavailable" in old:
            issues["cooling_unavailable"] = old["cooling_unavailable"]
        if executor.enabled and executor.step.phase in (
                "WAIT_TIMING", "WAIT_AC_MINIMUM_OFF", "WAIT_HAIER_COMMAND", "BLOCKED"):
            issues["action_deferred"] = issue("WARNING", "Действие охлаждения отложено: действует выдержка или блокировка контроллера.")

        notices = []
        if self.initialized:
            for key in old.keys() - issues.keys():
                if key != "action_deferred":
                    notices.append(dict(key=key, level="INFO",
                                        message={
                                            "temperature_high": "Температурная тревога снята контроллером.",
                                            "input_quality": "Достоверность данных контроллера восстановлена.",
                                            "ac_fault": "Кондиционер больше не сообщает об ошибке.",
                                            "ventilation_ineffective": "Признак неэффективности вентиляции сброшен контроллером.",
                                            "execution_failure": "Ошибка исполнения сброшена контроллером. Это не подтверждение ремонта оборудования.",
                                            "equipment_conflict": "Конфликт обратной связи оборудования снят.",
                                            "power_supply": "Внешнее питание серверной подтверждено.",
                                            "cooling_unavailable": "Контроллер больше не сообщает об отсутствии пригодного охлаждения.",
                                        }[key], active=False, resolved=False))
        mode = runtime.reported_mode
        command = executor.last_command or {}
        complete_command = command.get("status") == "confirmed" and executor.step.complete
        new_command = complete_command and command.get("sequence") != self.command_sequence
        if (executor.step.complete and executor.target is Mode.EMERGENCY_VENTILATION
                and state.proved(state.vent_output, True, runtime.data["max_age"])):
            mode = "emergency_ventilation"
        if executor.step.complete and mode in ("air_conditioning", "ventilation", "emergency_ventilation", "idle"):
            if self.initialized and new_command and (mode != self.mode or command.get("action") == "ac_cool"):
                notices.append(dict(key="mode_changed", level="INFO", active=False, resolved=False,
                                    message={"air_conditioning": "Подтверждён режим кондиционирования серверной.",
                                             "ventilation": "Подтверждён режим автоматической вентиляции серверной.",
                                             "emergency_ventilation": "Подтверждён режим резервной вентиляции серверной.",
                                             "idle": "Подтверждено отключение активного охлаждения."}[mode]))
            if self.mode is None or new_command:
                self.mode = mode
        if complete_command:
            self.command_sequence = command.get("sequence")
        self.initialized = True
        self.issues = issues
        return issues, notices
