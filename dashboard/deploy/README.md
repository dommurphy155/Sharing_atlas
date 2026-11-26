# Atlas Dashboard — deployment

Two ways in. The dashboard is an independent service and never sits in the
proxy's request path.

## 1. Direct (LAN / Tailscale)

```bash
DASHBOARD_HOST=0.0.0.0 DASHBOARD_PORT=8080 \
  /root/atlas/.venv/bin/python -m dashboard.api.main
```

Open `http://<host>:8080`. Plain HTTP — only for a trusted network.

## 2. HTTPS through nginx (production)

The box already terminates 443 for the Hermes dashboard with a certificate for
`SERVER_HOST`. Atlas mounts at `/atlas` under that same certificate: no new
DNS record, no new listener, no change to the existing site.

```bash
# 1. Bind the app to loopback (nginx is the only way in)
install -m 644 deploy/atlas-dashboard.service /etc/systemd/system/
systemctl daemon-reload
systemctl restart atlas-dashboard

# 2. Enable the site
ln -sf /etc/nginx/sites-available/hermes-dashboard \
       /etc/nginx/sites-available/atlas-dashboard   # merge the /atlas block in
nginx -t && systemctl reload nginx
```

Then: `https://SERVER_HOST/atlas/`

`deploy/nginx-atlas-dashboard.conf` is the reference. The `/atlas` location is
additive — the existing `location /` is untouched.

### Why loopback binding matters

With `DASHBOARD_HOST=127.0.0.1` the management API has no route from the
internet except nginx. That closes the "internal management API not directly
exposed" requirement in TASK.md §8 without a firewall change (ufw is currently
inactive on this box).

## Health and recovery

```bash
systemctl health atlas-dashboard      # unit state
curl -s localhost:8080/api/health     # app-level
journalctl -u atlas-dashboard -n 50
```

`Restart=always` with `RestartSec=3` handles crashes. The unit is deliberately
not ordered against `atlas-proxy`: the dashboard stays up when the proxy is
down, which is exactly when you need it.

## Backup and restore

```bash
atlas dashboard backup            # or: POST /api/system/backup (password + CSRF)
ls data/dashboard/backups/
```

A backup copies `.env`, `runtime_provider.json` and the key file to an
owner-only directory. Restore by copying the chosen directory back over
`data/` and restarting the dashboard. The key file is written `0600`.

## Updating

Updates are **detected and reported**, never applied from the dashboard:

```bash
git -C /root/atlas fetch --dry-run
git -C /root/atlas pull && atlas dashboard restart
```

Applying an update is left to the CLI so the backup and rollback path stays
yours.

## Not done

- No automated certificate renewal hook — certbot already manages the existing
  cert for this IP; a subpath needs nothing extra.
- No log rotation. The dashboard log grows with `journalctl` or
  `logrotate -f /etc/logrotate.d/atlas-dashboard` when you want it capped.