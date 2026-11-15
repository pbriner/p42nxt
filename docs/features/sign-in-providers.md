# Sign-in providers

People can sign in through an OpenID Connect provider: Microsoft Entra, Google, Okta and most
others (`backend/src/p42cloud/security/providers/`). An installation can have several.

## A provider

| Field | Meaning |
|---|---|
| Name | Shown on the sign-in page |
| Issuer | The provider's address (https); its configuration and keys are read from there |
| Client ID, client secret | From registering P42cloud with the provider. The secret is sealed with the master key and set on its own, never as a plain field |
| Scopes | Default `openid email profile` |
| Signs in | Employees or external people: people of another kind can't use it |
| Email domains | Domains it claims: those addresses always sign in through it |
| Creates people | Whether someone unknown is made a person at their first sign-in; off by default |
| Vouches for emails | The provider stands for every email it sends (a company's own tenant), so people may be linked by email without an `email_verified` claim |
| Groups claim | The claim listing the person's groups there (Entra: `groups`): groups following this provider take their members from it at each sign-in (`access.md`) |
| Fields from claims | Person fields filled from the ID token at every sign-in, like `{"name": "name"}`. Never `kind`, `active`, `emergency_access` or `email` |
| Active | Off: not offered, and nobody signs in through it |

Providers need the app's address (the setting `sys.public_url`): the provider sends people back to
`<public url>/api/v1/sign-in/provider-callback`. Only administrators manage providers.

## Signing in

1. The browser is sent to the provider with a `state` and a `nonce` that work once and for 10
   minutes, and a PKCE challenge (S256).
2. The provider sends it back with a code, which is exchanged for an ID token. The token must be
   signed by one of the keys the provider publishes, with RS256 or ES256 (never `none` or a
   shared-secret algorithm), issued by that provider, for this client, not expired, with the
   nonce. The provider's addresses must all use https.
3. **Who it is**:
   - first by the provider's subject ID, kept as an external ID of the person (`sys.external_id`,
     system `provider:<id>`);
   - otherwise **linked once by email**: only a person of the kind the provider signs in, only if
     the provider confirms the email (or vouches for its emails), and only someone not linked to
     that provider yet;
   - otherwise **made**, if the provider may make people: of its kind, with the email only if
     confirmed.
4. The fields from claims are updated, so groups with rules follow at once. An inactive person
   is refused.
5. A session begins. The provider's own second factor counts: P42cloud doesn't ask again.

Every failure is a security event (`provider_sign_in_failed`) with its reason; the browser only
learns that it didn't work.

## Claimed domains and emergency access

An email address in a domain a provider claims signs in through that provider: signing in with a
password answers "sign in with <provider>", the same whether the account exists or not. People
with **emergency access** keep signing in with their password and second factor, so an outage of
the provider can't lock everyone out. `p42cloud create-admin` gives it to the first administrator;
administrators can give it to others.
