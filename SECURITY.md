# Security Policy

DisasterMind is an autonomous disaster-coordination platform (cyclone/flood,
earthquake, and urban fire/collapse response). Because its outputs can drive
real-world dispatch and public alerting, we treat the integrity and availability
of the coordination loop and the Commander Dashboard as safety-relevant.

This document describes how to report vulnerabilities, the security controls that
ship today, and an honest threat-model summary of what is **not yet** done.

---

## Supported versions

| Version | Supported |
| ------- | --------- |
| `main` (latest) | yes |
| older tags | best-effort, no guarantee |

Security fixes land on `main` first. There is no long-term-support branch yet.

## Reporting a vulnerability

Please report security issues **privately** — do not open a public GitHub issue
for an unpatched vulnerability.

- Preferred: open a **GitHub Security Advisory** (private) on the repository.
- Email: `security@disastermind.example` (replace with the operator contact for
  your deployment).

Please include: affected component, a reproduction or proof-of-concept, the
impact you observed, and any suggested remediation. We aim to acknowledge within
**3 business days** and to provide a remediation timeline after triage. We will
credit reporters who request it once a fix is released.

Out of scope: findings that require physical access to a node, social
engineering of operators, or denial-of-service from a privileged
already-authenticated principal.

---

## Security controls shipping today

The runtime core is standard-library-only and **offline by default** (graceful
degradation), which minimizes the dependency attack surface. Optional network
capabilities (Kafka bus, Postgres/Timescale/Elasticsearch/MinIO storage, live
feeds, SMS dispatch) are opt-in via extras and configuration.

### Authentication (Commander Dashboard API)

- Bearer-token auth lives in `disastermind/security/auth.py` (`TokenStore`).
- Tokens are configured **only from the environment** so secrets never live in
  code or images:
  - `DM_API_KEYS` — comma/whitespace-separated bearer tokens (simple form).
  - `DM_API_KEYS_MAP` — `principal:token` pairs when you want named principals.
- **Default-open:** if no keys are configured the store is disabled and every
  route is reachable. This is intentional for local/offline development. **In any
  internet-reachable deployment you MUST set `DM_API_KEYS` (or the map form).**
- Tokens are accepted from the `Authorization` header or the `x-api-key` header.
  Token comparison is **constant-time** (`hmac.compare_digest`); only a short
  SHA-256 fingerprint of a token is ever logged, never the token itself.
- Open paths even when auth is enabled: `/`, `/index.html`, `/health`, `/docs`,
  `/openapi.json`, `/redoc` (UI + liveness + API docs).

### `/ws` WebSocket auth (`?token=` note)

Browsers cannot set an `Authorization` header on a `new WebSocket()` connection.
The dashboard therefore also accepts the token from the **query string**:

```
wss://host/ws?token=<API_KEY>      # or ?api_key=<API_KEY>
```

Security implication: a query-string token can leak into proxy/access logs and
browser history. Mitigations: always terminate TLS in front of `/ws` so the
query string is encrypted in transit; scope `/ws` tokens narrowly; rotate them;
and scrub `token`/`api_key` query params from any log pipeline. The auth guard is
a **pure-ASGI middleware** that covers *both* `http` and `websocket` scopes, so
the live stream is not left unauthenticated when auth is on.

### Authorization enforcement boundary

Auth + rate limiting are enforced by an ASGI security middleware mounted in
`api/server.py` (`_mount_security`). It is wired **inside** the CORS layer so
CORS preflight is answered before auth runs. The guard is inert unless API keys
are configured, so the default test/dev surface is unchanged.

### CORS

- Configured in `api/server.py` (`_mount_cors`) from `DM_CORS_ORIGINS`
  (comma-separated). Default is `*` for development convenience.
- `allow_credentials=False` — tokens travel in headers, never cookies, so a
  wildcard origin does not expose credentialed cross-site requests.
- **Production guidance:** set `DM_CORS_ORIGINS` to the explicit web-console
  origin(s). Do not ship `*` to production.

### Rate limiting

- Per-principal **token-bucket** limiter in `disastermind/security/ratelimit.py`.
- Burst `capacity` and sustained `refill_per_second` are env-tunable
  (`DM_RATE_CAPACITY`, `DM_RATE_REFILL_PER_SEC`). Defaults are permissive so the
  limiter never trips an existing deployment until explicitly tuned.
- On exhaustion the API returns **HTTP 429** with a `Retry-After` header; the
  `/ws` socket is closed with policy-violation code `1008`.
