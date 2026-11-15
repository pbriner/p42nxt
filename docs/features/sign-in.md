# Sign-in

People sign in with a password and, always, a second factor, or through a sign-in provider
(`sign-in-providers.md`). Sessions are kept on the server.
The HTTP routes and the browser cookie come with the web layer; this is the service under them
(`backend/src/p42cloud/security/sign_in/`).

## Passwords

- At least 12 characters and at most 256.
- Not one of the breached passwords that ship with each release: the entries of 12 characters
  or more of a list of the million most used breached passwords (see `NOTICE`), compared without
  regard to case. Checked offline: nothing leaves the installation.
- Not containing the person's email name.
- Hashed with Argon2id. A hash made with older settings is renewed at the next sign-in.
- A new password ends the person's other sessions.

## Second factor

- **Authenticator apps** (TOTP, RFC 6238): six digits, every 30 seconds. A code is accepted for
  the current step and one either side, and each step only once. The app's key is shown once
  (as a key and an `otpauth://` link for a QR code) and stored sealed with the master key.
- An app counts once it gave its first right code.
- **Passkeys and security keys** (WebAuthn): the browser signs a fresh challenge with a key that
  never leaves the device, and only for this installation's address, so a look-alike site gets
  nothing it could use. The installation keeps the public key, the passkey's ID and its signature
  counter; a counter going backwards means the key was copied, and is refused. Each challenge
  works once. Passkeys need the app's address (below); without it they're unavailable and
  authenticator apps still work.
- **Adding a second factor** while signed in needs the second factor given in the last 15
  minutes, so whoever took over an open session can't add a key of their own. It ends the
  person's other sessions.
- **Recovery codes**: ten one-time codes (`ABCD-EFGH-JKLM-NPQR`), shown once when the first app is
  set up; new ones replace the old ones. Only their hashes are stored.
- There's no SMS, and no way to switch the second factor off.

## Signing in

1. **Email and password.** A wrong password, an unknown email and an inactive person get the same
   answer after the same time. Service accounts never sign in this way, and addresses in a domain
   a sign-in provider claims sign in through it (except people with emergency access). Someone without a second factor yet can only set one up.
2. **The second factor.** An authenticator code, a recovery code, or a passkey. The waiting
   session ends and a new one begins, with a new token. Someone without a second factor sets one
   up here first (an app or a passkey) and gets their recovery codes.

Waiting sessions end after 10 minutes.

## Sessions

- The browser holds a random token; only its hash is stored.
- A session ends after the setting `sys.session_idle_minutes` without use (default 30) and after
  `sys.session_max_hours` in any case (default 12). Requests the app makes by itself (the bell
  asking for notifications) send `X-P42-Background: 1` and don't count as use, so an open tab
  doesn't keep a session alive.
- People see their own sessions and end them; administrators see and end anyone's. Nobody sees
  a session's token.
- **Changes to access** (groups, memberships, roles, grants) need the second factor given in the
  last 15 minutes. Giving it again doesn't start a new session. The browser app asks for the code
  when the server says so (`verify_again`), gives it (`POST /api/v1/sign-in/verify`) and makes
  the change again, once.

## Invitations and resets

Administrators bring people in with a one-time link, valid 7 days, which they pass on (email
comes later). It leads to setting a password, then a second factor.
In the app it's the **Sign-in** section of a person's form (**Make invitation link**, **Reset
sign-in**); through the API, `POST /api/v1/people/{person}/invitation` with `{"reset": false}`.

- A **reset** first ends the person's sessions and retires all their credentials (password,
  apps, passkeys, recovery codes), then makes a new link.
- Only administrators make links, with the second factor given in the last 15 minutes. Service
  accounts get none; they use API tokens.
- Only the newest link of a person works, and each works once. Only its hash is stored. Wrong
  links are security events (`invitation_refused`) and slow an address down like failed
  sign-ins.
- An administrator never chooses anyone's password.

## Passwords switched off

The setting `sys.passwords_off_for` (`settings.md`) names kinds of person who sign in only
through a sign-in provider. They're told so only after giving the right password, so it reveals
nothing about who has an account. People with emergency access keep their password.

## The installation's address

The setting **The app's address** (`sys.public_url`) is the address people open the app at, like `https://p42.example.com`: scheme
and host (and port), nothing after. Passkeys are bound to it. In production it must use https,
except for `localhost`.

## Slowing guessing down

Every failed attempt is a security event. After 5 failures within 15 minutes for one email
address (whether an account has it or not), or 20 from one network address, further attempts
are refused for 15 minutes, with the right password too. The same goes for 5 wrong codes or
passkey answers for one person, both while signing in and when giving them again.

## Security events

Entries of the audit log without a record: `sign_in`, `sign_in_failed`, `second_factor_failed`,
`sign_out`, `session_expired`, `session_ended`, `password_changed`, `second_factor_added`,
`recovery_codes_made`, `recovery_code_used`, `invitation_made`, `invitation_refused`,
`credentials_reset`. Only the core writes them.

## Credentials and secrets

Credentials (password hashes, sealed app keys, passkeys' public keys, recovery code hashes) are
`Secret` fields: never
returned by the records service and masked in the audit log. Nobody has a grant on them.

## The master key

The installer makes a random master key (`master.key` in the installation's folder,
`P42_MASTER_KEY_FILE`). Secrets that must be read back, like an authenticator's key, are sealed
with it: AES-GCM, with a key derived for each purpose, so a value sealed for one purpose can't be
opened as another. Without the master key nothing is sealed, and sealed secrets can't be
recovered.

## The first administrator

```bash
docker compose --project-directory ~/.p42cloud/<name> run --rm create-admin \
  --name "Anna Muster" --email anna@example.com
```

Asks for the password twice (it never appears on the command line), shows the key for an
authenticator app, asks for its first code, and prints the recovery codes. The person joins the
Administrators group. Works only while that group has no active member, and makes nothing if the
code is wrong three times.
