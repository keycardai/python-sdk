from re import fullmatch


def is_valid_zone_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", value)
        is not None
    )
