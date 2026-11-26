# Design

<!-- impeccable:design-schema 1 -->

Atlas Dashboard — graphite and amber. An instrument panel, not a showcase.

## World

The visual reference is professional infrastructure tooling: a rack console, a
network appliance UI, a terminal multiplexer. The surface is a dark graphite
ground with hairline rules doing the work that shadows and gradients do
elsewhere. Colour is a signal, not decoration: exactly one accent (amber) marks
focus, the active navigation item, the primary action, and the single most
important figure on a screen. Everything else is a neutral ramp.

There are no gradients in this system. No glow. No coloured drop shadows. No
illustration. Elevation is expressed as a border and a step in the surface ramp.

Physical scene: one operator, alone, at a desk or on a phone over a remote
connection, often at night, frequently glancing rather than reading. Dark is
forced by that scene, not chosen as a style.

## Colour

| Token | Value | Role |
|---|---|---|
| `--bg` | `#0d0f11` | Page ground |
| `--surface` | `#14171a` | Cards, sidebar, tables |
| `--surface-2` | `#1a1e22` | Hover, inputs, secondary chips |
| `--surface-3` | `#22272c` | Active nav, segmented control |
| `--border` | `#2c3238` | Strong rule |
| `--border-soft` | `#23282d` | Default hairline |
| `--text` | `#e8eaed` | Primary text (15.9:1 on bg) |
| `--text-2` | `#a8b0b8` | Secondary text (8.8:1) |
| `--text-3` | `#7e878f` | Tertiary, labels (4.9:1 on surface) |
| `--accent` | `#e8a33d` | The only accent (8.9:1 on bg) |
| `--ok` | `#6cab80` | Healthy (6.7:1) |
| `--warn` | `#cbaa55` | Cooling, degraded (8.1:1) |
| `--bad` | `#cc7770` | Suspended, failed (5.5:1) |
| `--info` | `#6b8ba8` | Neutral provider badge |

Semantic colours are desaturated and always paired with a text label — colour
never carries meaning alone. Status is also written as a word (`HEALTHY`,
`COOLING`, `SUSPENDED`) inside the badge.

## Type

`Inter` for UI, `JetBrains Mono` for every number, key, fingerprint, log line
and identifier. Tabular numerals are on for all mono text so columns align.

Scale is compressed: 17px page title, 13px body, 12px table, 10.5px uppercase
labels with 0.07em tracking. No display sizes. The largest number on any screen
is 21px, in the metric strip.

## Space and shape

4px base scale. Radius is 4px for controls, 6px for cards and modals, 8px for
the largest container. Nothing is pill-shaped except status badges. Cards are
`--surface` with a `--border-soft` hairline and 16px padding.

## Component language

- **Metric strip** — one ruled row of four cells divided by hairlines. Replaces
  the conventional row of floating stat cards.
- **Status line** — a full-width bar with a 2px amber left rule, a coloured dot,
  a status word, and mono metadata. Replaces the gradient hero banner.
- **Table** — sticky uppercase header, 6px cell padding, hairline row rules,
  hover raises to `--surface-2`, numeric cells right-aligned mono.
- **Badge** — 10.5px mono uppercase, 1px border in the semantic colour at 45%
  alpha. Text sits on the card surface, never on the tint: tinted-on-tinted
  measured 1.2:1, which is unreadable. The border carries the colour.
- **Button** — 5px/12px padding, 4px radius. Primary is solid amber with dark
  text. Danger is a red wash. Ghost is transparent.
- **Sparkline** — inline SVG polyline, 1.25px stroke, `non-scaling-stroke`. No
  chart library ships.
- **Bar** — a 6px inline flex distribution with a legend. Used for keypool
  health and model share.
- **Log viewer** — mono, 3-column grid (time, level, message), level-tinted rows.
- **Modal** — centred, 460px, scrim at 60% black, Escape closes.

## Motion

Two transitions only: 120ms background/border on interactive elements, 180ms
sidebar slide on mobile. A 1.6s opacity shimmer on skeletons. Everything is
disabled under `prefers-reduced-motion`.

## Ban list

Gradients. Glow. Coloured shadows. Rounded-card grids. Oversized headings.
Decorative illustration. Chart libraries. More than one accent. Any hex value
outside `tokens.css`.