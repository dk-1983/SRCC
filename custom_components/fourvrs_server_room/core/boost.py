"""One-shot emergency cooling request, within the appliance setpoint grid."""
from math import floor, isfinite


def emergency_target(normal, measured, minimum, maximum, step):
    values = (normal, measured, minimum, maximum, step)
    if any(type(v) not in (int, float) or not isfinite(v) for v in values):
        return None
    if step <= 0 or minimum > maximum or not minimum <= normal <= maximum:
        return None
    desired = min(normal, measured - max(1.0, step))
    result = minimum + max(0, floor((desired - minimum) / step + 1e-9)) * step
    return round(min(normal, maximum, result), 6)
