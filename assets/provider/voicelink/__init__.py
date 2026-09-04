"""VoiceLink telephony provider package."""

from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import VoiceLinkConfigurationRequest, VoiceLinkConfigurationResponse
from .provider import VoiceLinkProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    # Credentials come from the Settings → Telephony card. did_number /
    # from_numbers are NOT card fields — the telephony factory fills
    # from_numbers from the active telephony_phone_numbers rows and the
    # campaign dispatcher reads it to build the outbound caller-id pool —
    # but pass through anything already stored so an older Dograh without
    # that factory step still works.
    return {
        "provider": "voicelink",
        "api_base": value.get("api_base"),
        "username": value.get("username"),
        "password": value.get("password"),
        "bearer_token": value.get("bearer_token"),
        "did_number": value.get("did_number"),
        "from_numbers": value.get("from_numbers", []),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="VoiceLink",
    docs_url="https://docs.dograh.com/integrations/telephony/voicelink",
    fields=[
        ProviderUIField(
            name="username",
            label="Username",
            type="text",
            required=False,
            sensitive=True,
            description=(
                "VoiceLink account username. Provide username + password so "
                "expired tokens can be refreshed automatically."
            ),
        ),
        ProviderUIField(
            name="password",
            label="Password",
            type="password",
            required=False,
            sensitive=True,
        ),
        ProviderUIField(
            name="bearer_token",
            label="Bearer Token",
            type="password",
            required=False,
            sensitive=True,
            description=(
                "Static VoiceLink bearer token. Optional when username and "
                "password are provided."
            ),
        ),
    ],
)


SPEC = ProviderSpec(
    name="voicelink",
    provider_cls=VoiceLinkProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=VoiceLinkConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=VoiceLinkConfigurationResponse,
    account_id_credential_field="username",
)


register(SPEC)


__all__ = [
    "SPEC",
    "VoiceLinkConfigurationRequest",
    "VoiceLinkConfigurationResponse",
    "VoiceLinkProvider",
    "create_transport",
]
