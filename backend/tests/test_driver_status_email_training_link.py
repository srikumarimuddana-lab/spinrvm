"""The driver approval email names the training host, and links it.

Reported from live testing: an approved driver's "You're Approved!" email sent
them straight to Go Online and never mentioned training. The address existed
only in the welcome email at signup — days or weeks earlier, and phrased
"before your first ride", the one instruction the approval email then
contradicted by telling them to start taking offers.

Companion to test_driver_status_email.py (the fan-out itself) and
test_driver_status_email_app_name.py (the `{app_name}` placeholder); this pins
the `{training}` placeholder on both approval paths, and pins that the three
non-approval emails are untouched by it.
"""

from unittest.mock import AsyncMock, patch

import pytest

import utils.company_details as cd_mod
from utils import driver_status_notifications as policy
from utils.driver_emails import TRAINING_HOST

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_DRIVER = {"id": "drv-1", "user_id": "usr-1", "status": "active"}
_USER = {"id": "usr-1", "first_name": "Kiran", "email": "kiran@example.test"}


async def _notify(message, settings=None, driver=_DRIVER, user=_USER):
    email = AsyncMock(return_value=True)
    with (
        patch("utils.email_notifications.send_lifecycle_email", email),
        patch("utils.email_notifications.resolve_recipient", AsyncMock(return_value=user)),
        patch("features.send_push_notification", AsyncMock(return_value=True)),
        patch.object(cd_mod, "get_app_settings", AsyncMock(return_value=settings or {})),
    ):
        await policy.notify_driver_status_change(driver, message, "test")
    return email.await_args.kwargs["rendered"]


# Both approval paths matter: the admin "approve" action, and entering `active`
# directly — which is what the document-approval auto-activation and the status
# override use, and what produced the reported email.
@pytest.mark.parametrize(
    "message_factory",
    [
        lambda: policy.action_message("approve"),
        lambda: policy.status_message("active"),
    ],
    ids=["admin_approve_action", "entering_active_status"],
)
async def test_approval_email_names_and_links_the_training_host(message_factory):
    rendered = await _notify(message_factory())

    assert TRAINING_HOST in rendered.text
    assert f'<a href="https://{TRAINING_HOST}"' in rendered.html
    # The placeholder must not survive into what the driver reads.
    assert "{training}" not in rendered.text
    assert "{training}" not in rendered.html


@pytest.mark.parametrize(
    "message_factory",
    [
        lambda: policy.action_message("approve"),
        lambda: policy.status_message("active"),
    ],
    ids=["admin_approve_action", "entering_active_status"],
)
async def test_approval_email_keeps_training_ahead_of_going_online(message_factory):
    """Training is stated as a precondition, not an afterthought.

    The welcome email promises training happens "before your first ride"; this
    pins that the approval email agrees rather than contradicting it, and that
    it still tells the driver how to go online.
    """
    text = (await _notify(message_factory())).text

    assert "before your first ride" in text
    assert text.index(TRAINING_HOST) < text.index("tap Go Online")


@pytest.mark.parametrize(
    "message_factory",
    [
        lambda: policy.action_message("approve"),
        lambda: policy.status_message("active"),
    ],
    ids=["admin_approve_action", "entering_active_status"],
)
async def test_training_line_is_additive_and_leaves_the_shipped_sentence_intact(message_factory):
    """The Go Online sentence must survive this change byte-for-byte.

    Training was added as its own sentence rather than folded into the existing
    one. An earlier draft joined the two with a dash ("... before your first
    ride — then open the Spinr driver app"), which silently lowercased "Open"
    and broke test_driver_status_email_app_name.py's `"Open the Spinr driver
    app" in body`. That assertion is what pins the `{app_name}` setting, so
    rewording around it costs real coverage; this test fails first and names
    the reason if someone reflows the copy again.
    """
    text = (await _notify(message_factory())).text

    assert "Open the Spinr driver app, tap Go Online, and you'll start receiving ride offers." in text


async def test_training_host_survives_a_renamed_app():
    """The host is a constant, so a white-labelled app name must not touch it."""
    rendered = await _notify(
        policy.action_message("approve"),
        settings={"company_app_name": "Northern Rides"},
    )

    assert TRAINING_HOST in rendered.text
    assert "Open the Northern Rides driver app" in rendered.text


# The other three email statuses are account blocks, not invitations to drive.
# Adding a training address to them would be noise at best, and at worst reads
# as a way to undo a suspension.
@pytest.mark.parametrize("action", ["reject", "suspend", "ban"])
async def test_blocking_emails_gain_no_training_link(action):
    rendered = await _notify(policy.action_message(action, reason="documents expired"))

    assert TRAINING_HOST not in rendered.text
    # No `links` map is passed at all for these, so they render exactly as before.
    assert "<a href" not in rendered.html
