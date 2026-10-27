"""Add app/waitlist HTTPS hosts without replacing existing production or test hosts."""

import subprocess
import urllib.request
from pathlib import Path


def main():
    for port, route in [(18001, "/api/v1/health/ready"), (13001, "/")]:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{route}", timeout=5) as response:
            assert response.status == 200
    request = urllib.request.Request(
        "http://127.0.0.1:18081/v1/waitlist",
        method="OPTIONS",
        headers={"Origin": "https://navox.net", "Access-Control-Request-Method": "POST"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200
    path = Path("/etc/caddy/Caddyfile")
    original = path.read_text()
    addition = ""
    if "app.navox.net {" not in original:
        assert "\n\nwww.navox.net" in original
        block = original.split("\n\nwww.navox.net", 1)[0]
        assert block.startswith("navox.net {") and "vox-test." not in block
        addition += "\n\n" + block.replace("navox.net {", "app.navox.net {", 1).replace(
            "127.0.0.1:3000", "127.0.0.1:13001"
        ).replace("127.0.0.1:8000", "127.0.0.1:18001")
    if "api.navox.net {" not in original:
        addition += """

api.navox.net {
 encode zstd gzip
 header {
  X-Content-Type-Options nosniff
  Referrer-Policy no-referrer
  Cache-Control no-store
 }
 handle /v1/waitlist {
  reverse_proxy 127.0.0.1:18081
 }
 handle {
  respond "Not found" 404
 }
}
"""
    if not addition:
        print("App and waitlist HTTPS hosts are already installed.")
        return
    backup = Path("/opt/navox/operations/landing-app-cutover-20261027/Caddyfile.before")
    if not backup.exists():
        backup.write_text(original)
        backup.chmod(0o600)
    candidate = path.with_name("Caddyfile.landing-candidate")
    candidate.write_text(original + addition)
    subprocess.run(
        ["caddy", "validate", "--config", str(candidate), "--adapter", "caddyfile"], check=True
    )
    if path.read_text() != original:
        raise RuntimeError("Caddy changed concurrently; inspect the new config before retrying")
    candidate.replace(path)
    try:
        subprocess.run(["systemctl", "reload", "caddy"], check=True)
    except subprocess.CalledProcessError:
        path.write_text(original)
        subprocess.run(["systemctl", "reload", "caddy"], check=True)
        raise
    print("App and waitlist hosts added; existing apex/www and Vox hosts retained.")


if __name__ == "__main__":
    main()
