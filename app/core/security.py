import base64
import binascii
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

JWT_ALGORITHM = "HS256"
ADULT_ACCESS_TOKEN_TYPE = "adult_access"
DEVICE_ACCESS_TOKEN_TYPE = "device_access"


class InvalidAccessTokenError(Exception):
    pass


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


class AccessTokenCodec:
    def __init__(self, secret: str, expiration_minutes: int, token_type: str):
        self.secret = secret.encode("utf-8")
        self.expiration = timedelta(minutes=expiration_minutes)
        self.token_type = token_type

    def issue(
        self,
        subject_id: uuid.UUID,
        *,
        now: datetime | None = None,
        expiration: timedelta | None = None,
    ) -> tuple[str, int]:
        issued_at = now or datetime.now(timezone.utc)
        expires_at = issued_at + (expiration if expiration is not None else self.expiration)
        header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
        payload = {
            "sub": str(subject_id),
            "type": self.token_type,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        encoded_header = self._encode_json(header)
        encoded_payload = self._encode_json(payload)
        signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
        signature = hmac.new(self.secret, signing_input, hashlib.sha256).digest()
        token = f"{encoded_header}.{encoded_payload}.{_base64url_encode(signature)}"
        return token, max(0, int((expires_at - issued_at).total_seconds()))

    def decode_subject(self, token: str, *, now: datetime | None = None) -> uuid.UUID:
        try:
            encoded_header, encoded_payload, encoded_signature = token.split(".")
            signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
            expected_signature = hmac.new(self.secret, signing_input, hashlib.sha256).digest()
            supplied_signature = _base64url_decode(encoded_signature)
            if not hmac.compare_digest(expected_signature, supplied_signature):
                raise InvalidAccessTokenError

            header = json.loads(_base64url_decode(encoded_header))
            payload = json.loads(_base64url_decode(encoded_payload))
            self._validate_claims(header, payload, now or datetime.now(timezone.utc))
            return uuid.UUID(payload["sub"])
        except InvalidAccessTokenError:
            raise
        except (ValueError, TypeError, KeyError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError) as exc:
            raise InvalidAccessTokenError from exc

    @staticmethod
    def _encode_json(value: dict[str, Any]) -> str:
        return _base64url_encode(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))

    def _validate_claims(self, header: Any, payload: Any, now: datetime) -> None:
        if not isinstance(header, dict) or header.get("alg") != JWT_ALGORITHM or header.get("typ") != "JWT":
            raise InvalidAccessTokenError
        if not isinstance(payload, dict) or payload.get("type") != self.token_type:
            raise InvalidAccessTokenError
        if not isinstance(payload.get("sub"), str):
            raise InvalidAccessTokenError
        iat = payload.get("iat")
        exp = payload.get("exp")
        if (
            not isinstance(iat, int)
            or isinstance(iat, bool)
            or not isinstance(exp, int)
            or isinstance(exp, bool)
            or exp <= int(now.timestamp())
            or iat > int(now.timestamp()) + 60
        ):
            raise InvalidAccessTokenError


class AdultAccessTokenCodec(AccessTokenCodec):
    def __init__(self, secret: str, expiration_minutes: int):
        super().__init__(secret, expiration_minutes, ADULT_ACCESS_TOKEN_TYPE)


class DeviceAccessTokenCodec(AccessTokenCodec):
    def __init__(self, secret: str, expiration_minutes: int):
        super().__init__(secret, expiration_minutes, DEVICE_ACCESS_TOKEN_TYPE)


def peek_access_token_type(token: str) -> str | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = json.loads(_base64url_decode(parts[1]))
        token_type = payload.get("type") if isinstance(payload, dict) else None
        return token_type if isinstance(token_type, str) else None
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
        return None

