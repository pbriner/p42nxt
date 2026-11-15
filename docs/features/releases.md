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

The core is released as the app image, not this way (next section).

## Releasing the core

Push a tag `v<version>`, like `v0.24.0`, the version in `backend/pyproject.toml`. The workflow
(`.github/workflows/release-core.yml`):

1. checks that the tag's version is the core's;
2. builds the app image for Intel and ARM machines and pushes it to the repository's container
   registry (`ghcr.io/<owner>/<repo>:<version>`);
3. adds the release to the registry index as `sys`, naming the image by its digest
   (`ghcr.io/<owner>/<repo>@sha256:…`), signed with the release key, and signs the index again;
4. publishes a GitHub Release with the changelog entry and the image.

A tag can be moved to another image; a digest can't, so installations only ever use the digest.
The image must be public for installations to download it without signing in (GitHub: the
package's settings, once).

## Updating an installation

```bash
deploy/update.sh <name>                     # to the newest core release
deploy/update.sh <name> --version 0.24.0    # to this one
deploy/update.sh <name> --image p42cloud:0.24.0   # an image built on this machine
```

For a release, the image running now reads the signed index (`p42cloud core image`) and names
the new image only if a key it trusts signed it and it's not a lower version. An image built on
the machine isn't checked; the script says so.

Then the script:

1. backs up the database (`p42-before-update-<time>.dump` in the backup folder, kept by the
   nightly clean-up);
2. takes the new image's own `compose.yml` and sets the image in `.env`;
3. stops the app and the worker, upgrades the database, checks that it's safe, applies the
   settings the machine needs (the backup folder, from the database), and starts them;
4. if the upgrade or the check fails, starts the version before again and names the backup.

Changing the core to a new image needs control over Docker on the machine, so the app never
does it itself: an update is always run by a person (or a timer they set up) on the machine.

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

`p42cloud module updates` reads the index from the setting **Release address** (`sys.registry_url`; for a GitHub repository:
`https://github.com/<owner>/<repo>/releases/download/registry`), refuses it unless a trusted key
signed it, and records, for every installed module with a newer release, the newest one
(`sys.module_update`): installed and available version, normal or security, release notes, and
whether it fits what's installed, or what must be updated first ("needs sys >=2.1"). Security
updates come first. Only administrators see these records.

The installation only downloads the index; it sends nothing about itself. The release address is
empty by default, and must use https in production.

## Installing a module from its package

An installation runs the modules that come with its image, and those installed from signed
packages into its modules folder (`P42_MODULES_DIR`; in the deployment, the volume `modules`
at `/data/modules`). In the installation's folder:

```bash
docker compose run --rm module install dca           # the newest release
docker compose run --rm module update dca            # to the newest release
docker compose run --rm module update dca --version 0.5.0
docker compose restart web worker                    # to use it
```

`--file <package>.whl` installs a package downloaded by hand instead; its signature must lie
next to it (`<package>.whl.sig`). No registry is needed then. A package built by hand is signed with
`uv run python -m p42cloud.modules.release_tool sign --file <package>.whl` (in `backend/`): it
asks for the private release key without showing it (or takes `P42_RELEASE_KEY`), and refuses a
key the platform doesn't trust. In the deployment the file reaches the `module` command through
a folder given to it: `docker compose run --rm -v <folder>:/pkg:ro module install <module>
--file /pkg/<package>.whl`.

Before anything changes, the package must:

- be the one the signed index lists (its SHA-256), and be signed by a trusted key;
- fit what's installed (its `depends_on`), and never go back to a lower version;
- hold only its own module: one Python package and its metadata, no paths leaving its folder,
  no links, not more than 5000 files or 50 MB;
- need no library the image hasn't got, in a version it accepts (nothing else is installed with
  it).

It's then unpacked beside the version before, the module installed or upgraded (only this one:
other modules found but not installed stay out), and the version before removed. If the install
fails, the version before is put back. Each install is a security event
(`module_package_installed`, with the module, version, SHA-256 and where it came from).

Only the `module` service can write to the modules folder; the app, the worker and the other
commands only read it.

Files are downloaded through the guarded client (https only, no address inside a network), from
the registry's host only; for a registry on GitHub also from GitHub's download hosts
(`*.githubusercontent.com`), where it redirects to.
