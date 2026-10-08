import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from api.services.telephony.providers.voicelink.provider import VoiceLinkProvider
from api.services.telephony.providers.voicelink.routes import handle_voicelink_events


def _provider() -> VoiceLinkProvider:
    return VoiceLinkProvider(
        {
            "api_base": "https://app.voicelink.co.in/api",
            "username": "reseller-user",
            "password": "placeholder-password",
        }
    )


def _body(event: str = "call.completed") -> str:
    return json.dumps(
        {
            "event": event,
            "timestamp": "2026-06-11T10:01:08Z",
            "call": {
                "id": "5b2f9c1e-aaaa-bbbb-cccc-1234567890ab",
                "direction": "outbound",
                "callType": "outbound",
                "from": "919484959244",
                "to": "7340400524",
                "status": "completed",
                "hangupCause": "16",
                "durationSec": 60,
                "customParameters": {"workflow_run_id": 123},
            },
        },
        separators=(",", ":"),
    )


def _request(body: str) -> Request:
    async def receive():
        return {
            "type": "http.request",
            "body": body.encode("utf-8"),
            "more_body": False,
        }

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/telephony/voicelink/events/123",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_voicelink_events_route_processes_status_update():
    provider = _provider()
    body = _body("call.completed")

    with (
        patch(
            "api.services.telephony.providers.voicelink.routes.db_client"
        ) as db_client,
        patch(
            "api.services.telephony.providers.voicelink.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.voicelink.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        result = await handle_voicelink_events(_request(body), workflow_run_id=123)

    assert result == {"status": "success"}
    process_status.assert_awaited_once()
    _, status_update = process_status.await_args.args
    assert status_update.status == "completed"
    assert status_update.call_id == "5b2f9c1e-aaaa-bbbb-cccc-1234567890ab"
    assert status_update.duration == "60"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event,expected_status",
    [
        ("call.initiated", "initiated"),
        ("call.ringing", "ringing"),
        ("call.answered", "in-progress"),
        ("call.ended", "completed"),
        ("call.failed", "failed"),
    ],
)
async def test_voicelink_events_route_maps_lifecycle_events(event, expected_status):
    provider = _provider()

    with (
        patch(
            "api.services.telephony.providers.voicelink.routes.db_client"
        ) as db_client,
        patch(
            "api.services.telephony.providers.voicelink.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.voicelink.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        result = await handle_voicelink_events(
            _request(_body(event)), workflow_run_id=123
        )

    assert result == {"status": "success"}
    _, status_update = process_status.await_args.args
    assert status_update.status == expected_status


@pytest.mark.asyncio
async def test_voicelink_events_route_ignores_unknown_workflow_run():
    with (
        patch(
            "api.services.telephony.providers.voicelink.routes.db_client"
        ) as db_client,
        patch(
            "api.services.telephony.providers.voicelink.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=None)

        result = await handle_voicelink_events(_request(_body()), workflow_run_id=123)

    assert result == {"status": "ignored", "reason": "workflow_run_not_found"}
    process_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_voicelink_events_route_rejects_invalid_json_without_raising():
    with patch(
        "api.services.telephony.providers.voicelink.routes._process_status_update",
        new_callable=AsyncMock,
    ) as process_status:
        result = await handle_voicelink_events(
            _request("not-json{"), workflow_run_id=123
        )

    assert result == {"status": "error", "reason": "invalid_json"}
    process_status.assert_not_awaited()


def _unanswered_body(event: str) -> str:
    body = json.loads(_body(event))
    body["call"].update(
        answeredAt=None,
        hangupCause="19 - User alerting, no answer",
        callStatus="NO ANSWER",
        durationSec=None,
    )
    return json.dumps(body)


async def _post(body: str, workflow_run, claimed: bool):
    with (
        patch(
            "api.services.telephony.providers.voicelink.routes.db_client"
        ) as db_client,
        patch(
            "api.services.telephony.providers.voicelink.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=_provider(),
        ),
        patch(
            "api.services.telephony.providers.voicelink.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
        patch(
            "api.services.telephony.providers.voicelink.routes._claim_terminal_event",
            new_callable=AsyncMock,
            return_value=claimed,
        ),
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )
        db_client.update_workflow_run = AsyncMock()

        result = await handle_voicelink_events(_request(body), workflow_run_id=123)

    return result, process_status, db_client.update_workflow_run


@pytest.mark.asyncio
async def test_unanswered_call_is_processed_as_no_answer():
    run = SimpleNamespace(workflow_id=7, state="initialized", logs={})

    result, process_status, _ = await _post(
        _unanswered_body("call.failed"), run, claimed=True
    )

    assert result == {"status": "success"}
    _, status_update = process_status.await_args.args
    assert status_update.status == "no-answer"


@pytest.mark.asyncio
async def test_duplicate_terminal_event_is_logged_but_not_processed():
    run = SimpleNamespace(
        workflow_id=7,
        state="completed",
        logs={"telephony_status_callbacks": [{"status": "no-answer"}]},
    )

    result, process_status, update_run = await _post(
        _unanswered_body("call.completed"), run, claimed=False
    )

    assert result == {"status": "success"}
    process_status.assert_not_awaited()
    callbacks = update_run.await_args.kwargs["logs"]["telephony_status_callbacks"]
    assert len(callbacks) == 2
    assert callbacks[-1]["duplicate"] is True
    assert callbacks[-1]["status"] == "no-answer"


@pytest.mark.asyncio
async def test_answered_call_without_media_stream_is_recorded_as_failed():
    """The run never left INITIALIZED, so no agent ever spoke on the call."""
    run = SimpleNamespace(workflow_id=7, state="initialized", logs={})
    body = json.loads(_body("call.completed"))
    body["call"]["answeredAt"] = "2026-06-11T10:00:08Z"

    _, process_status, _ = await _post(json.dumps(body), run, claimed=True)

    _, status_update = process_status.await_args.args
    assert status_update.status == "failed"


@pytest.mark.asyncio
async def test_answered_call_with_running_pipeline_stays_completed():
    run = SimpleNamespace(workflow_id=7, state="running", logs={})
    body = json.loads(_body("call.completed"))
    body["call"]["answeredAt"] = "2026-06-11T10:00:08Z"

    _, process_status, _ = await _post(json.dumps(body), run, claimed=True)

    _, status_update = process_status.await_args.args
    assert status_update.status == "completed"


@pytest.mark.asyncio
async def test_in_flight_events_do_not_claim_the_terminal_slot():
    run = SimpleNamespace(workflow_id=7, state="initialized", logs={})

    with patch(
        "api.services.telephony.providers.voicelink.routes._claim_terminal_event",
        new_callable=AsyncMock,
    ) as claim:
        with (
            patch("api.services.telephony.providers.voicelink.routes.db_client") as db,
            patch(
                "api.services.telephony.providers.voicelink.routes.get_telephony_provider_for_run",
                new_callable=AsyncMock,
                return_value=_provider(),
            ),
            patch(
                "api.services.telephony.providers.voicelink.routes._process_status_update",
                new_callable=AsyncMock,
            ),
        ):
            db.get_workflow_run_by_id = AsyncMock(return_value=run)
            db.get_workflow_by_id = AsyncMock(
                return_value=SimpleNamespace(organization_id=11)
            )
            await handle_voicelink_events(
                _request(_body("call.ringing")), workflow_run_id=123
            )

    claim.assert_not_awaited()
