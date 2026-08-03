import shlex


def as_curl(response, *, save_to_file=False) -> str:
    request = response.request
    command = ["curl", "-X", request.method]
    for name, value in request.headers.items():
        command.extend(["-H", f"{name}: {value}"])
    if request.body is not None:
        body = request.body.decode() if isinstance(request.body, bytes) else request.body
        command.extend(["--data-raw", body])
    command.append(request.url)
    return " ".join(shlex.quote(str(part)) for part in command)
