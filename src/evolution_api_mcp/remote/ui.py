"""One document shell for every human-facing page of the hosted server.

The landing page, the three policy pages and the consent form share `layout()`, so the page that asks a
person for an instance token renders the same way on a phone (the in-app browser of Claude or ChatGPT) as on a
desktop.

Two rules this module exists to keep:

* **No external request.** The stylesheet is served by this same server from `pages/style.css`, the mark is an
  inline SVG data URI, and there is no web font and no third-party script: a page that asks for a credential must
  not also ask a CDN to watch it being typed. That is also what lets the Content Security Policy in `app.py` be
  as narrow as `default-src 'none'`. The one script on these pages is inline, short, and admitted by its own
  SHA-256 hash (`SCRIPT_HASH`), so the policy names that exact text rather than opening the page to scripts in
  general.
* **The stylesheet is cache-busted by version.** `/style.css?v=<version>` with a one-year immutable cache: a
  deploy changes the query, so nobody reads a new page through an old stylesheet.
"""

import base64
import hashlib
import html

from evolution_api_mcp import __version__

REPO_URL = "https://github.com/singleflo/evolution-api-mcp"
PRODUCT_NAME = "Evolution API Assistant"

# The consent POST opens a real connection to the user's own Evolution server and waits for it, up to twenty
# seconds (`consent._VERIFY_TIMEOUT`). A page that looks untouched for that long reads as broken and gets clicked
# again, so the form marks itself as sending and the pressed button grows a spinner. The guard on
# `dataset.sending` turns a second click into nothing rather than a second authorization request. Buttons are not
# disabled: a disabled submitter drops its own name and value from the body, which would silently turn a refusal
# into a blank submission. They are made unclickable in CSS instead. Without JavaScript the form still works; it
# just submits silently.
#
# A missing submitter (e.g. from a programmatic submit) would leave no button marked, so the script falls back to
# the primary button. The resolved button is marked busy for screen readers, rather than the form, because a
# role="status" region inside an aria-busy subtree may never be announced.
_PENDING_SCRIPT = (
    "document.addEventListener('submit',function(e){"
    "var f=e.target;"
    "if(f.dataset.sending){e.preventDefault();return;}"
    "f.dataset.sending='1';"
    "f.classList.add('is-sending');"
    "var b=e.submitter||f.querySelector('button.primary');"
    "if(b){"
    "b.classList.add('is-busy');"
    "b.setAttribute('aria-busy','true');"
    "if(b.dataset.busyLabel){b.textContent=b.dataset.busyLabel;}"
    "}"
    "});"
)

# What the Content Security Policy must name to admit the script above. Derived from the text itself, so editing
# one without the other is not a state this module can be left in.
SCRIPT_HASH = "'sha256-{}'".format(base64.b64encode(hashlib.sha256(_PENDING_SCRIPT.encode("utf-8")).digest()).decode())

# A flat monogram as vector, inline: no route, no file, no second request, and it survives a CSP that allows
# `img-src 'self' data:`. Letters only, on the same teal as the listing icon; no WhatsApp or Evolution artwork.
_MARK = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
    "%3Crect width='32' height='32' rx='7' fill='%23128c7e'/%3E"
    "%3Ctext x='16' y='21.5' text-anchor='middle' font-family='Helvetica,Arial,sans-serif'"
    " font-size='15' font-weight='700' fill='%23ffffff'%3EEA%3C/text%3E"
    "%3C/svg%3E"
)

_NAV = (("/", "Overview"), ("/privacy", "Privacy"), ("/terms", "Terms"), ("/support", "Support"))


def asset(path: str) -> str:
    """A same-origin asset URL carrying the version.

    `/style.css` answers with a year-long immutable cache, which is only safe because the version rides on the
    query: a deploy changes the URL, so nobody reads a new page through an old stylesheet.
    """
    return f"{path}?v={_version()}"


def layout(title: str, body: str, *, publisher: str, active: str | None = None, lead: str | None = None) -> str:
    """The full document for one page.

    `title` is both the `<title>` and the page's `<h1>`; `lead` is the one sentence under it (already HTML, the
    caller escapes what it interpolates). `active` is the nav path to mark as current, and is None on the consent
    page, which is reached from a client rather than from the navigation.
    """
    links = []
    for path, label in _NAV:
        current = ' aria-current="page"' if path == active else ""
        links.append(f'<a href="{path}"{current}>{html.escape(label)}</a>')
    nav = "".join(links)
    lead_html = f'<p class="lead">{lead}</p>' if lead else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>{html.escape(title)} — {PRODUCT_NAME}</title>"
        f'<link rel="icon" href="{_MARK}">'
        f'<link rel="stylesheet" href="{asset("/style.css")}">'
        "</head><body>"
        '<header class="site-header"><div class="header-inner">'
        f'<a class="brand" href="/"><img src="{_MARK}" alt="" width="28" height="28"><span>{PRODUCT_NAME}</span></a>'
        f'<nav class="site-nav" aria-label="Pages">{nav}</nav>'
        "</div></header>"
        f'<main class="page"><h1>{html.escape(title)}</h1>{lead_html}{body}</main>'
        f"{_footer(publisher)}"
        f"<script>{_PENDING_SCRIPT}</script>"
        "</body></html>"
    )


def _footer(publisher: str) -> str:
    """Publisher, links, and the trademark line.

    The trademark sentence is not decoration: this server works with WhatsApp through Evolution API without any
    relationship to Meta, WhatsApp or the Evolution API project, and a listing that lets a reader infer
    endorsement is the kind of claim a directory review rejects.
    """
    return (
        '<footer class="site-footer"><div class="footer-inner">'
        f"<p>Published by {html.escape(publisher)}. Open source under the MIT licence — "
        f'<a href="{REPO_URL}">source and documentation</a>.</p>'
        '<p class="trademark">WhatsApp and Meta are trademarks of their respective owners. This is an '
        "independent project: not affiliated with, endorsed or sponsored by Meta, WhatsApp or the Evolution API "
        "project.</p>"
        "</div></footer>"
    )


def _version() -> str:
    return html.escape(__version__)
