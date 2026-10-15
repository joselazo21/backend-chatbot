from urllib.parse import urlsplit


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


def normalize_origin(value):
    try:
        parsed = urlsplit(value.strip())
        hostname = normalize_hostname(parsed.hostname or "")
        port = parsed.port
    except (ValueError, AttributeError) as error:
        raise ValueError("Origin must be an http(s) URL without path") from error

    if (
        parsed.scheme not in ("http", "https")
        or "@" in parsed.netloc
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Origin must be an http(s) URL without path")
    if port == 0:
        raise ValueError("Origin port must be positive")
    if (
        parsed.scheme == "http"
        and hostname not in ("localhost", "127.0.0.1")
        and not hostname.endswith(".localhost")
    ):
        raise ValueError("HTTP origins are only allowed for local development")

    default_port = 80 if parsed.scheme == "http" else 443
    port_suffix = f":{port}" if port and port != default_port else ""
    origin = f"{parsed.scheme}://{hostname}{port_suffix}"
    if len(origin) > 255:
        raise ValueError("Origin is too long")
    return origin
