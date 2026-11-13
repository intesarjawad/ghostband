# 0007: Keep the browser boundary on loopback with a per-run token

Status: accepted

## Context

The browser interface uploads audio, starts and stops extraction, drives
playback and downloads exports. These are private files and direct control of a
local audio process, so the conductor must not answer requests from other hosts
on the network. It also must not let a page from another origin drive the
session through the browser's existing connection.

`conductor/ghostband/cli.py` starts the application with
`web.run_app(..., host="127.0.0.1", port=7777)`, and
`conductor/ghostband/server.py` holds the request policy. A built interface is
served from `conductor/ghostband/ui_dist`; during development the Vite server
runs on a separate port.

## Decision

I bind the application to `127.0.0.1` only. `cli.py` generates one token per run
with `secrets.token_urlsafe(32)` and prints it in the address the page is opened
from, `http://127.0.0.1:7777/?t=<token>`. The interface reads that query
parameter and repeats the token in the `X-Ghostband-Token` header for `/api/*`
and in the `ghostband.<token>` WebSocket subprotocol for `/ws`.
`security_policy` compares both with `hmac.compare_digest`. `/` and
`/assets/*` do not require the token; the page must load before it can send one.

Every request must carry `Host` set to `127.0.0.1:7777` or `localhost:7777`, and
a request that carries `Origin` must use one of those two origins. Anything else
receives `403`; a missing or wrong token receives `401`. This rejects a page
from another origin and a request whose `Host` names a foreign host, which is
how a re-bound name would reach loopback.

I allow one extra development origin only through `--dev-origin` together with
`GHOSTBAND_DEV=1`. That origin must be HTTP on `localhost` or `127.0.0.1` with
an explicit port and no path, query, fragment or credentials. It is added as
one more allowed `Host` and `Origin`; it does not relax the token requirement.

Uploads are written to a temporary directory created by `create_app`, under a
random identifier that the client uses afterwards. Only the extension of the
submitted name is kept for the decoder, and the submitted name is used for
display. Bodies above `MAX_UPLOAD_BYTES` (200 MiB) are rejected from
`Content-Length` and again while the file is written, and the partially written
file is removed. The directory is deleted when the application shuts down;
imported workspaces and the recorded take live inside it too.

I reject an unauthenticated loopback listener: any local process, and any page
that can reach `127.0.0.1:7777`, could then start playback or read an export.
I reject binding to `0.0.0.0` or a LAN address because the use case is one Mac.
I reject a cookie-based session because it adds CSRF handling and storage that a
per-run URL token avoids. I reject reading the token from a file or an
environment variable because it would survive the run and belong to the
environment rather than to one server instance. I reject TLS because the traffic
never leaves the machine and a self-signed certificate would add a browser
warning without protecting that segment.

## Consequences

The token appears in the terminal output, in the browser address and in its
history. Anyone who can read the address or inspect the page can act on the
session for as long as the server runs; each new run gets a new token. Loopback
is not a boundary against other processes or users on the same Mac. The `Host`
and `Origin` checks defend against another origin; they are not a substitute for
the token.

Uploaded files exist until the run ends. The 200 MiB limit applies to this
boundary and is repeated for CLI imports. The policy matrix is pinned in
`conductor/tests/test_server_security.py`.

I have not tested this boundary against a hostile local process or performed a
penetration test, and I make no claim that the development-origin path is safe
outside a trusted workstation.
