"""Credentials and sessions. Nobody reads credentials; people see and end their own sessions."""

from typing import Any, cast

from p42cloud.data.declarations import Table
from p42cloud.data.settings import Setting
from p42cloud.data.types import Invalid
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.integer import Integer
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.secret import Secret
from p42cloud.data.types.string import String

CREDENTIAL = Table(
    "credential",
    "Credential",
    {
        "person": Reference("Person", "sys.person", required=True),
        "kind": Choice(
            "Kind",
            {
                "password": "Password",
                "totp": "Authenticator app",
                "passkey": "Passkey or security key",
                "recovery": "Recovery code",
            },
            required=True,
        ),
        # A password's hash, an authenticator's key sealed with the master key, a passkey's
        # public key, or a recovery code's hash. Never read back, masked in the audit log.
        "secret": Secret("Secret", required=True),
        "label": String("Name", max_length=80),
        # An authenticator app counts once it gave a right code.
        "confirmed": DateTime("Confirmed"),
        # The last time step an authenticator's code was used: a code works only once.
        "last_step": Integer("Last step"),
        # A passkey's ID, as the browser names it, and its signature counter: a counter going
        # backwards means the key was copied.
        "key_id": String("Passkey ID", max_length=1024),
        "counter": Integer("Signature counter"),
        "last_used": DateTime("Last used"),
    },
    label_plural="Credentials",
)

SESSION = Table(
    "session",
    "Session",
    {
        "person": Reference("Person", "sys.person", required=True),
        # The hash of the token the browser holds; the token itself is never stored.
        "token": String("Token hash", max_length=64, required=True, unique=True),
        "stage": Choice(
            "Stage",
            {
                "second_factor": "Waiting for the second factor",
                "enroll": "Setting up a second factor",
                "active": "Signed in",
            },
            required=True,
        ),
        "expires": DateTime("Ends at the latest", required=True),
        "last_seen": DateTime("Last used", required=True),
        "verified": DateTime("Second factor given"),
        # A passkey challenge waiting for its answer; each works once.
        "challenge": String("Passkey challenge", max_length=128),
        "address": String("Address", max_length=64),
        "agent": String("Browser", max_length=255),
    },
    label_plural="Sessions",
)


def _kinds(given: Any) -> list[str]:
    allowed = {"employee", "external"}
    if not isinstance(given, list) or not set(cast(list[Any], given)) <= allowed:
        raise Invalid("A list of kinds of person: employee, external.")
    return sorted(set(cast(list[str], given)))


PASSWORDS_OFF = Setting(
    "sys.passwords_off_for",
    "No passwords for",
    [],
    check=_kinds,
    help="Kinds of person who sign in only through a sign-in provider. People with emergency"
    " access keep their password.",
)

TABLES = [CREDENTIAL, SESSION]
