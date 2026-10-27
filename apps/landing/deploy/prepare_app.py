"""Stage the existing production app at new loopback ports, preserving its code/data.

Run on the VPS as its deployment operator. Runtime credentials stay in private
env files on that host and never enter source control or stdout.
"""

import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path("/opt/navox/operations/landing-app-cutover-20261027")
ORIGIN = "https://app.navox.net"
API_NAME = "navox-app-api-20261027"
WEB_NAME = "navox-app-web-20261027"


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name]))[0]


def private_env(path, values):
    if any("\n" in value or "\r" in value for value in values.values()):
        raise RuntimeError("Environment requires an operator-reviewed multiline transfer")
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    path.chmod(0o600)


def environment(container):
    return dict(item.split("=", 1) for item in container["Config"]["Env"])


def mounts(container):
    arguments = []
    for mount in container["Mounts"]:
        source = mount["Name"] if mount["Type"] == "volume" else mount["Source"]
        value = f"type={mount['Type']},source={source},target={mount['Destination']}"
        if not mount["RW"]:
            value += ",readonly"
        arguments.extend(["--mount", value])
    return arguments


def main():
    os.umask(0o077)
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    old_api, old_web = inspect("navox-api-1"), inspect("navox-web-1")
    network = next(iter(old_api["NetworkSettings"]["Networks"]))
    api_env = environment(old_api)
    web_env = environment(old_web)
    for key in ["WEB_ORIGIN", "NAVOX_ASSISTANT_ORIGIN", "NAVOX_PUBLIC_ORIGIN"]:
        if key in api_env:
            api_env[key] = ORIGIN
    api_env["WEB_ORIGIN"] = ORIGIN
    for key in ["NEXT_PUBLIC_API_BASE_URL", "NAVOX_PUBLIC_API_BASE_URL"]:
        if key in api_env:
            api_env[key] = ORIGIN + "/api/v1"
    web_env.update(
        NEXT_PUBLIC_API_BASE_URL=ORIGIN + "/api/v1",
        NAVOX_API_BASE_URL=f"http://{API_NAME}:8000/api/v1",
        NAVOX_ASSISTANT_ORIGIN=ORIGIN,
    )
    # Existing registered callback URLs stay valid through exact Vercel redirects.
    private_env(ROOT / "api.env", api_env)
    private_env(ROOT / "web.env", web_env)
    source = ROOT / "web-source"
    if not source.exists():
        source.mkdir()
        with (ROOT / "web-source.tar").open("wb") as archive:
            subprocess.run(
                [
                    "docker",
                    "exec",
                    "navox-web-1",
                    "tar",
                    "-C",
                    "/app",
                    "--exclude=node_modules",
                    "--exclude=.next",
                    "--exclude=.git",
                    "--exclude=.env*",
                    "--exclude=.venv",
                    "--exclude=__pycache__",
                    "-cf",
                    "-",
                    ".",
                ],
                stdout=archive,
                check=True,
            )
        subprocess.run(["tar", "-xf", str(ROOT / "web-source.tar"), "-C", str(source)], check=True)
    dockerfile = source / "apps/web/Dockerfile"
    allowed_args = re.findall(r"^ARG (\w+)", dockerfile.read_text(), re.MULTILINE)
    command = ["docker", "build", "-f", str(dockerfile), "-t", "navox-app-origin:20261027"]
    for key in allowed_args:
        if key.startswith("NEXT_PUBLIC_") and key in web_env:
            command.extend(["--build-arg", f"{key}={web_env[key]}"])
    command.append(str(source))
    subprocess.run(command, check=True)
    for name, port, image, values in [
        (API_NAME, "18001:8000", old_api["Image"], old_api),
        (WEB_NAME, "13001:3000", "navox-app-origin:20261027", old_web),
    ]:
        exists = subprocess.run(["docker", "inspect", name], capture_output=True).returncode == 0
        if not exists:
            env_file = ROOT / ("api.env" if name == API_NAME else "web.env")
            subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--restart",
                    "unless-stopped",
                    "--network",
                    network,
                    "-p",
                    "127.0.0.1:" + port,
                    "--env-file",
                    str(env_file),
                    *mounts(values),
                    image,
                ],
                check=True,
            )
    for port, route in [(18001, "/api/v1/health/ready"), (13001, "/")]:
        for attempt in range(30):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}{route}", timeout=3
                ) as response:
                    if response.status == 200:
                        break
            except OSError:
                if attempt == 29:
                    raise RuntimeError(f"Staged app did not become ready on port {port}") from None
                time.sleep(1)
    print(
        "Staged app/API are healthy on loopback 13001/18001. Existing production remains running."
    )


if __name__ == "__main__":
    main()
