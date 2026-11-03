#!/usr/bin/env python3
"""One-shot local setup for xiao-seat-vision (run from Git Bash in the repo root).

    python scripts/setup_local.py --host 172.16.139.140 --supabase-ref <project-ref>

Creates only what is missing — existing values are never overwritten:
  mqtt/certs/        dev CA + broker certificate (SAN = --host values)
  mqtt/secrets/      MQTT users: <device_id>, xiao-seat-vision-bridge, healthcheck
  .env               compose settings (bind address, healthcheck password, dashboard API URL)
  bridge/.env        bridge MQTT password, Supabase functions URL, bridge token
  supabase/.env.secrets   Edge Function secrets (bridge / dashboard viewer / admin tokens)
  firmware/xiao_seat_vision/config.h   MQTT URI, MQTT password, CA certificate, web token
                                       (Wi-Fi values are left untouched)

Secret values are written to these git-ignored files and are never printed.
"""

from __future__ import annotations

import argparse
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_H = ROOT / "firmware" / "xiao_seat_vision" / "config.h"
CONFIG_EXAMPLE = ROOT / "firmware" / "xiao_seat_vision" / "config.example.h"
PASSWD = ROOT / "mqtt" / "secrets" / "passwd"


def say(msg: str) -> None:
    print(f"[setup] {msg}")


def run_sh(*args: str) -> None:
    sh = shutil.which("sh") or shutil.which("bash")
    if not sh:
        sys.exit("needs 'sh' (run this from Git Bash on Windows)")
    subprocess.run([sh, *args], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)


