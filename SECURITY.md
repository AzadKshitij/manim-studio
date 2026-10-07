# Security Policy

## Supported versions

Manim Studio is pre-1.x in spirit despite the 1.1.x version line; only the latest
release receives security fixes.

| Version | Supported |
| ------- | --------- |
| 1.1.x   | ✅        |
| < 1.1   | ❌        |

## Reporting a vulnerability

Please report suspected vulnerabilities **privately** via GitHub Security
Advisories:

👉 https://github.com/azadk/manim-studio/security/advisories/new

Do not open a public issue for a security problem. Include:

- affected version (`manim-studio --version`),
- platform and how you run the app (portable zip, installer or source),
- a minimal reproduction (scene file + steps),
- the impact you believe is possible.

We aim to acknowledge reports within a few days and will coordinate a fix and a
disclosure timeline with you.

## Threat model notes

Manim Studio is a **local desktop editor that runs the scene code you give it**.
That code is Python and is executed by design — it is not a sandbox. The
security-relevant surface is therefore:

- **Scene execution** — running an untrusted scene executes arbitrary Python.
  Only open scenes you trust.
- **The preview-host IPC channel** — a loopback TCP/JSON-line protocol between
  the Qt shell and the companion `preview_host` process. See
  [`docs/protocol.md`](docs/protocol.md).
- **LaTeX provisioning** — the on-demand TinyTeX installer downloads pinned
  release bundles over HTTPS and verifies a SHA-256 checksum before unpacking
  (see [`docs/latex.md`](docs/latex.md)).
- **Subprocess export** — video export shells out to `manimgl -w`.

Reports about the on-demand download/checksum path, the IPC socket, or the
frozen bundle's binary handling are especially welcome.
