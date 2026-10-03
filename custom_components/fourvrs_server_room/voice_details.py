"""Human-readable equipment roles and execution reasons; no control actions."""
ROLES = {
    "trm_temperature": "Основной датчик температуры стойки",
    "outdoor_temperature": "Датчик наружной температуры",
    "bolid_temperature_1": "Резервный датчик температуры № 1",
    "bolid_temperature_2": "Резервный датчик температуры № 2",
    "vent_enable": "Разрешение автоматической вентиляции",
    "vent_output": "Выход управления вентилятором",
    "climate": "Кондиционер", "ac_telemetry": "Телеметрия кондиционера",
    "ac_fault": "Сигнал неисправности кондиционера",
    "ups_online": "UPS: наличие внешнего питания",
    "ups_on_battery": "UPS: работа от батареи",
}
QUALITY = {"stale": "данные не обновляются", "unavailable": "сущность недоступна",
           "unknown": "состояние неизвестно", "invalid": "недостоверные данные",
           "unverified": "ожидаются подтверждённые данные"}
ACTIONS = {"ac_off": "выключение кондиционера", "ac_cool": "включение режима охлаждения",
           "ac_set_target": "установка температуры кондиционера", "fan_off": "выключение вентилятора",
           "fan_on": "включение вентилятора", "vent_disable": "запрет автоматической вентиляции",
           "vent_enable": "разрешение автоматической вентиляции"}
REASONS = {
    "AC_OFF_UNPROVEN": "Запуск вентиляции заблокирован: выключение кондиционера не подтверждено",
    "VENT_DISABLE_UNPROVEN": "Запуск кондиционера заблокирован: запрет автоматической вентиляции не подтверждён",
    "VENT_OUTPUT_UNPROVEN": "Запуск кондиционера заблокирован: состояние выхода вентилятора не подтверждено",
    "VENT_OUTPUT_TIMEOUT": "Не получено подтверждение выключения вентилятора",
    "TEMPERATURE_UNTRUSTED": "Управление заблокировано: нет достоверной температуры помещения или улицы",
    "INPUT_QUALITY": "Действие заблокировано: необходимые показания температуры недостоверны",
    "AC_HARD_MIN_BLOCK": "Запуск кондиционера запрещён: наружная температура достигла нижнего допустимого предела",
    "AC_REQUIRES_MAINS": "Запуск кондиционера запрещён: внешнее питание не подтверждено",
    "POWER_POLICY_REQUIRED": "Управление заблокировано: состояние питания или условия работы от UPS не определены",
    "AC_UNAVAILABLE": "Команда кондиционеру заблокирована: связь, исправность или эффективность не подтверждены",
    "VENT_UNAVAILABLE": "Вентиляция недоступна либо признана неэффективной",
    "OUTDOOR_NOT_USEFUL": "Вентиляция не выбрана: наружный воздух не подходит для охлаждения",
    "NORMAL_VENT_TEMPERATURE": "Штатная вентиляция запрещена при текущей наружной температуре",
    "NEGATIVE_TEMPERATURE_RESERVE_ONLY": "При отрицательной наружной температуре кондиционер разрешён только как резерв",
    "EMERGENCY_DEMAND_UNSET": "Не определены условия потребности в резервном охлаждении",
    "EMERGENCY_DELTA_UNSET": "Не настроена полезная разница температур для резервной вентиляции",
    "INITIAL_MODE_POLICY_REQUIRED": "Не определён начальный способ охлаждения",
    "TARGET_UPDATE_PROFILE_REQUIRED": "Не настроен способ изменения уставки работающего кондиционера",
    "READBACK_TIMEOUT": "Истекло время ожидания подтверждения команды",
    "HAIER_COMMAND_UNCONFIRMED": "Контроллер кондиционера не подтвердил выполнение команды",
    "HAIER_COMMAND_REJECTED": "Контроллер кондиционера отклонил команду",
    "HAIER_RESTART_DURING_COMMAND": "Контроллер кондиционера перезапустился во время команды",
    "HAIER_COMMAND_CONFLICT": "Получены конфликтующие подтверждения команд кондиционера",
    "HAIER_COMMAND_SUPERSEDED": "Команда кондиционеру вытеснена другой командой",
    "AC_COOL_UNSUPPORTED": "Сущность кондиционера не поддерживает режим охлаждения",
    "AC_TARGET_OUT_OF_RANGE": "Уставка вне допустимого диапазона кондиционера",
    "HAIER_RESULT_SUBSCRIPTION_MISSING": "Нет подписки на подтверждения команд кондиционера",
    "HAIER_TELEMETRY_UNTRUSTED": "Телеметрия кондиционера недостоверна",
    "HAIER_COMMAND_ALREADY_PENDING": "Кондиционер ещё выполняет другую команду",
    "NO_HEALTHY_COOLING": "Нет пригодного оборудования для охлаждения",
    "NO_COOLING_IS_NOT_AN_OFF_COMMAND": "Отсутствие охлаждения не разрешает команду выключения",
    "UNSUPPORTED_TARGET": "Выбранный способ охлаждения не поддерживается",
    "CLOCK_MOVED_BACKWARDS": "Нарушена последовательность времени исполнения",
    "TARGET_CHANGED_RECONCILE": "Требуется повторное согласование изменившегося режима",
    "MANUAL_WITH_UNRESOLVED_INTENT": "Управление выключено до подтверждения отправленной команды",
}


def label(runtime, role):
    entity = runtime.hass.states.get(runtime.data.get(role, ""))
    name = " ".join(str(entity.attributes.get("friendly_name", "")).split())[:60] if entity else ""
    base = ROLES.get(role, "Вход контроллера")
    return f"{base} «{name}»" if name else base


def reason_text(reason):
    if reason in REASONS:
        return REASONS[reason]
    if reason.startswith("SERVICE_UNAVAILABLE:"):
        return "Не доступен сервис управления оборудованием"
    return "Причина не распознана; подробности в диагностике исполнения"


def source_text(runtime):
    selection = runtime.status.temperature
    if selection.reason == "TRM_PRIMARY":
        return label(runtime, "trm_temperature")
    if selection.reason == "BOLID_MEAN_FALLBACK":
        return "среднее показаний: " + "; ".join(label(runtime, r) for r in ("bolid_temperature_1", "bolid_temperature_2"))
    if selection.sample:
        for role in ("bolid_temperature_1", "bolid_temperature_2"):
            if runtime.data.get(role) == selection.sample.source:
                return label(runtime, role)
    return "нет пригодного температурного источника"
