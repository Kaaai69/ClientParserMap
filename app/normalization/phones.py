import re

import phonenumbers
from phonenumbers import PhoneNumberFormat

DIGITS_RE = re.compile(r"\D+")


def normalize_phone(value: str, default_region: str = "RU") -> str | None:
    raw = value.strip()
    if not raw:
        return None

    digits = DIGITS_RE.sub("", raw)
    if default_region.upper() == "RU":
        if len(digits) == 11 and digits.startswith("8"):
            raw = "+7" + digits[1:]
        elif len(digits) == 11 and digits.startswith("7"):
            raw = "+" + digits
        elif len(digits) == 10:
            raw = "+7" + digits

    try:
        parsed = phonenumbers.parse(raw, default_region.upper())
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_possible_number(parsed) or not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
