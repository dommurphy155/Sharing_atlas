Atlas — Full Personal Dashboard Development

Mission

Transform the existing Atlas project at ~/atlas into a polished, full-featured, remotely accessible personal AI infrastructure management platform.

Atlas must evolve from primarily a CLI-managed OpenRouter translation proxy into a complete visual control centre.

The existing CLI must remain fully functional. The dashboard is an additional interface, NOT a replacement or rewrite of Atlas.

The end result should feel like a professionally engineered, production-quality software product rather than a basic admin panel.

I want everything organised in one clean, intuitive interface. It should be visually impressive, fast, responsive, easy to navigate and genuinely useful for managing every aspect of Atlas.

⸻

1. SKILLS — LOAD BEFORE STARTING

Load and follow these skills:

* /impeccable
* /loop
* /workflow-authoring
* /ui-ux-pro-max:banner-design

Do not assume these are the only relevant skills.

Inspect the available skills and identify any additional ones relevant to:

* Dashboard and admin interface design.
* Frontend architecture.
* React and TypeScript.
* FastAPI and Python.
* Backend architecture.
* Authentication and authorisation.
* Application security.
* Database design.
* Responsive design.
* Accessibility.
* Testing and quality assurance.
* Deployment and DevOps.

Load the relevant skills before beginning development.

Use the skills as actual implementation guidance, not simply as a checklist.

⸻

2. FIRST: AUDIT THE EXISTING ATLAS CODEBASE

Before modifying anything, thoroughly inspect ~/atlas.

Understand the existing architecture, including:

* FastAPI application.
* Proxy implementation.
* OpenRouter keypool.
* Key rotation and selection strategies.
* Provider integrations.
* Model routing.
* Translation layer.
* Configuration management.
* CLI commands and runtime.
* Logging and diagnostics.
* Systemd integration.
* Existing tmux support.
* Installation and update procedures.
* Existing API endpoints.
* Environment variables and secrets handling.

Identify which existing functions can be reused by the dashboard.

Do not unnecessarily duplicate existing functionality.

Do not rewrite working components simply to accommodate the dashboard.

Produce a concise technical audit identifying:

1. Existing functionality.
2. Existing reusable backend services.
3. Missing functionality required by the dashboard.
4. Proposed architecture.
5. Potential compatibility risks.
6. Security risks.
7. Implementation phases.

After the audit, proceed with implementation unless a genuinely destructive or ambiguous decision requires clarification.

⸻

3. ARCHITECTURE — CRITICAL REQUIREMENTS

The dashboard must integrate with the existing Atlas installation.

Recommended architecture:

* Existing Atlas proxy remains responsible for AI traffic.
* Dashboard frontend provides the visual interface.
* Dashboard API provides authenticated administrative operations.
* Shared management services provide the underlying functionality.
* Existing CLI and dashboard use the same management services.
* Persistent storage handles dashboard users, sessions, audit records and historical analytics.

The architecture should look conceptually like this:

CLI ─────────────┐
│
Web Dashboard ───┼── Shared Atlas Management Layer
│              │
│       Existing Atlas Services
│              │
└──────── Existing Proxy
│
OpenRouter

Important principles:

* One source of truth for configuration.
* One underlying keypool implementation.
* No duplicated business logic.
* Changes made through the CLI must appear in the dashboard.
* Changes made through the dashboard must be reflected in the CLI.
* The dashboard must not become a dependency for normal proxy operation.
* The proxy must continue working if the dashboard crashes or is unavailable.
* The dashboard must remain accessible if the proxy is stopped or broken.

Keep the dashboard and proxy independently manageable.

Do not introduce unnecessary microservices or infrastructure.

Use the existing FastAPI backend wherever practical. A React and TypeScript frontend is preferred, with a modern component system and a consistent design language.

Choose supporting technologies based on the existing codebase rather than introducing dependencies without justification.

⸻

4. DASHBOARD DESIGN — VISUAL DIRECTION

