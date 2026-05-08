# Deployment

## Local development

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000`.

## Linux service

1. Copy the project to `/opt/software-cup-ops`.
2. Run `sudo bash deploy/install.sh`.
3. Copy `deploy/systemd.service` to `/etc/systemd/system/software-cup-ops.service`.
4. Run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now software-cup-ops
```

## Kylin Advanced Server V11 + LoongArch notes

- Prefer the OS-provided Python 3 package or a LoongArch-compatible Python build.
- Install Python wheels from sources compatible with LoongArch when binary wheels are unavailable.
- The backend command whitelist uses Linux commands such as `ps`, `ss`, and `systemctl`.
- Keep LLM access API-based in the first version to avoid local model dependency pressure on LoongArch.

## Least privilege

The install script creates a dedicated system user:

```bash
software-cup-agent:software-cup-agent
```

The systemd service runs as that user and writes only to:

- `/var/lib/software-cup-ops`
- `/var/log/software-cup-ops`
- `/opt/software-cup-ops/tmp`

The service uses `NoNewPrivileges=true`, an empty capability bounding set, and
systemd filesystem protections. Do not run the production service as root.
