"""The transfer tool driving a real VoiceLink provider end to end.

VoiceLink re-routes a live call itself when it receives a ``transfer`` event on
the media WebSocket, so the transfer tool takes its external-PBX path: play the
handoff message, send the event, stamp the run as transferred, drop our leg.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.utils.enums import EndTaskReason

from api.enums import WorkflowRunMode
from api.services.telephony.providers.voicelink import provider as provider_module
from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager
from api.services.workflow.tools.transfer_resolver import ResolvedTransferConfig
from api.tests.telephony.voicelink.test_provider import _provider, _SendingWebSocket
from api.tests.test_transfer_message_playback import RecordingEngine, TransferToolModel

_TOOLS = "api.services.workflow.pipecat_engine_custom_tools"


async def _run_transfer(workflow_run, websocket, destination="+91 98290 12345"):
    engine = RecordingEngine()
    handler = CustomToolManager(engine)._create_transfer_call_handler(
        TransferToolModel(), "transfer_call"
    )
    params = SimpleNamespace(arguments={}, result_callback=AsyncMock())
    update_run = AsyncMock()

    with (
        patch.dict(provider_module._ACTIVE_SOCKETS, {1: websocket}, clear=True),
        patch(
            f"{_TOOLS}.db_client.get_workflow_run_by_id",
            AsyncMock(return_value=workflow_run),
        ),
        patch(
            f"{_TOOLS}.db_client.get_workflow_run_configurations",
            AsyncMock(return_value={"external_pbx_field_mappings": []}),
        ),
        patch(f"{_TOOLS}.db_client.update_workflow_run", update_run),
        patch(
            f"{_TOOLS}.get_telephony_provider_for_run",
            AsyncMock(return_value=_provider()),
        ),
        patch(
            f"{_TOOLS}.resolve_transfer_config",
            AsyncMock(
                return_value=ResolvedTransferConfig(
                    destination=destination, timeout_seconds=30, source="static"
                )
            ),
        ),
        patch(f"{_TOOLS}.asyncio.sleep", AsyncMock()),
    ):
        await handler(params)

    return engine, params, update_run


@pytest.mark.asyncio
async def test_transfer_tool_hands_voicelink_call_off_after_message():
    ws = _SendingWebSocket()
    workflow_run = SimpleNamespace(
        mode=WorkflowRunMode.VOICELINK.value,
        initial_context={
            "external_pbx_call": {"type": "voicelink", "workflow_run_id": 1}
        },
        gathered_context={"call_id": "CA1"},
    )

    engine, params, update_run = await _run_transfer(workflow_run, ws)

    assert ws.sent == [{"event": "transfer", "target": "9829012345"}]
    # The caller hears the handoff message before VoiceLink moves them.
    assert engine.events.index("wait_for_playback") < engine.events.index(
        ("disposition", EndTaskReason.CALL_TRANSFERRED.value)
    )
    assert engine.events[-1] == ("end_call", EndTaskReason.CALL_TRANSFERRED.value)
    assert update_run.await_args.kwargs["gathered_context"][
        "external_pbx_transferred"
    ] is True
    assert params.result_callback.await_args.args[0]["status"] == "success"


@pytest.mark.asyncio
async def test_transfer_tool_keeps_call_when_voicelink_send_fails():
    """A failed handoff resumes the agent instead of ending the call."""
    ws = _SendingWebSocket(fail=True)
    workflow_run = SimpleNamespace(
        mode=WorkflowRunMode.VOICELINK.value,
        initial_context={
            "external_pbx_call": {"type": "voicelink", "workflow_run_id": 1}
        },
        gathered_context={"call_id": "CA1"},
    )

    engine, params, _ = await _run_transfer(workflow_run, ws)

    assert not any(
        isinstance(e, tuple) and e[0] == "end_call" for e in engine.events
    )
    result = params.result_callback.await_args.args[0]
    assert result["status"] == "transfer_failed"
    assert result["reason"] == "voicelink_transfer_send_failed"