I want a clean, sleek, modern dark interface.

Think premium infrastructure management software.

Design characteristics:

* Dark, sophisticated colour palette.
* Clean typography.
* Carefully balanced spacing.
* Consistent component sizing.
* Subtle borders and shadows.
* Restrained accent colours.
* Smooth, purposeful transitions.
* Excellent information hierarchy.
* Clear visual indicators for system health.
* Minimal clutter.
* Responsive layouts.
* No unnecessary decorative elements.

Avoid:

* Generic AI-generated dashboard aesthetics.
* Excessive gradients.
* Excessive rounded cards.
* Oversized headings.
* Unnecessary animations.
* Cluttered sidebars.
* Too many competing colours.
* Excessive empty space.
* Repetitive information.
* Fake statistics or decorative charts.

The interface should feel cohesive, intentional and professionally designed.

Use the loaded design skills to establish a consistent design system covering typography, spacing, colours, buttons, cards, tables, navigation, charts, forms, modals and notifications.

Navigation structure

Create a clean sidebar with the following sections:

Overview

Central dashboard displaying:

* Proxy health.
* Uptime.
* Active requests.
* Total requests.
* Keypool size.
* Healthy and unhealthy keys.
* Current default model.
* Current provider.
* Request throughput.
* Error rates.
* Latency.
* Recent activity.
* Important system alerts.

Keypool Management

Complete visual management of OpenRouter API keys.

Features:

* View all keys.
* Search and filter keys.
* Import keys.
* Remove keys.
* Enable and disable keys.
* Inspect individual key health.
* Display rate-limit information.
* Identify exhausted, invalid or failing keys.
* View key usage and request history.
* Bulk import and management.
* Key rotation controls.
* Keypool health statistics.

Never expose complete API keys unnecessarily. Mask sensitive values by default.

Models & Routing

Manage Atlas model and provider configuration.

Features:

* View available models.
* Change default model.
* Configure provider routing.
* Configure fallback models.
* Manage routing strategies.
* Inspect model availability.
* Configure model-specific settings where supported.
* View provider performance.
* Test model connectivity.

Live Monitoring

Provide real-time visibility into Atlas traffic.

Features:

* Incoming requests.
* Outgoing upstream requests.
* Request timestamps.
* Selected model.
* Provider.
* Response status.
* Request duration.
* Latency.
* Error information.
* Concurrency.
* Throughput.
* Request history.

Include a detailed request inspector.

Sensitive request and response content must not be exposed or retained by default. Provide deliberate, configurable diagnostic logging with appropriate redaction.

Analytics

Provide useful historical statistics:

* Requests per hour and day.
* Model usage.
* Provider usage.
* Token consumption.
* Estimated spending where reliable pricing data exists.
* Error rates.
* Average latency.
* Key utilisation.
* Historical performance.

Charts must use real Atlas data, not fabricated example statistics.

Configuration

Provide a visual interface for managing Atlas configuration.

Features:

* Proxy settings.
* Ports and bind addresses.
* Provider configuration.
* Model settings.
* Timeouts.
* Concurrency.
* Keypool settings.
* Routing strategies.
* Other existing supported configuration options.

Include:

* Configuration validation.
* Clear error messages.
* Change previews.
* Confirmation for sensitive changes.
* Backups before modifications.
* Rollback capability where practical.

Never silently overwrite existing configuration.

System Management

Provide complete visual system administration.

Features:

* Start Atlas.
* Stop Atlas.
* Restart Atlas.
* View current status.
* View process information.
* View systemd status.
* View tmux status where applicable.
* Inspect deployment configuration.
* View installed version.
* Check for updates.
* Perform updates with confirmation and backup.
* Run health checks.
* View diagnostics.
* Manage dashboard service independently.

The dashboard must never assume that Atlas is running under systemd if the existing installation uses another supervisor.

Logs & Diagnostics

Create a useful live log viewer.

Features:

