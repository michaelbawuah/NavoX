"""Release only the waitlist, with a separate PostgreSQL role and database."""

import json
import os
import secrets
import subprocess
from pathlib import Path

ROOT = Path("/opt/navox/operations/landing-waitlist-20261027")
NAME = "navox-landing-waitlist-20261027"


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name]))[0]


def admin(sql):
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "navox-postgres-1",
            "sh",
            "-c",
            'exec psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" -d postgres -tAc "$1"',
            "sh",
            "SELECT 1",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError("PostgreSQL administrator connection unavailable")
    # SQL containing credentials travels through stdin and is never echoed.
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "navox-postgres-1",
            "sh",
            "-c",
            'exec psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" -d postgres',
        ],
        input=sql,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError("Waitlist database provisioning failed; inspect privately on the host")


def main():
    os.umask(0o077)
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    api = inspect("navox-api-1")
    network = next(iter(api["NetworkSettings"]["Networks"]))
    network_info = json.loads(subprocess.check_output(["docker", "network", "inspect", network]))[0]
    gateway = network_info["IPAM"]["Config"][0]["Gateway"]
    env_file = ROOT / "runtime.env"
    if not env_file.exists():
        password = secrets.token_urlsafe(48)
        # Fixed operator-owned identifiers; no public input reaches SQL.
        admin(
            f"CREATE ROLE navox_waitlist LOGIN PASSWORD '{password}';\n"
            "CREATE DATABASE navox_waitlist OWNER navox_waitlist;\n"
            "REVOKE ALL ON DATABASE navox_waitlist FROM PUBLIC;\n"
        )
        env_file.write_text(
            "APP_ENVIRONMENT=production\nWEB_ORIGIN=https://app.navox.net\n"
            f"DATABASE_URL=postgresql+asyncpg://navox_waitlist:{password}@postgres:5432/navox_waitlist\n"
            f"FORWARDED_ALLOW_IPS={gateway}\n"
        )
        env_file.chmod(0o600)
    source = ROOT / "source"
    if not source.exists():
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                "feat/landing-waitlist-20261027",
                "https://github.com/michaelbawuah/NavoX.git",
                str(source),
            ],
            check=True,
        )
    image = "navox-landing-waitlist:20261027"
    base = "navox-waitlist-base:" + api["Image"].split(":")[-1][:12]
    subprocess.run(["docker", "tag", api["Image"], base], check=True)
    subprocess.run(
        [
            "docker",
            "build",
            "-f",
            str(source / "services/api/Dockerfile.waitlist"),
            "--build-arg",
            "NAVOX_API_IMAGE=" + base,
            "-t",
            image,
            str(source / "services/api"),
        ],
        check=True,
    )
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            "--env-file",
            str(env_file),
            image,
            "uv",
            "run",
            "--no-sync",
            "python",
            "-m",
            "navox.api.waitlist_service",
        ],
        check=True,
    )
    exists = subprocess.run(["docker", "inspect", NAME], capture_output=True).returncode == 0
    if not exists:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                NAME,
                "--restart",
                "unless-stopped",
                "--network",
                network,
                "--env-file",
                str(env_file),
                "-p",
                "127.0.0.1:18081:8000",
                image,
            ],
            check=True,
        )
    print("Standalone waitlist deployed on loopback 18081 with a dedicated database.")


if __name__ == "__main__":
    main()