- In-memory only — see the gaps section for the multi-replica caveat.

### Decision audit integrity

- Every coordination decision is appended to a **hash-chained** decision log
  (`disastermind/audit/`), verifiable offline via
  `python -m disastermind verify-audit <path>` (also `make verify-audit`).
  Tampering with a past entry breaks the chain and is detectable.

### Container hardening

- **Multi-stage** image (`Dockerfile`): a `builder` stage installs into an
  isolated virtualenv; the slim `runtime` stage copies only the venv + package,
  so no build toolchain or pip cache ships in the final image.
- Runs as a **non-root** user (`uid 10001`).
- Base image is pinned to a specific tag for reproducible, scannable rebuilds.
- A `HEALTHCHECK` probes the API health endpoint using the stdlib (no extra
  tooling baked in).
- `.dockerignore` keeps secrets/tests/VCS out of the build context.
- `make sbom` produces a CycloneDX SBOM for supply-chain review.

### Secrets handling

- All credentials/DSNs/tokens come from environment variables (the `DM_*` keys);
  `.env` files are git-ignored and excluded from the image. Only `.env.example`
  (placeholders) is tracked.

---

## Threat model summary

**Assets:** the coordination loop's decision integrity, the audit chain, the
dashboard control plane (approve/reject escalations), and outbound dispatch /
alerting channels.

**Trust boundaries:** (1) untrusted internet -> dashboard API/WebSocket; (2)
external feed providers -> ingestion; (3) the app -> backing stores and dispatch
providers.

**Primary threats considered:** unauthorized control of escalations (mitigated by
token auth on all non-open routes incl. `/ws`); credential exposure (env-only
secrets, no token logging, constant-time compare); request flooding (per-principal
rate limit + circuit breakers / graceful shutdown in `ops/`); audit tampering
(hash-chained log); cross-origin abuse (CORS with non-credentialed wildcard,
tightened in prod); supply-chain risk (stdlib-only core, pinned base, SBOM,
non-root runtime).

---

## What is NOT yet done (known gaps)

These are explicit, accepted limitations — fix before a hardened production
rollout:

1. **TLS is not terminated by the app.** Run behind a TLS-terminating reverse
   proxy / platform router (Railway/Fly/k8s ingress). Without it, bearer tokens
   and `/ws?token=` are sent in cleartext.
2. **Default-open auth.** With no `DM_API_KEYS`, the API is fully open. There is
   no startup guard that *refuses* to serve when unauthenticated on a public bind
   — operators must configure keys themselves.
3. **No roles / RBAC.** A valid token is fully privileged; there is no per-action
   authorization (e.g. approve vs. read-only) beyond named principals.
4. **Rate limiting is per-process, in-memory.** Across multiple replicas each
   process has its own buckets, so the effective limit scales with replica count;
   there is no shared (e.g. Redis) limiter yet.
5. **No token rotation/expiry/revocation lifecycle.** Tokens are static env
   values; rotation means redeploying with new env and is operator-driven.
6. **Default CORS is `*`.** Convenient for dev, must be narrowed in prod.
7. **No mTLS / message signing between internal services** (bus, stores).
8. **No automated dependency / image CVE scanning gate** in CI yet (SBOM is
   generated but not policy-enforced).
9. **No audit-log encryption at rest or off-host shipping** — integrity is
   protected (hash chain), confidentiality and durability are deployment
   responsibilities.
10. **Ingestion input is trusted-ish.** Feed payloads are parsed defensively but
    there is no signature verification of upstream providers. The data-plane
    attack surface (spoofed feeds, sensor/social manipulation, suppression,
    replay) is modelled in full in [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md),
    along with the structural mitigation (no consequential action is autonomous)
    and the prioritised hardening steps.

---

## Hardening checklist for operators

- [ ] Set `DM_API_KEYS` (or `DM_API_KEYS_MAP`) — never run public + unauthenticated.
- [ ] Terminate TLS in front of the API and `/ws`.
- [ ] Set `DM_CORS_ORIGINS` to explicit web-console origin(s).
- [ ] Tune `DM_RATE_CAPACITY` / `DM_RATE_REFILL_PER_SEC` for your traffic.
- [ ] Scrub `token` / `api_key` query params from log pipelines.
- [ ] Run `make sbom` and review the dependency manifest before release.
- [ ] Periodically `make verify-audit` (or wire it into monitoring).