* Live log streaming.
* Search.
* Filtering.
* Log levels.
* Timestamps.
* Error highlighting.
* Copyable diagnostic information.
* Historical logs where available.

Do not expose secrets, API keys, passwords or authentication tokens in logs.

Security & Account

Features:

* Change dashboard password.
* Manage active sessions.
* Logout.
* Revoke sessions.
* View security events.
* View administrative audit history.
* Configure session expiry.
* Manage security settings.

⸻

5. FIRST-TIME SETUP AND AUTHENTICATION

This is mandatory.

When someone first opens the dashboard, they must be presented with a secure initial setup screen.

The dashboard must NOT have a default username/password combination.

Initial setup should require the user to create their own administrator password.

Requirements:

1. First-time setup is available only while no administrator account exists.
2. Require a strong password.
3. Require password confirmation.
4. Validate password strength.
5. Store passwords using Argon2id or another appropriately configured modern password-hashing algorithm.
6. Never store plaintext passwords.
7. Prevent additional unauthenticated administrator registration after initial setup.
8. Require authentication before accessing dashboard functionality.
9. Protect against brute-force login attempts.
10. Use secure server-side sessions.
11. Use HTTP-only, Secure and appropriately configured SameSite cookies.
12. Implement CSRF protection.
13. Provide secure logout.
14. Provide session expiry and revocation.
15. Protect password changes and account operations.

Initial setup must be atomic and protected against simultaneous setup requests.

Do not allow an attacker to claim the initial administrator account remotely before the legitimate owner.

Consider requiring local access, a one-time setup token or an installation-generated secret for the initial setup process.

⸻

6. SECURITY — HIGHEST PRIORITY

The dashboard will control infrastructure containing valuable API credentials.

Treat it as a sensitive administrative application.

No application can honestly guarantee that it will never be hacked. The objective is to minimise attack surface, implement defence in depth and test security rigorously.

Mandatory requirements:

Network security

* HTTPS for remote access.
* Secure reverse-proxy configuration.
* Do not expose internal management endpoints publicly.
* Restrict backend administrative interfaces.
* Configure trusted proxy handling correctly.
* Avoid unrestricted CORS.
* Validate host headers.
* Use appropriate security headers.
* Do not expose unnecessary ports.

Application security

* Authentication and authorisation on every administrative endpoint.
* Input validation.
* Output encoding.
* Protection against SQL injection.
* Protection against XSS.
* CSRF protection.
* SSRF protections where relevant.
* Rate limiting.
* Secure error handling.
* No sensitive information in stack traces.
* No arbitrary command execution through user input.
* No unrestricted shell terminal exposed to the internet.

Secrets management

* Never expose OpenRouter API keys to frontend JavaScript.
* Never return raw credentials in API responses.
* Mask sensitive values.
* Protect environment files.
* Restrict filesystem permissions.
* Avoid secrets in logs.
* Ensure backups containing credentials are protected.

Administrative operations

* Require confirmation for destructive actions.
* Validate configuration before applying it.
* Back up configuration before risky changes.
* Audit sensitive operations.
* Protect update operations.
* Prevent unauthorised service manipulation.

Security testing

Perform appropriate testing for:

* Authentication bypass.
* Session hijacking.
* Brute-force attacks.
* Privilege escalation.
* CSRF.
* XSS.
* SQL injection.
* SSRF.
* Path traversal.
* Unauthorised API access.
* Secrets exposure.
* Unsafe command execution.
* Initial administrator setup abuse.

Do not claim the dashboard is secure simply because authentication works.

Document remaining risks and mitigations.

⸻

7. CLI — MUST REMAIN FULLY FUNCTIONAL

The existing Atlas CLI is important and must be preserved.

Do not remove, replace or break existing CLI functionality.

All existing commands must continue operating.

Where practical, add dashboard management commands:

* atlas dashboard start
* atlas dashboard stop
* atlas dashboard restart
* atlas dashboard status

Inspect the actual existing CLI before deciding on command names.

