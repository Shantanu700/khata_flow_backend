# Production on a free Oracle Cloud server

One always-free Ampere (arm64) VM runs two containers:

- **api**: Django under uvicorn. It serves the REST API and the live-transcript WebSocket on one port that is not exposed to the internet.
- **caddy**: HTTPS on ports 80 and 443 with a free Let's Encrypt certificate, forwarding everything to `api`.

The database is Supabase (free) and photos and voice audio are in Cloudflare R2 (free). Nothing on the server needs backing up except `docker/prod/.env` and the three `*-migrations` volumes.

## 1. Accounts (once)

- **Oracle Cloud**: sign up with the home region **India West (Mumbai)** or **India South (Hyderabad)**. The home region cannot be changed later, and Always Free servers only run there. Oracle asks for a card to verify you; Always Free resources are not charged.
- **Supabase**: a new project in **South Asia (Mumbai)**. Copy the **Session pooler** connection string (Project > Connect). The direct connection is IPv6-only and the server is IPv4.
- **Cloudflare R2**: the bucket and token from `.env.example` in the repo root.
- **Hostname**: a free name at [duckdns.org](https://www.duckdns.org) (for example `khataflow.duckdns.org`), or your own domain.

## 2. The server (once)

1. Oracle console > Compute > Instances > Create instance:
   - Image: **Canonical Ubuntu 24.04** (aarch64).
   - Shape: **VM.Standard.A1.Flex**, sized within the Always Free allowance the console shows (2 OCPU and 12 GB is plenty).
   - Add your SSH public key. Keep the public IPv4 address it gets.
   - "Out of capacity": try again later or another availability domain; free Arm capacity in Mumbai comes and goes.
2. Open ports 80 and 443 in **two** places:
   - Console: the instance's subnet > Security list > Add ingress rules: source `0.0.0.0/0`, TCP, ports `80` and `443`.
   - On the server (Oracle's Ubuntu image blocks them in iptables too):
     ```bash
     sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
     sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
     sudo netfilter-persistent save
     ```
3. Point the hostname at the server's public IP (DuckDNS: paste the IP and press "update ip").
4. Install Docker and let your user run it:
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER   # log out and back in
   ```

## 3. Deploy

```bash
git clone https://github.com/Shantanu700/khata_flow_backend.git
cd khata_flow_backend
cp docker/prod/.env.example docker/prod/.env
nano docker/prod/.env        # fill in every value; chmod 600 it
docker compose -f docker/prod/docker-compose.yml up -d --build
docker compose -f docker/prod/docker-compose.yml logs -f api
```

The first start generates and applies the migrations, then prints `Uvicorn running`. Caddy fetches the certificate on the first request: open `https://<API_HOST>/api/v1/i18n/`.

Shops sign up through the web app. For the Django admin, create a superuser:

```bash
docker compose -f docker/prod/docker-compose.yml exec api python manage.py createsuperuser
```

## Updating

```bash
git pull
docker compose -f docker/prod/docker-compose.yml up -d --build
```

Each start runs `makemigrations` then `migrate`. The generated migration files live in the `shops-migrations`, `ledger-migrations` and `voice-migrations` volumes, so the next deploy migrates from them. **Never delete those volumes** (no `down -v`, no `volume prune`): without them, the next start would generate a fresh `0001_initial` that the database already counts as applied, and model changes would silently stop reaching the database.

## Good to know

- **Files must go to R2.** The container's own disk is replaced on every deploy, so leave the `R2_*` settings filled in.
- **Supabase free pauses a project after a week with no requests.** Any use of the app counts; a paused project is resumed from the Supabase dashboard.
- **Logs:** `docker compose -f docker/prod/docker-compose.yml logs --tail 100 api` (and `caddy`).
- Access logs are off on purpose: the live-transcript ticket travels in the WebSocket URL.
