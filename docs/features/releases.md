# Releases

Every module, and the core, is released on its own (`backend/src/p42cloud/modules/releases.py`,
`release_tool.py`, `.github/workflows/release.yml`).

## Changing a package means releasing it

A pull request that changes a package's shipped files must raise its version (in its
`pyproject.toml`) and add a `CHANGELOG.md` entry for that version. CI checks it
(`python -m p42cloud.modules.release_tool check --base <branch>`). The packages are the core
(`backend/`) and every module (`modules/<id>/`). Tests and the README aren't shipped, so changing
only them needs no release.

```markdown
## 1.4.1 (security)

- What changed, in plain words.
```

`(security)` marks a security release: installations list it first.

## Releasing a module

Push a tag `<module>/v<version>`, like `hello/v0.1.0`. The release workflow:

1. checks that the tag's version is the one in the module's `pyproject.toml`;
2. runs the module's lint, types and tests;
3. builds its package (a wheel, with its documentation);
4. signs it with the release key and publishes it as a GitHub Release, with the changelog entry
   as release notes;
5. adds it to the **registry index** and signs the index again. The index and its signature are
   the files `index.json` and `index.json.sig` of the release named `registry`.

The core is released as the app image, not this way.

## Signatures

Packages and the index are signed with an Ed25519 key. Installations trust only the public keys
listed in `TRUSTED_KEYS` (`releases.py`): with none listed, nothing verifies, so nothing from a
registry is believed. A signature is checked offline, so packages uploaded by hand are checked
the same way.

Setting up the release key, once:

1. `uv run python -m p42cloud.modules.release_tool keygen`
2. Add the private half as the repository secret `P42_RELEASE_KEY` (GitHub: Settings, Secrets
   and variables, Actions). Keep no other copy outside a password manager.
3. Add the public half to `TRUSTED_KEYS`, and release the core with it.

## The index

```json
{"format": 1, "modules": {"hello": [
  {"version": "0.1.0", "depends_on": {"sys": ">=0.1,<1"}, "kind": "normal",
   "notes": "…", "package": "https://…/p42cloud_hello-0.1.0-py3-none-any.whl",
   "sha256": "…", "signature": "…"}]}}
```

## Learning about updates

`p42cloud module updates` reads the index from `P42_REGISTRY_URL` (for a GitHub repository:
`https://github.com/<owner>/<repo>/releases/download/registry`), refuses it unless a trusted key
signed it, and records, for every installed module with a newer release, the newest one
(`sys.module_update`): installed and available version, normal or security, release notes, and
whether it fits what's installed, or what must be updated first ("needs sys >=2.1"). Security
updates come first. Only administrators see these records.

The installation only downloads the index; it sends nothing about itself. `P42_REGISTRY_URL` is
off by default, and must use https in production.