The dashboard should also be manageable independently of the proxy.

Both interfaces must operate on the same underlying Atlas configuration and management services.

Do not implement a second configuration system.

⸻

8. DEPLOYMENT AND REMOTE ACCESS

The dashboard must be accessible remotely through a browser on desktop, tablet and mobile.

Keep the existing proxy port and behaviour intact.

Use a separate dashboard listener.

For example, the intended arrangement is:

* Atlas proxy: existing production port 8788.
* Dashboard: separate port, such as 8790.
* Public HTTPS access: through a properly configured reverse proxy.
* Internal management API: not directly exposed to the internet.

These are proposed values, not permission to overwrite existing production settings without inspection.

Provide a secure deployment configuration.

The dashboard should:

* Run as an independent service.
* Start automatically where appropriate.
* Survive proxy restarts.
* Have its own logs.
* Have health checks.
* Support recovery after failures.
* Have a documented backup and restore process.

Do not disrupt existing production services during development.

⸻

9. DEVELOPMENT WORKFLOW

Work incrementally.

Phase 1: Audit

Understand the entire existing codebase and produce the technical plan.

Phase 2: Foundation

Create the dashboard architecture, authentication, initial setup, frontend shell and design system.

Phase 3: Core management

Implement keypool management, models, routing, configuration and proxy controls.

Phase 4: Monitoring

Implement live requests, logs, health monitoring and analytics.

Phase 5: System management

Implement lifecycle management, CLI integration, updates, backups and diagnostics.

Phase 6: Security and production

Complete security testing, deployment configuration, HTTPS, audit logging and recovery procedures.

At each phase:

* Test what has been implemented.
* Preserve existing behaviour.
* Check for regressions.
* Validate frontend and backend integration.
* Fix errors before proceeding.
* Keep documentation updated.

Use /loop for iterative improvement and testing where appropriate.

Do not endlessly redesign working components. Work towards clear acceptance criteria.

⸻

10. TESTING AND ACCEPTANCE CRITERIA

The project is not complete until all of the following are verified:

* Existing Atlas proxy works.
* Existing CLI works.
* Existing client integrations continue working.
* Dashboard initial password setup works.
* Unauthenticated access is rejected.
* Authentication persists correctly.
* Passwords are securely hashed.
* Remote HTTPS access works.
* Keypool management works.
* Model and routing management works.
* Configuration changes are validated.
* CLI and dashboard state remain synchronised.
* Proxy can be restarted without losing dashboard access.
* Dashboard can restart without interrupting proxy traffic.
* Live monitoring uses actual data.
* Logs do not expose secrets.
* Administrative operations are audited.
* Backup and recovery procedures work.
* Security tests pass.
* Mobile and desktop layouts work correctly.

Document any incomplete functionality honestly.

Do not present mock functionality as working functionality.

⸻

11. FINAL EXPECTATION

I want Atlas to become a proper personal AI infrastructure management platform.

The dashboard should be the central place where I can:

* Control my OpenRouter keypool.
* Manage models and providers.
* Configure routing.
* Monitor every aspect of proxy activity.
* Inspect requests and errors.
* View analytics.
* Manage configuration.
* Start, stop and restart services.
* Inspect logs.
* Update Atlas.
* Manage security.
* Administer the entire installation remotely.

All while retaining the existing CLI as a fully supported alternative.

Build something that looks and feels like a real software product.

Prioritise excellent UX, clean architecture, reliability, security and maintainability.

Do not sacrifice existing Atlas functionality to achieve the dashboard.

The final product should be visually polished, technically sound, secure by design and genuinely useful for daily administration.

#Clarification:
MAKE SURE YOU ARE USING THESE SKILLS, YOU DO NOT HAVE ACESS TO READ/LOOK AT IMAGES SO DONT EVEN TRY JUST BUILD BLIND BUT BUILD LIKE YOU MEAN IT ALWAYS USE THE IMPECCABLE SKILL 
