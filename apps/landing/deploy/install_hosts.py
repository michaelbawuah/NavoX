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
    config = original + addition
    if "# landing-callback-bridge" not in original:
        marker = "\thandle /api/v1/assistant/* {"
        assert marker in config
        bridge = """
 # landing-callback-bridge: supports resolvers still caching the old apex IP.
 handle /api/v1/connections/google/callback {
  redir https://app.navox.net{uri} 307
 }
 handle /api/v1/connectors/canvas-lms/callback {
  redir https://app.navox.net{uri} 307
 }
"""
        config = config.replace(marker, bridge + marker, 1)
    if "# landing-static-cache-bridge" not in original:
        marker = "\thandle {\n\t\treverse_proxy 127.0.0.1:3000\n\t}\n}"
        assert marker in config
        bridge = """
 # landing-static-cache-bridge: serves the same Vercel site to old DNS caches.
 handle /_next/* {
  reverse_proxy 127.0.0.1:3000
 }
 handle {
  reverse_proxy https://navox-landing.vercel.app {
   header_up Host navox-landing.vercel.app
  }
 }
}"""
        config = config.replace(marker, bridge, 1)
    if config == original:
        print("App/waitlist hosts and callback compatibility routes are installed.")
        return
    backup = Path("/opt/navox/operations/landing-app-cutover-20261027/Caddyfile.before")
    if not backup.exists():
        backup.write_text(original)
        backup.chmod(0o600)
    candidate = path.with_name("Caddyfile.landing-candidate")
    candidate.write_text(config)
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
