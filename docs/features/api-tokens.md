# API tokens and service accounts

Scripts, integrations and AI agents use the API with a token. A token acts as one person or one
service account (`backend/src/p42cloud/security/tokens/`).

## Service accounts

A service account is a person of the kind *service account*: an import, a job, an integration.

- It gets its rights like anyone else, as a member of groups, but never of a group with a rule:
  what it may do is given to it by hand.
- It never signs in with a password; it works through API tokens only.

## Tokens

| Field | Meaning |
|---|---|
| Acts as | The person or service account |
| Name | Shown in the audit log: requests with the token have the channel `api:token:<name>` |
| Starts with | The token's first characters (`p42_…`), to tell tokens apart |
| Only these roles | The roles it may use; empty: all of the account's roles |
| Expires | 90 days after it's made unless set otherwise, a year at the most |
| Last used | Updated at most every 5 minutes |

- **At most the account's rights**: a token naming roles gets those and the roles they include,
  as far as the account holds them, never more. An account that loses a role or becomes inactive
  takes it away from its tokens at once.
- **Shown once**: only the token's hash is stored. Lost tokens are revoked and made again.
- **Who makes tokens**: people for themselves; administrators for service accounts. Either needs
  the second factor given in the last 15 minutes, so a token can't make another token.
- **A token can't change access**: it gives no second factor (`access.md`).
- People see their own tokens and revoke them; administrators see and revoke anyone's. Nobody
  sees a token's hash.

## Security events

`api_token_made`, `api_token_revoked`, and `api_token_refused` for every request with a wrong,
revoked or expired token. After 20 refused tokens from one network address within 15 minutes,
further requests from it are refused for 15 minutes.
