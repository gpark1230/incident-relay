import httpx
import pytest

from app.notifier import build_message, send_notification


EVENT = {
    "event": "incident.created",
    "incident_id": 5,
    "user_id": 3,
    "details": "severity: critical",
    "timestamp": "2026-08-13T18:00:00+00:00",
}


def test_build_message_includes_incident_and_details():
    message = build_message(EVENT, "user:3")
    assert "#5" in message
    assert "user:3" in message
    assert "severity: critical" in message


def test_send_notification_success(mocker):
    mock_post = mocker.patch(
        "app.notifier.httpx.post",
        return_value=httpx.Response(200, request=httpx.Request("POST", "https://example.com")),
    )

    send_notification(EVENT, "user:3")

    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["text"]


def test_send_notification_raises_on_http_error(mocker):
    mocker.patch(
        "app.notifier.httpx.post",
        return_value=httpx.Response(404, request=httpx.Request("POST", "https://example.com")),
    )

    with pytest.raises(httpx.HTTPStatusError):
        send_notification(EVENT, "user:3")
