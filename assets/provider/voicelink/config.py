"""VoiceLink telephony configuration schemas."""

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

DEFAULT_VOICELINK_API_BASE = "https://app.voicelink.co.in/api"


class VoiceLinkConfigurationRequest(BaseModel):
    """Request schema for VoiceLink configuration.

    Only credentials are configured here. ``api_base`` is not exposed on the
    Settings → Telephony card — it falls back to ``DEFAULT_VOICELINK_API_BASE``.
    Phone numbers / DIDs are managed separately as ``telephony_phone_numbers``
    rows on the config detail page.
    """

    provider: Literal["voicelink"] = Field(default="voicelink")
    api_base: str = Field(
        default=DEFAULT_VOICELINK_API_BASE,
        description="VoiceLink API base URL",
    )
    username: Optional[str] = Field(
        default=None,
        description=(
            "VoiceLink account username. Used together with password to "
            "obtain (and refresh) bearer tokens via /v1/auth/login."
        ),
    )
    password: Optional[str] = Field(
        default=None, description="VoiceLink account password"
    )
    bearer_token: Optional[str] = Field(
        default=None,
        description=(
            "Static VoiceLink bearer token. Optional when username/password "
            "are provided — those allow automatic re-login on token expiry."
        ),
    )

    @model_validator(mode="after")
    def _require_credentials(self) -> "VoiceLinkConfigurationRequest":
        if not self.bearer_token and not (self.username and self.password):
            raise ValueError(
                "VoiceLink configuration requires either bearer_token or "
                "both username and password"
            )
        return self


class VoiceLinkConfigurationResponse(BaseModel):
    """Response schema for VoiceLink configuration with masked sensitive fields."""

    provider: Literal["voicelink"] = Field(default="voicelink")
    api_base: str = DEFAULT_VOICELINK_API_BASE
    username: Optional[str] = None  # Masked
    password: Optional[str] = None  # Masked
    bearer_token: Optional[str] = None  # Masked
