"""Run: python -m examples.temperature_recovery"""
from custom_components.fourvrs_server_room.core.models import Config, Sample
from custom_components.fourvrs_server_room.core.severity import TemperatureAlarm


def main():
    alarm = TemperatureAlarm(Config(), max_age=60, max_gap=60)
    print("seconds  temperature  severity  thermal_critical")
    for seconds, value in ((0, 25), (60, 26), (120, 28), (180, 27), (240, 26.9), (300, 26.8)):
        result = alarm.update(Sample(value, "simulated_trm", seconds), now=seconds)
        print(f"{seconds:7}  {value:11.1f}  {result.severity.value:8}  {result.thermal_critical}")


if __name__ == "__main__":
    main()
