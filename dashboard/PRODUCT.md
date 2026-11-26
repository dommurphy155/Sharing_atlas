# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The single operator of a personal Atlas installation — a self-hosted AI
infrastructure proxy. They are technical, they already administer the box from
the terminal, and they open the dashboard when the terminal is the wrong tool:
bulk key work, reading a live traffic picture, or changing configuration
without remembering exact env var names.

## Product Purpose

Atlas is a multi-provider LLM translation proxy with a large rotating keypool.
The dashboard is the visual control centre for that installation: keypool
health, model and provider routing, live traffic, historical analytics,
configuration, service lifecycle, logs, and account security. Success is the
operator resolving an infrastructure question or making a change in seconds
that previously took a log grep and a text editor.

## Positioning

The dashboard is a second interface over the same management layer the CLI
uses — not a parallel implementation. Every value shown is read from the
running proxy or the same files the CLI writes, and every mutation goes through
the same code path the CLI uses. The CLI keeps working with the dashboard
stopped, and the dashboard keeps working with the proxy stopped.

## Operating Context

- Atlas proxy runs as `atlas-proxy.service` on port 8788; dashboard runs as
  `atlas-dashboard.service` on 8080.
- Keypool is a flat text file of `sk-` prefixed keys, one per line.
- Runtime provider/model selection lives in `data/proxy_data/runtime_provider.json`.
- Proxy emits a human-readable structured log at `proxy/logs/atlas-proxy.log`
  with per-request lines carrying a short request id, key index, status, and
  model.
- The operator is frequently on a phone, over a remote connection, on a
  hostile network.

## Capabilities and Constraints

- Read-only live state: proxy health, keypool summary and per-key detail,
  in-flight concurrency.
- Mutations: key import/removal/retirement, provider and model selection,
  service start/stop/restart, dashboard password and sessions.
- Historical analytics must be sampled over time; there is no upstream
  analytics API.
- Request and response bodies must never be retained or surfaced by default.
- Raw API keys must never reach the browser.
- Configuration is env-var driven; the dashboard may only change what the
  proxy actually reads at runtime, and must back up before writing.

## Brand Commitments

Name is Atlas. Existing CLI voice is terse and operational. No marketing
surface exists and none is wanted.

## Evidence on Hand

Real running system: 1170-key pool, live proxy on 8788, real request log with
per-request status and latency, real systemd units. No fabricated statistics
are permitted anywhere in the product.

## Product Principles

1. One source of truth — the dashboard reads and writes what the CLI reads and writes.
2. Never fabricate a number; show an em dash before showing a lie.
3. The proxy's survival is independent of the dashboard's.
4. Credential material is masked by default and never logged.
5. Density over decoration — the operator is here to work, not to admire.

## Accessibility & Inclusion

WCAG 2.2 AA: 4.5:1 body contrast, visible focus, full keyboard operation,
no meaning carried by colour alone, responsive to 360px.