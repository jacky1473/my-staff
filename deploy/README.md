# Deploying my-staff on a Linux host (Docker + systemd + nginx)

Container runs as a systemd-managed service bound to `127.0.0.1:5000` only;
nginx is the public-facing side on port 80.

## 1. Build the image

```bash
cd /path/to/my-staff
sudo docker build -t my-staff:latest .
```

## 2. Set up persistent data + secrets

```bash
sudo mkdir -p /var/lib/my-staff/data
sudo mkdir -p /etc/my-staff
sudo cp deploy/my-staff.env.example /etc/my-staff/my-staff.env
sudo nano /etc/my-staff/my-staff.env   # fill in SECRET_KEY, review FORCE_HTTP_COOKIES
sudo chmod 600 /etc/my-staff/my-staff.env
```

Generate a `SECRET_KEY`:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## 3. Install the systemd service

```bash
sudo cp deploy/systemd/my-staff.service /etc/systemd/system/my-staff.service
sudo systemctl daemon-reload
sudo systemctl enable --now my-staff
sudo systemctl status my-staff        # confirm it's running
sudo journalctl -u my-staff -f        # follow logs
```

The unit auto-restarts on failure and starts on boot. To redeploy after
building a new image: `sudo systemctl restart my-staff`.

## 4. Install the nginx reverse proxy

```bash
sudo cp deploy/nginx/my-staff.conf /etc/nginx/sites-available/my-staff.conf
sudo ln -s /etc/nginx/sites-available/my-staff.conf /etc/nginx/sites-enabled/
sudo nginx -t                          # verify syntax before reloading
sudo systemctl reload nginx
```

Edit `server_name` in that file to your real domain or the host's IP first.

## 5. Verify

```bash
curl -I http://<server-ip>/
```

Then browse to `http://<server-ip>/setup` to complete first-run setup.

## Notes / tradeoffs

- **Plain HTTP only, for now.** `FORCE_HTTP_COOKIES=true` in the env file
  disables the `Secure` flag on session cookies so login works without
  TLS. This is fine for an internal-only LAN/VPN deployment, but anyone
  who can observe traffic on that network segment can read session
  cookies and submitted passwords/PINs in cleartext. If this ever becomes
  reachable outside a trusted network, put TLS in front (e.g.
  `sudo certbot --nginx -d your-domain`) and remove `FORCE_HTTP_COOKIES`
  from the env file.
- **Single worker only.** The Dockerfile runs gunicorn with `--workers 1
  --threads 4`. The in-process attendance scheduler thread in `app.py`
  assumes exactly one process — running more workers would start
  duplicate schedulers racing on the same database. Scale vertically
  (more threads/CPU) rather than horizontally unless the scheduler is
  first moved to an external cron job or a leader-elected design.
- **Data persistence.** `/var/lib/my-staff/data` is bind-mounted to `/data`
  inside the container (matches `DB_PATH` in `app.py`), so the sqlite DB
  and backups survive `docker build`/container recreation. Back this
  directory up separately — this is app-level backup rotation, not a
  substitute for real backups off the host.
