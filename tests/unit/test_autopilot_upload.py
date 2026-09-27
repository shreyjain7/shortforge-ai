from shortforge.core.config import AutopilotSettings
from shortforge.workers.stages import autopilot_may_upload

ON = AutopilotSettings(enabled=True, auto_upload=True)


def test_autopilot_uploads_fresh_automatic_short():
    assert autopilot_may_upload(ON, "ready", manual=False, tried_before=False)


def test_manual_renders_are_never_auto_uploaded():
    assert not autopilot_may_upload(ON, "ready", manual=True, tried_before=False)


def test_short_is_auto_uploaded_at_most_once():
    assert not autopilot_may_upload(ON, "ready", manual=False, tried_before=True)


def test_requires_ready_and_settings():
    assert not autopilot_may_upload(ON, "review", manual=False, tried_before=False)
    assert not autopilot_may_upload(AutopilotSettings(enabled=True), "ready", manual=False, tried_before=False)
    assert not autopilot_may_upload(AutopilotSettings(auto_upload=True), "ready", manual=False, tried_before=False)


def test_disabled_api_error_is_explained():
    from shortforge.engines.publishing.youtube_auth import explain_api_error

    msg = explain_api_error(Exception('403 ... reason "accessNotConfigured" ... has not been used in project 123'))
    assert msg and "Enable it" in msg
    assert "no YouTube channel" in (explain_api_error(Exception("youtubeSignupRequired")) or "")
    assert explain_api_error(Exception("quotaExceeded")) is None
