# Deploying my-staff (Jenkins + Docker + nginx)

**Jenkins is the deployment mechanism** — the `Jenkinsfile` in the repo
root builds the image, runs the container bound to `127.0.0.1:5000` only,
waits for it to respond, then installs/reloads the nginx config so port 80
serves the new version. nginx is the only thing reachable from outside the
host; the container itself is never exposed directly.

> The `systemd/` unit in this folder is an **alternative** for hosts that
> don't use Jenkins — don't run both at once. If Jenkins is your CI/CD,
> ignore `deploy/systemd/`; running it alongside Jenkins is what caused two
> separate containers (different names, different env vars) to fight over
> the same port in an earlier version of this setup.

## One-time setup on the Jenkins agent / target host

**1. Create the `SECRET_KEY` credential in Jenkins**

The Jenkinsfile pulls this from Jenkins Credentials — it is never stored in
the repo. In Jenkins: **Manage Jenkins → Credentials → System → Global
credentials → Add Credentials**
- Kind: `Secret text`
- ID: `staff-portal-secret-key` (must match exactly — the Jenkinsfile
  references this ID)
- Secret: output of `python3 -c "import secrets; print(secrets.token_hex(32))"`

> ⚠️ A previous version of the Jenkinsfile had a real `SECRET_KEY` hardcoded
> in plaintext, committed to git. That key is permanently compromised (it's
> in git history even after removal) — generate a **new** one for this
> credential, don't reuse the old value.

**2. Install nginx and Docker on the host** (if not already):
```bash
sudo apt install -y nginx docker.io   # or your distro's equivalent
sudo systemctl enable --now docker nginx
```

**3. Make sure the Jenkins agent user can run `docker`, `nginx -t`,
`systemctl reload nginx`, and write to `/etc/nginx/sites-available/`** —
either run the agent as root, or grant the Jenkins user sudo/docker-group
access as appropriate for your environment.

## Deploying

Just run the Jenkins job. Each build:
1. Builds `attendance-app:latest` from the repo
2. Stops/removes the previous `attendance-inst` container
3. Starts the new one bound to `127.0.0.1:5000`, with `SECRET_KEY` from
   the Jenkins credential and `FORCE_HTTP_COOKIES=true` (see tradeoff below)
4. Waits (up to 30s) for the container to answer `/login` or `/setup`
   before touching nginx, so nginx never points at a half-started container
5. Installs `deploy/nginx/my-staff.conf` and reloads nginx

## Verify

```bash
curl -I http://<server-ip>/
```

Then browse to `http://<server-ip>/setup` for first-run setup (skip if
already configured).

## Notes / tradeoffs

- **Plain HTTP only, for now.** `FORCE_HTTP_COOKIES=true` (set directly in
  the Jenkinsfile's `docker run`) disables the `Secure` flag on session
  cookies so login works without TLS. Fine for an internal-only LAN/VPN
  deployment; anyone who can observe traffic on that network segment can
  otherwise read session cookies and submitted passwords/PINs in
  cleartext. If this becomes reachable outside a trusted network, put TLS
  in front (e.g. `sudo certbot --nginx -d your-domain`) and remove
  `FORCE_HTTP_COOKIES` from the Jenkinsfile.
- **Single worker only.** The Dockerfile runs gunicorn with `--workers 1
  --threads 4`. The in-process attendance scheduler thread in `app.py`
  assumes exactly one process — running more workers would start
  duplicate schedulers racing on the same database. Scale vertically
  (more threads/CPU) rather than horizontally unless the scheduler is
  first moved to an external cron job or a leader-elected design.
- **Data persistence.** The named Docker volume `attendance_db_vol` is
  mounted to `/data` inside the container (matches `DB_PATH` in
  `app.py`), so the sqlite DB and backups survive redeploys. Back this up
  separately (`docker volume inspect attendance_db_vol` for its path on
  disk) — this is app-level backup rotation, not a substitute for real
  backups off the host.
- **First deploy on a fresh host**: nginx needs `sites-available` /
  `sites-enabled` to exist (default on Debian/Ubuntu). On RHEL/CentOS-style
  hosts without that convention, adjust `NGINX_CONF_DST` in the Jenkinsfile
  to drop the file directly in `/etc/nginx/conf.d/my-staff.conf` instead.

## Alternative: systemd instead of Jenkins

If you're not using Jenkins and want the container managed by systemd
directly instead, see `deploy/systemd/my-staff.service` and
`deploy/my-staff.env.example`. Do not run this alongside the Jenkins
pipeline — pick one.
