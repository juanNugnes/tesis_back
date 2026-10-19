"""Cliente de lectura: no evalua mensajes ni modifica el almacen Analytics."""

import json
from uuid import UUID

import httpx
from pydantic import ValidationError

from app.schemas.adult_message import AdultMessageHistory


class AdultMessagesError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int, retryable: bool):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


class AdultMessagesClient:
    def __init__(
        self,
        service_url: str,
        service_token: str,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.service_url = service_url.rstrip("/")
        self.service_token = service_token
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def history(self, profile_id: UUID) -> AdultMessageHistory:
        if not self.service_token:
            raise AdultMessagesError(
                "ADULT_MESSAGES_NOT_CONFIGURED", "Adult messages service is not configured",
                status_code=503, retryable=False,
            )
        endpoint = f"{self.service_url}/v1/adult-messages/profiles/{profile_id}/history"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self.transport, follow_redirects=False,
            ) as client:
                response = await client.get(
                    endpoint, headers={"Authorization": f"Bearer {self.service_token}"},
                )
        except httpx.TimeoutException:
            raise AdultMessagesError(
                "ADULT_MESSAGES_TIMEOUT", "Adult messages service timed out",
                status_code=504, retryable=True,
            ) from None
        except httpx.RequestError:
            raise AdultMessagesError(
                "ADULT_MESSAGES_UNAVAILABLE", "Adult messages service is unavailable",
                status_code=503, retryable=True,
            ) from None
        if response.status_code != 200:
            retryable = response.status_code >= 500 or response.status_code == 429
            raise AdultMessagesError(
                f"ADULT_MESSAGES_HTTP_{response.status_code}",
                "Adult messages service could not return history",
                status_code=503 if retryable else 502, retryable=retryable,
            )
        try:
            payload = json.loads(
                response.content.decode("utf-8"),
                parse_constant=_reject_constant, object_pairs_hook=_unique_object,
            )
            history = AdultMessageHistory.model_validate(payload)
            # Defensa adicional: nunca devolver tarjetas de otro perfil, aunque
            # provengan de una respuesta 200 del servicio interno.
            if any(item.card.profile_id != str(profile_id) for item in history.versions):
                raise ValueError("mismatched profile")
        except (UnicodeDecodeError, ValueError, ValidationError, RecursionError):
            raise AdultMessagesError(
                "ADULT_MESSAGES_INVALID_RESPONSE", "Adult messages service returned an invalid history",
                status_code=502, retryable=False,
            ) from None
        return history


def _reject_constant(_value):
    raise ValueError("non-finite JSON number")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
