from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from email.utils import format_datetime
from functools import wraps
from urllib.parse import urlsplit

from django.http.response import HttpResponseBase

from drf_spectacular.utils import extend_schema

_METADATA_ATTRIBUTE = "__api_deprecation__"


def _parse_date(name, value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} deve usar YYYY-MM-DD") from exc


def _validate_uri(name, value):
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise ValueError(f"{name} deve ser uma URI válida")

    parsed = urlsplit(value)
    is_http = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    is_absolute_path = not parsed.scheme and not parsed.netloc and value.startswith("/") and not value.startswith("//")
    if not (is_http or is_absolute_path):
        raise ValueError(f"{name} deve ser HTTP(S) ou caminho absoluto")
    return value


@dataclass(frozen=True)
class ApiDeprecation:
    since: date
    sunset: date
    documentation: str
    replacement: str | None

    @classmethod
    def from_strings(cls, *, since, sunset, documentation, replacement):
        since_date = _parse_date("since", since)
        sunset_date = _parse_date("sunset", sunset)
        if sunset_date - since_date < timedelta(days=90):
            raise ValueError("sunset deve ficar pelo menos 90 dias depois de since")
        return cls(
            since=since_date,
            sunset=sunset_date,
            documentation=_validate_uri("documentation", documentation),
            replacement=_validate_uri("replacement", replacement) if replacement is not None else None,
        )

    @property
    def deprecation_header(self):
        instant = datetime.combine(self.since, time.min, tzinfo=UTC)
        return f"@{int(instant.timestamp())}"

    @property
    def sunset_header(self):
        instant = datetime.combine(self.sunset, time.min, tzinfo=UTC)
        return format_datetime(instant, usegmt=True)

    @property
    def documentation_link(self):
        return f'<{self.documentation}>; rel="deprecation"; type="text/html"'

    @property
    def openapi_extensions(self):
        extensions = {
            "x-deprecation-since": self.since.isoformat(),
            "x-sunset": self.sunset.isoformat(),
        }
        if self.replacement is not None:
            extensions["x-replacement"] = self.replacement
        return extensions

    def apply_headers(self, response):
        response["Deprecation"] = self.deprecation_header
        response["Sunset"] = self.sunset_header
        current_link = response.headers.get("Link")
        if current_link and self.documentation_link not in current_link:
            response["Link"] = f"{current_link}, {self.documentation_link}"
        elif not current_link:
            response["Link"] = self.documentation_link


def api_deprecated(*, since, sunset, documentation, replacement=None):
    metadata = ApiDeprecation.from_strings(
        since=since,
        sunset=sunset,
        documentation=documentation,
        replacement=replacement,
    )

    def decorator(handler):
        if hasattr(handler, _METADATA_ATTRIBUTE):
            raise ValueError("@api_deprecated não pode ser aplicado mais de uma vez")

        @wraps(handler)
        def wrapped(*args, **kwargs):
            response = handler(*args, **kwargs)
            if isinstance(response, HttpResponseBase):
                metadata.apply_headers(response)
            return response

        setattr(wrapped, _METADATA_ATTRIBUTE, metadata)
        return extend_schema(
            deprecated=True,
            external_docs={"url": metadata.documentation},
            extensions=metadata.openapi_extensions,
        )(wrapped)

    return decorator
