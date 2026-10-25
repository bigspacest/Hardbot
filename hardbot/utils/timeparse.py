import re

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_PART = re.compile(r"(\d+)([smhdw])")


def parse_duration(text):
    """'30s', '10m', '2h', '1d', '1w' o combinados ('1h30m') -> segundos. None si no es válido."""
    text = (text or "").strip().lower().replace(" ", "")
    if not text:
        return None
    pos = total = 0
    for m in _PART.finditer(text):
        if m.start() != pos:
            return None
        total += int(m.group(1)) * _UNITS[m.group(2)]
        pos = m.end()
    if pos != len(text) or total <= 0:
        return None
    return total


def format_duration(seconds):
    parts = []
    for name, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        qty, seconds = divmod(int(seconds), size)
        if qty:
            parts.append(f"{qty}{name}")
    return " ".join(parts) or "0s"
