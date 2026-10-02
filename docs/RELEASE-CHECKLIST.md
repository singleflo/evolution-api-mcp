# Release Checklist (User-Only Prerequisites)

This document outlines the manual release prerequisites and verification steps for publishing the `evolution-api-mcp`
MCP server from the `singleflo` organization. Steps 1 to 3 are done once; the steps that repeat for every release start
at Step 4. The hosted server and the store submissions have their own sections at the end.

These steps require human 2FA, web-UI access, or manual credentials that the automated worker cannot access. **Do not
attempt to run these steps via automated agents.**

---

## Step 1: PyPI Project & Trusted Publisher Setup
Before pushing the first release tag, you must configure PyPI to trust the GitHub repository via OpenID Connect (OIDC).
This allows GitHub Actions to publish to PyPI securely without storing long-lived API tokens.

1. Log in to your PyPI account at [pypi.org](https://pypi.org/). If you do not have an account, create one.
2. Navigate to the [PyPI Publishing Management page](https://pypi.org/manage/account/publishing/).
3. Add a **Pending Publisher** with the following details:
   - **PyPI Project Name**: `evolution-api-mcp`
   - **Owner (GitHub Username/Org)**: `singleflo`
   - **Repository Name**: `evolution-api-mcp`
   - **Workflow Name**: `publish.yml`
   - **Environment Name**: (Leave blank unless you explicitly configure a GitHub environment for releases)
4. Save the pending publisher. This must be done **before** the first tag is pushed, as Trusted Publishing requires the
   project to either not exist yet or have the pending publisher pre-registered.

---

## Step 2: GitHub Repository
- Create the public repository `singleflo/evolution-api-mcp` and push `main`. The tag in Step 4 needs it, and every URL
  in `pyproject.toml`, `server.json` and the plugin manifests points at it.
- In the repository settings, enable private vulnerability reporting: `SECURITY.md` sends reporters to GitHub security
  advisories.

---

## Step 3: MCP Registry GitHub OIDC Trust
The official Model Context Protocol (MCP) Registry supports GitHub-based authentication using OpenID Connect (OIDC).

- **No manual pre-registration or secret setup is required** on the MCP Registry website or GitHub settings for OIDC.
- The authentication is fully automated in the CI pipeline via the `mcp-publisher login github-oidc` command.
- The workflow in `.github/workflows/publish.yml` is already configured with the necessary permission:
  ```yaml
  permissions:
    id-token: write
  ```
  This permission allows the runner to acquire a temporary OIDC token from GitHub, which `mcp-publisher` uses to
  authenticate directly with the MCP Registry.

---

## Step 4: Tag and Release
Once PyPI Trusted Publishing is configured, trigger the automated release pipeline by tagging and pushing a release.

1. Ensure the version numbers in `pyproject.toml` and `server.json` are updated and match, and that `CHANGELOG.md` has
   an entry for the version dated the day of the tag (replace `Unreleased`).
2. Check that `server.json`, `pyproject.toml` and the README's `mcp-name` comment agree:
   ```bash
   uv run python scripts/check-release-consistency.py
   ```
3. Run the local test suite to verify everything is green:
   ```bash
   uv run pytest
   ```
   Do not use the short `-m` expression containing only `not live`: a command-line `-m` replaces `addopts` instead of
   narrowing it, silently re-enabling the `wheel`, `remote_live` and `sandbox` marker suites, which fail on a clean
   checkout.
4. Confirm `docs/TOOLS.md` is current (`uv run python scripts/generate_docs.py --check` exits 0; the default suite runs
   the same check).
5. Confirm the "Tests" GitHub Actions workflow is green on the exact commit being tagged. For example,
   `gh run list --workflow=Tests --branch=main --limit=1` must show success, and its commit SHA must match
   `git rev-parse HEAD`.
6. Fetch **every** URL in `[project.urls]` and confirm each one both returns 200 **and** actually resolves to the
   intended content:
   ```bash
   python3 -c "import tomllib;[print(v) for v in tomllib.load(open('pyproject.toml','rb'))['project']['urls'].values()]" \
     | while read -r url; do
         printf '%s -> %s\n' "$url" "$(curl -sL -o /dev/null -w '%{http_code}' "$url")"
       done
   ```
   A 200 is **not** sufficient. Open the response and confirm it names this project: a page can return 200 while
   silently discarding its query parameters and rendering an unrelated default listing. Read the body, or load the URL
   in a browser and look at what renders.

   Do this **before** tagging. `[project.urls]` is baked into the published PyPI metadata and is immutable once a
   version is released: a broken link can only be corrected by publishing a new version.
7. Create and push the version tag. It must match the version in `pyproject.toml` and `server.json`, prefixed with
   `v`; the workflows trigger on `tags: ["v*"]`:
   ```bash
   VERSION=$(python3 -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
   git tag "v$VERSION"
   git push origin "v$VERSION"
   ```
8. This triggers `.github/workflows/publish.yml`, which will:
   - Check release consistency and run the test suite.
   - Build the package and run the wheel end-to-end tests against it.
   - Publish the package to PyPI (using Trusted Publisher OIDC).
   - Authenticate to the MCP Registry (using `github-oidc`).
   - Wait for PyPI propagation, then publish the server metadata to the MCP Registry.

   The same tag triggers `.github/workflows/deploy.yml`, which redeploys the hosted server (see below).

---

## Step 5: Post-Publish Verification
After the GitHub Actions run completes successfully, verify the release:

1. **PyPI Verification**: Visit [pypi.org/project/evolution-api-mcp/](https://pypi.org/project/evolution-api-mcp/) and
   verify that the package is live and shows the correct version.
2. **MCP Registry Verification**: Query the MCP Registry API to verify the server is listed:
   ```bash
   curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.singleflo/evolution-api-mcp"
   ```
   Verify that the returned JSON contains the server metadata.
   PyPI and the MCP Registry can have a short CDN propagation delay after a fresh upload, so the registry can briefly
   return 404 (see documented MCP Registry issue #553). If the registry job fails immediately after a successful PyPI
   publish, re-run only the registry job; never bump the version.
3. **Clean Install Verification**: On a clean machine (or in a temporary environment), verify that the package can be
   executed directly via `uvx`:
   ```bash
   uvx evolution-api-mcp --version
   uvx evolution-api-mcp --list-toolsets
   ```
   Then run `uvx evolution-api-mcp` with the two variables set against a test instance and call `get_instance_status`.
   With the variables unset the server still starts, lists its tools, and answers a call with "Missing Evolution
   credentials".

---

## Hosted server (Coolify)

The hosted server at `https://evolution-mcp.singleflo.com` is deployed by Coolify from the repository's
`docker-compose.yaml` (repository mode: the compose has a `build:`, which only that mode supports). The compose file
carries no domain and no secret: the domain lives in Coolify's UI, and the required secrets are `${VAR:?}` references
that Coolify surfaces under **Configuration → Environment Variables** and refuses to deploy while empty. Steps in order:

1. **DNS**: create an **A record** `evolution-mcp` on `singleflo.com` pointing at the Coolify server's IP address, not a
   CNAME, unless the domain sits behind a CDN.
2. **Coolify**: **+ Add resource** → **GitHub repository** (the GitHub App source) → repository
   `singleflo/evolution-api-mcp`, branch `main`.
3. **Configuration → General**: set **Build Pack** to **Docker Compose** and **Docker Compose Location** to
   `docker-compose.yaml`.
4. Set the domain of the `mcp` service to `https://evolution-mcp.singleflo.com`.
5. **Configuration → Environment Variables**: set `EVOLUTION_REMOTE_PUBLIC_URL=https://evolution-mcp.singleflo.com`
   (typed explicitly: the OAuth issuer and the protected-resource metadata must equal the URL users visit, character
   for character, so it is never derived from a generated variable), generate `EVOLUTION_REMOTE_SECRET_KEY` with
   ```bash
   python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
   ```
   and keep a copy of it somewhere safe: losing it makes every stored instance token unreadable, so every user has to
   reconnect. Set `EVOLUTION_REMOTE_OPENAI_CHALLENGE`, `EVOLUTION_REMOTE_PUBLISHER`, `EVOLUTION_REMOTE_SUPPORT_EMAIL`
   and `EVOLUTION_REMOTE_ALLOWED_INTEGRATIONS` as wanted (all four are optional; the last one defaults to
   `WHATSAPP-BUSINESS`, which is what the store listings describe, so leave it unset on the public server).
6. **Advanced**: turn **Auto Deploy** on.
7. Press **Deploy** the first time and watch the deployment log.
8. **Keys & Tokens → API Tokens**: create a token with the **deploy** permission; **Webhooks → Deploy Webhook**: copy
   the webhook URL. Put both into the GitHub repository secrets `COOLIFY_API_TOKEN` and `COOLIFY_DEPLOY_WEBHOOK`;
   `.github/workflows/deploy.yml` then redeploys on every `v*` tag (and no-ops on forks that lack the secrets).
9. **After every deploy, check `https://evolution-mcp.singleflo.com/health` first.** A failing healthcheck does not
   block or roll back a Coolify deploy: Traefik simply drops the unhealthy container from routing and the site answers
   "No available server" instead of an error. Named volumes survive redeploys: the SQLite database (`remote.db`) and
   the published files persist across them. Bind mounts, if anyone adds one later, resolve against the service
   configuration directory on the Coolify server, not against the repository checkout, which is why the compose uses a
   **named volume** for `/data`.
10. **Post-deploy verification**, against the live domain after every deploy:
    ```bash
    curl https://evolution-mcp.singleflo.com/health
    ```
    must return the version JSON (`{"status":"ok","version":"…"}`) naming the version that was just deployed. An
    unauthenticated call to the endpoint
    ```bash
    curl -si -X POST https://evolution-mcp.singleflo.com/mcp
    ```
    must answer **401**, and its `WWW-Authenticate` header must carry
    `resource_metadata="https://evolution-mcp.singleflo.com/.well-known/oauth-protected-resource/mcp"`; that header is
    how a host discovers where to sign in, so a 401 without it breaks every client before the first sign-in. The
    protected-resource metadata itself
    ```bash
    curl https://evolution-mcp.singleflo.com/.well-known/oauth-protected-resource/mcp
    ```
    must return JSON whose `resource` equals the `/mcp` URL character for character, which is why
    `EVOLUTION_REMOTE_PUBLIC_URL` is typed explicitly in step 5 rather than derived. `bash scripts/remote_live_check.sh
    https://evolution-mcp.singleflo.com` runs these three checks. And when `EVOLUTION_REMOTE_OPENAI_CHALLENGE` is set,
    OpenAI's app review verifies it by fetching
    ```bash
    curl https://evolution-mcp.singleflo.com/.well-known/openai-apps-challenge
    ```
    which must return exactly the token stored in the variable, with no wrapper and no trailing content.
11. **End-to-end against a real Evolution**, once per release: with a test instance, run `scripts/remote_token.py` and
    `scripts/remote_smoke.py` against the live domain (see `docs/REMOTE.md`) and confirm the smoke run lists the tools and
    reports the instance status.

The origin `https://evolution-mcp.singleflo.com` can never change once a listing is published on OpenAI, so treat the
domain as permanent before the first submission.

---

## Store submissions

Neither store can be submitted by an automated worker. Both need the hosted server live and verified (the section
above), and both guides are in `docs/listing/`:

1. Prepare a live test instance for the reviewers: a `WHATSAPP-BUSINESS` instance and a chat whose owner agreed to
   receive test messages. Then replace the fixture data of the dossier test cases with the values actually observed
   (`docs/listing/README.md`).
2. Record the demo video the OpenAI guide asks for and place its URL where that guide says.
3. Follow `docs/listing/SUBMIT-CLAUDE.md` for the Claude connectors directory and `docs/listing/SUBMIT-OPENAI.md` for the
   OpenAI Plugins Directory. Every step marked "confirm on submission day" has to be checked against the live portal.
4. Publish on OpenAI only after approval, as the OpenAI guide describes.

`docs/listing/OTHER-DIRECTORIES.md` lists the further directories that accept the package or the hosted URL.
