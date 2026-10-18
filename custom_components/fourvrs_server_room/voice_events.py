"""Semantic process observations; no transports, thresholds or hardware actions."""
from .core.models import Mode, Power
from .voice_details import QUALITY, ACTIONS, label, reason_text, source_text


def issue(level, message):
    return {"level": level, "message": ("Климат серверной. " + message)[:500]}


class VoiceProjection:
    def __init__(self):
        self.issues = {}
        self.mode = None
        self.command_sequence = None
        self.initialized = False
        self.pending_inputs = {}
        self.recovery_inputs = {}
        self.wait_signature = None

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
        if getattr(getattr(runtime, "observer", None), "reserve_hold", False):
            issues["vent_retry_locked"] = issue("WARNING", "После перегрева или роста температуры на вентиляции автоматический возврат на неё заблокирован. При доступности используется кондиционер. Повторная проверка вентиляции разрешается вручную.")
        recovery_messages = {}
        if getattr(getattr(runtime, "adaptation", None), "limit_reported", False):
            issues["adaptation_limit"] = issue("WARNING", "Уставка кондиционера достигла нижнего предела. Дальнейшее автоматическое снижение невозможно.")
        now = state.now
        selected = status.temperature
        for role, item in runtime.input_status.items():
            if runtime.haier and role in ("climate", "ac_fault"):
                continue  # These roles share the same UART snapshot.
            key = "input_" + role
            bad = item["quality"] != "valid"
            if bad:
                self.recovery_inputs.pop(key, None)
                since = self.pending_inputs.setdefault(key, now)
                # Only unused backup loss is delayed; control-affecting loss is immediate.
                unused_backup = role.startswith("bolid_temperature") and selected.reason == "TRM_PRIMARY"
                if unused_backup and now - since < 20 and key not in old:
                    continue
                message = label(runtime, role) + ": " + QUALITY.get(item["quality"], "данные непригодны") + "."
                if role == "trm_temperature":
                    message += (" Для управления используется " + source_text(runtime) + "."
                                if selected.sample else " Нет пригодного резерва температуры стойки.")
                elif unused_backup:
                    message += " Основной датчик используется; температурный резерв уменьшен."
                elif role in ("ups_online", "ups_on_battery"):
                    message += " Достоверность питания оценивается по обоим сигналам UPS."
                level = "ERROR" if role == "trm_temperature" and selected.sample is None and executor.enabled else "WARNING"
                issues[key] = issue(level, message)
            else:
                self.pending_inputs.pop(key, None)
                if key in old:
                    since = self.recovery_inputs.setdefault(key, now)
                    if now - since < 20:
                        issues[key] = old[key]
                    recovery_messages[key] = label(runtime, role) + ": поступление достоверных данных восстановлено."
        if selected.degraded and selected.sample:
            issues["temperature_source"] = issue("WARNING", "Управление использует резерв температуры: " + source_text(runtime) + ".")
            # The source event includes the primary failure, avoiding two speeches.
            primary = issues.pop("input_trm_temperature", None)
            if primary:
                issues["temperature_source"] = issue("WARNING", primary["message"].removeprefix("Климат серверной. "))
        if selected.sample is None:
            issues["temperature_source"] = issue("ERROR" if executor.enabled else "WARNING",
                "Нет пригодных показаний температуры стойки. " +
                ("Автоматические действия охлаждения заблокированы." if executor.enabled else "Автоматика выключена; температурный контроль недоступен."))
            issues.pop("input_trm_temperature", None)
        recovery_messages["temperature_source"] = "Температурный контроль использует " + source_text(runtime) + "."
        if "temperature_source" in issues:
            self.recovery_inputs.pop("temperature_source", None)
        elif "temperature_source" in old:
            since = self.recovery_inputs.setdefault("temperature_source", now)
            if now - since < 20:
                issues["temperature_source"] = old["temperature_source"]
        if state.ac_fault is True:
            frame = getattr(runtime.haier, "frame", None)
            fault_code = getattr(frame, "fault_code", None)
            suffix = f" Код ошибки {fault_code}." if type(fault_code) is int else ""
            issues["ac_fault"] = issue("ERROR", label(runtime, "climate") + " сообщает о неисправности." + suffix)
        elif state.ac_fault is None and "ac_fault" in old:
            issues["ac_fault"] = old["ac_fault"]
        if runtime.vent_failed:
            issues["ventilation_ineffective"] = issue("ERROR", "Контроллер признал вентиляцию неэффективной по температурному тренду.")
        if executor.error:
            action = (executor.last_command or {}).get("action")
            issues["execution_failure"] = issue("ERROR", reason_text(executor.error) + ". Действие: " +
                ACTIONS.get(action, "согласование оборудования") + ". Дальнейшее исполнение остановлено; проверьте диагностику.")
        if runtime.reported_mode == "conflict":
            issues["equipment_conflict"] = issue("ERROR", "Обратная связь оборудования противоречит взаимному исключению охлаждения и вентиляции.")
        elif runtime.reported_mode == "unknown" and "equipment_conflict" in old:
            issues["equipment_conflict"] = old["equipment_conflict"]
        if state.power is not Power.MAINS:
            power_message = {
                Power.BATTERY: "UPS сообщает о работе от батареи. Кондиционирование требует внешнего питания.",
                Power.CONFLICT: "Сигналы UPS о внешнем питании и работе от батареи противоречат друг другу. Запуск кондиционера запрещён.",
                Power.UNKNOWN: "Состояние питания UPS не подтверждено. Запуск кондиционера запрещён.",
            }[state.power]
            details = [label(runtime, role) + ": " + QUALITY.get(item["quality"], "данные непригодны")
                       for role, item in runtime.input_status.items()
                       if role in ("ups_online", "ups_on_battery") and item["quality"] != "valid"]
            issues["power_supply"] = issue("WARNING", power_message + " Источники: " + label(runtime, "ups_online") + "; " + label(runtime, "ups_on_battery") + "." + (" " + "; ".join(details) + "." if details else ""))
            for role in ("ups_online", "ups_on_battery"):
                issues.pop("input_" + role, None)
        if status.decision.desired is Mode.NO_COOLING_AVAILABLE:
            issues["cooling_unavailable"] = issue("ERROR" if status.decision.urgent else "WARNING", "Нет пригодного способа охлаждения. " + reason_text(status.decision.reason) + ".")
        elif status.decision.desired is None and "cooling_unavailable" in old:
            issues["cooling_unavailable"] = old["cooling_unavailable"]
        phase, reason = executor.step.phase, executor.step.reason
        if executor.enabled and not executor.error and phase in ("WAIT_HAIER_COMMAND", "BLOCKED", "WAIT_VENT_OUTPUT_OFF"):
            text = ("Конкурирующая команда кондиционеру: коррекция отложена до завершения команды и проверки телеметрии."
                    if reason == "COMMAND_CONFLICT_RECOVERY" else
                    "Кондиционер выполняет внешнюю команду. Автоматическая коррекция ожидает её завершения."
                    if phase == "WAIT_HAIER_COMMAND" else
                    "Запуск кондиционера ожидает подтверждения выключения вентилятора."
                    if phase == "WAIT_VENT_OUTPUT_OFF" else reason_text(reason) + ".")
            # Supply and total temperature loss already describe their blocker.
            if not ((reason in ("POWER_POLICY_REQUIRED", "AC_REQUIRES_MAINS") and "power_supply" in issues)
                    or (reason in ("TEMPERATURE_UNTRUSTED", "INPUT_QUALITY") and selected.sample is None)):
                issues["action_deferred"] = issue("ERROR" if status.decision.urgent else "WARNING", text)
        if "action_deferred" in issues:
            roles = {
                "AC_OFF_UNPROVEN": ("climate", "ac_telemetry"),
                "AC_UNAVAILABLE": ("climate", "ac_telemetry", "ac_fault"),
                "VENT_DISABLE_UNPROVEN": ("vent_enable",),
                "VENT_OUTPUT_UNPROVEN": ("vent_output",),
                "VENT_UNAVAILABLE": ("vent_enable", "vent_output"),
                "TEMPERATURE_UNTRUSTED": ("trm_temperature", "outdoor_temperature"),
                "INPUT_QUALITY": ("trm_temperature", "outdoor_temperature"),
            }.get(reason, ())
            detail = []
            for role in roles:
                removed = issues.pop("input_" + role, None)
                if removed:
                    detail.append(removed["message"].removeprefix("Климат серверной. "))
            if detail:
                value = issues["action_deferred"]
                value["message"] = (value["message"] + " " + " ".join(detail))[:500]
            if "cooling_unavailable" in issues:
                issues.pop("action_deferred")
            elif "ac_fault" in issues and reason == "AC_UNAVAILABLE":
                issues.pop("action_deferred")
                issues["ac_fault"]["message"] += " Автоматический запуск кондиционера запрещён."
        recovery_messages["action_deferred"] = ("Автоматическое управление выключено. Ожидавшее действие отменено." if not executor.enabled else "Ранее сообщённая блокировка или ожидание завершены. Это не подтверждение запуска оборудования.")
        notices = []
        if self.initialized:
            for key in old.keys() - issues.keys():
                if key.startswith("input_") and key not in recovery_messages:
                    continue  # Grouping/reclassification is not physical recovery.
                if key == "action_deferred" and (executor.error or phase == "BLOCKED"):
                    continue
                if key not in issues:
                    notices.append(dict(key=key, level="INFO",
                                        message=recovery_messages.get(key) or {
                                            "temperature_high": "Температурная тревога снята контроллером.",
                                            "input_quality": "Достоверность данных контроллера восстановлена.",
                                            "ac_fault": "Кондиционер больше не сообщает об ошибке.",
                                            "ventilation_ineffective": "Признак неэффективности вентиляции сброшен контроллером.",
                                            "execution_failure": "Ошибка исполнения сброшена контроллером. Это не подтверждение ремонта оборудования.",
                                            "equipment_conflict": "Конфликт обратной связи оборудования снят.",
                                            "power_supply": "Внешнее питание серверной подтверждено.",
                                            "cooling_unavailable": "Контроллер больше не сообщает об отсутствии пригодного охлаждения.",
                                        }.get(key, "Условие предупреждения больше не действует."), active=False, resolved=False))
        wait_signature = (phase, executor.target) if executor.enabled and phase == "WAIT_TIMING" else None
        if self.initialized and wait_signature and wait_signature != self.wait_signature:
            notices.append(dict(key="normal_wait", level="INFO", active=False, resolved=False,
                message="Перед сменой способа охлаждения выдерживается время устойчивости условий."))
        self.wait_signature = wait_signature
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
        if self.initialized and new_command and command.get("action") == "ac_set_target":
            notices.append(dict(key="target_restored", level="INFO", active=False, resolved=False,
                message=(f"Из-за предупредительной температуры стойки уставка кондиционера снижена с {command['adapted_from']:g} до {command['target']:g} градусов и сохранена в настройках."
                         if "adapted_from" in command else
                         f"Подтверждена уставка кондиционера {command.get('target', runtime.data['ac_target']):g} градусов.")))
        if complete_command:
            self.command_sequence = command.get("sequence")
        self.initialized = True
        self.issues = issues
        for notice in notices:
            notice["message"] = ("Климат серверной. " + notice["message"])[:500]
        return issues, notices
