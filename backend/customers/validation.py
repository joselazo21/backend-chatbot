def normalize_hostname(value):
    hostname = value.strip().lower()
    labels = hostname.split(".")
    if len(hostname) > 253 or not labels:
        raise ValueError("Domain must be a hostname without scheme or port")
    for label in labels:
        if (
            not 1 <= len(label) <= 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(character.isascii() and (character.isalnum() or character == "-") for character in label)
        ):
            raise ValueError("Domain must be a hostname without scheme or port")
    return hostname
