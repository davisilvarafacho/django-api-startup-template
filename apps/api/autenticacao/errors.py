from enum import StrEnum


class AuthErrorCode(StrEnum):
    INVALID_CREDENTIALS = "auth.invalid_credentials"
    INVALID_TOKEN = "auth.invalid_token"
    EXPIRED_TOKEN = "auth.expired_token"
    REVOKED_TOKEN = "auth.revoked_token"
    REAUTHENTICATION_REQUIRED = "auth.reauthentication_required"
    INVALID_CHALLENGE = "auth.invalid_challenge"
    INVALID_OTP = "auth.invalid_otp"
    OTP_COOLDOWN = "auth.otp_cooldown"
    TOO_MANY_ATTEMPTS = "auth.too_many_attempts"
    PWNED_PASSWORD = "auth.pwned_password"
    DELIVERY_UNAVAILABLE = "auth.delivery_unavailable"
