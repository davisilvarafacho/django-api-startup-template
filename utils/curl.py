import logging
import shlex
from pathlib import Path
from uuid import uuid4

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def as_curl(response, *, save_to_file=False) -> str:
    request = response.request
    command = ["curl", "-X", request.method]
    for name, value in request.headers.items():
        command.extend(["-H", f"{name}: {value}"])
    if request.body is not None:
        body = request.body.decode() if isinstance(request.body, bytes) else request.body
        command.extend(["--data-raw", body])
    command.append(request.url)
    result = " ".join(shlex.quote(str(part)) for part in command)
    if save_to_file:
        output = PROJECT_ROOT / f"{uuid4()}.curl"
        output.write_text(result)
        LOGGER.info("Comando curl salvo em %s", output)
    return result
