# Atlas npm Distribution — Plan

This directory contains the read-only architecture assessment and implementation plan for distributing Atlas via npm.

## Documents

| File                              | Purpose                                      |
| --------------------------------- | -------------------------------------------- |
| `current-architecture.md`         | How Atlas works today (verified from source) |
| `npm-architecture.md`             | Proposed npm + Python hybrid architecture    |
| `cross-platform-compatibility.md` | Windows / Linux / macOS requirements         |
| `installation-workflow.md`        | End-to-end user journey                      |
| `repository-impact.md`            | Every file that would change, and why        |
| `risks-and-security.md`           | Risks, mitigations, update safety            |
| `testing-strategy.md`             | Per-platform test matrix                     |
| `implementation-roadmap.md`       | Phased, independently testable work          |

## Status

**PLAN ONLY.** No application files were modified. No dependencies installed. No services touched.

## TL;DR

- npm is a viable distribution layer; the Python app stays untouched.
- The npm package should **download a versioned source tarball from GitHub releases**, not bundle source.
- Only **one new directory** (`npm/`) and **zero existing-file changes** are strictly required.
- `postinstall` should NOT auto-run setup; print `atlas setup` instead.
- Updates must never touch `data/`, `.env`, or `.venv`.