def env_get(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    m = re.search(rf"^{re.escape(key)}=(.*)$", path.read_text(encoding="utf-8"), re.M)
    return m.group(1).strip() if m else ""


def env_set(path: Path, key: str, value: str, only_if_placeholder: bool = True) -> bool:
    """Set KEY=value. Keeps an existing real value (placeholders are replaced)."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    current = env_get(path, key)
    placeholder = current == "" or "change-me" in current or "generate-with" in current \
        or "your-project-ref" in current or "same-as" in current
    if current and only_if_placeholder and not placeholder:
        return False
    line = f"{key}={value}"
    if re.search(rf"^{re.escape(key)}=", text, re.M):
        text = re.sub(rf"^{re.escape(key)}=.*$", lambda _: line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
    path.write_text(text, encoding="utf-8")
    return True


def existing_users() -> set[str]:
    if not PASSWD.exists():
        return set()
    return {ln.split(":", 1)[0] for ln in PASSWD.read_text(encoding="utf-8").splitlines() if ":" in ln}


def ensure_user(name: str) -> str | None:
    """Create the MQTT user if missing. Returns the new password, or None if it existed."""
    if name in existing_users():
        return None
    pw = secrets.token_urlsafe(24)
    run_sh("mqtt/scripts/add-user.sh", name, pw)
    say(f"MQTT user '{name}' created")
    return pw


def config_define(text: str, key: str) -> str | None:
    m = re.search(rf'^#define\s+{key}\s+(R"PEM\(.*?\)PEM"|"[^"]*")', text, re.M | re.S)
    return m.group(1) if m else None


def config_set(text: str, key: str, literal: str) -> str:
    pattern = re.compile(rf'^#define\s+{key}\s+(R"PEM\(.*?\)PEM"|"[^"]*")', re.M | re.S)
    repl = f"#define {key} {literal}"
    if pattern.search(text):
        return pattern.sub(lambda _: repl, text, count=1)
    return text.rstrip("\n") + "\n" + repl + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", action="append", required=True,
                    help="LAN IP / hostname the ESP32 uses for the broker (repeatable; first = MQTT URI)")
    ap.add_argument("--supabase-ref", default="", help="Supabase project reference id")
    args = ap.parse_args()
    host = args.host[0]

    # 1. certificates
    if (ROOT / "mqtt/certs/private/ca.key").exists():
        say("certificates exist - kept (FORCE=1 sh mqtt/scripts/gen-dev-certs.sh ... to regenerate)")
    else:
        run_sh("mqtt/scripts/gen-dev-certs.sh", *args.host)
        say(f"certificates created (SAN: mosquitto, localhost, 127.0.0.1, {', '.join(args.host)})")
    ca_pem = (ROOT / "mqtt/certs/public/ca.crt").read_text(encoding="utf-8").strip()

    # 2. firmware config.h (device id decides the MQTT username)
    if not CONFIG_H.exists():
        shutil.copyfile(CONFIG_EXAMPLE, CONFIG_H)
        say("config.h created from config.example.h (fill in Wi-Fi!)")
    cfg = CONFIG_H.read_text(encoding="utf-8")
    device_id = (config_define(cfg, "XSV_DEVICE_ID") or '"xiao-01"').strip('"')

    # 3. MQTT users
    dev_pw = ensure_user(device_id)
    bridge_pw = ensure_user("xiao-seat-vision-bridge")
    hc_pw = ensure_user("healthcheck")
    dash_pw = ensure_user("xiao-seat-vision-dashboard")  # developer test mode (MQTT)
    feeder_pw = ensure_user("xiao-seat-vision-feeder")  # test video -> device (MQTT)

    # 4. config.h MQTT fields
    changed = []
    if config_define(cfg, "XSV_MQTT_URI") in (None, '""'):
        cfg = config_set(cfg, "XSV_MQTT_URI", f'"mqtts://{host}:8883"')
        changed.append("XSV_MQTT_URI")
    if dev_pw:
        cfg = config_set(cfg, "XSV_MQTT_PASSWORD", f'"{dev_pw}"')
        changed.append("XSV_MQTT_PASSWORD")
    elif config_define(cfg, "XSV_MQTT_PASSWORD") in (None, '""'):
        say(f"WARNING: MQTT user '{device_id}' exists but config.h has no password - "
            f"re-create it: sh mqtt/scripts/add-user.sh {device_id} <pw>")
    if ca_pem not in cfg:
        cfg = config_set(cfg, "XSV_MQTT_CA_CERT", f'R"PEM(\n{ca_pem}\n)PEM"')
        changed.append("XSV_MQTT_CA_CERT")
    if config_define(cfg, "XSV_WEB_TOKEN") in (None, '""'):
        cfg = config_set(cfg, "XSV_WEB_TOKEN", f'"{secrets.token_hex(12)}"')
        changed.append("XSV_WEB_TOKEN")
    CONFIG_H.write_text(cfg, encoding="utf-8")
    say(f"config.h updated: {', '.join(changed) or 'nothing to change'} (Wi-Fi untouched)")
    if config_define(cfg, "XSV_WIFI_SSID") in (None, '""'):
        say("NOTE: XSV_WIFI_SSID is empty in config.h - fill in Wi-Fi before uploading")

    # 5. root .env
    env = ROOT / ".env"
    if not env.exists():
        shutil.copyfile(ROOT / ".env.example", env)
    if hc_pw:
        env_set(env, "MQTT_HEALTHCHECK_PASSWORD", hc_pw, only_if_placeholder=False)
    if dash_pw:
        env_set(env, "DASHBOARD_MQTT_PASSWORD", dash_pw, only_if_placeholder=False)
    if feeder_pw:
        env_set(env, "FEEDER_MQTT_PASSWORD", feeder_pw, only_if_placeholder=False)
    env_set(env, "DEVICE_ID", device_id)
    # MQTT_BIND_ADDRESS stays 0.0.0.0 (works when DHCP changes the PC's IP); set it to
    # the LAN IP manually to avoid listening on other interfaces.
    if args.supabase_ref:
        env_set(env, "DASHBOARD_API_URL", f"https://{args.supabase_ref}.supabase.co/functions/v1/dashboard-api")
    say(".env ready")

    # 6. Supabase secrets (tokens shared with bridge/.env)
    sec = ROOT / "supabase" / ".env.secrets"
    if not sec.exists():
        shutil.copyfile(ROOT / "supabase" / ".env.example", sec)
    for key in ("BRIDGE_API_TOKENS", "DASHBOARD_VIEWER_TOKENS", "DASHBOARD_ADMIN_TOKENS"):
        env_set(sec, key, secrets.token_hex(32))
    bridge_token = env_get(sec, "BRIDGE_API_TOKENS").split(",")[0]
    say("supabase/.env.secrets ready (dashboard viewer/admin tokens are in this file)")
    # Dashboard token proxy (127.0.0.1 only): the page works without typing tokens.
    env_set(env, "DASHBOARD_VIEWER_TOKEN", env_get(sec, "DASHBOARD_VIEWER_TOKENS").split(",")[0])
    env_set(env, "DASHBOARD_ADMIN_TOKEN", env_get(sec, "DASHBOARD_ADMIN_TOKENS").split(",")[0])

    # 7. bridge/.env
    benv = ROOT / "bridge" / ".env"
    if not benv.exists():
        shutil.copyfile(ROOT / "bridge" / ".env.example", benv)
    if bridge_pw:
        env_set(benv, "MQTT_PASSWORD", bridge_pw, only_if_placeholder=False)
    env_set(benv, "BRIDGE_API_TOKEN", bridge_token)
    if args.supabase_ref:
        env_set(benv, "SUPABASE_FUNCTIONS_URL", f"https://{args.supabase_ref}.supabase.co/functions/v1")
    say("bridge/.env ready")

    say("done. Next: docker compose up -d --build  |  upload firmware  |  "
        "npx supabase secrets set --env-file supabase/.env.secrets")


if __name__ == "__main__":
    main()
