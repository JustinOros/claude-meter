import json
import os
import plistlib
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone

PACKAGES = ["pystray", "Pillow", "keyring", "curl_cffi"]
MODULES = ("pystray", "PIL", "keyring", "curl_cffi")


def ensure_deps():
    if getattr(sys, "frozen", False):
        return
    import importlib.util
    if all(importlib.util.find_spec(m) for m in MODULES):
        return
    venv = os.path.join(os.path.expanduser("~"), ".claude-meter", "venv")
    bindir = os.path.join(venv, "Scripts" if os.name == "nt" else "bin")
    py = os.path.join(bindir, "python.exe" if os.name == "nt" else "python")
    run_py = py
    if os.name == "nt" and os.path.basename(sys.executable).lower() == "pythonw.exe":
        run_py = os.path.join(bindir, "pythonw.exe")
    if os.path.abspath(sys.prefix) == os.path.abspath(venv):
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q"] + PACKAGES)
        return
    if not os.path.exists(py):
        print("Creating environment in " + venv)
        subprocess.check_call([sys.executable, "-m", "venv", venv])
    if subprocess.call([py, "-c", f"import importlib.util as u, sys; sys.exit(0 if all(u.find_spec(m) for m in {MODULES!r}) else 1)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
        print("Installing " + ", ".join(PACKAGES))
        subprocess.check_call([py, "-m", "pip", "install", "-q", "--upgrade", "pip"])
        subprocess.check_call([py, "-m", "pip", "install", "-q"] + PACKAGES)
    sys.exit(subprocess.call([run_py, os.path.abspath(__file__)] + sys.argv[1:]))


ensure_deps()

import pystray
from PIL import Image, ImageDraw, ImageFont

try:
    import keyring
except Exception:
    keyring = None

try:
    from curl_cffi import requests as creq
except Exception:
    creq = None

SERVICE = "ClaudeMeter"
ACCOUNT = "sessionKey"
FALLBACK = os.path.join(os.path.expanduser("~"), ".claude_meter_key")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
INTERVAL = 120
NORMAL = (204, 120, 92)
WARN = (232, 168, 0)
CRIT = (220, 50, 47)
GRAY = (120, 120, 120)
FONTS = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:\\Windows\\Fonts\\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "arialbd.ttf",
]


def load_key():
    if keyring:
        try:
            v = keyring.get_password(SERVICE, ACCOUNT)
            if v:
                return v
        except Exception:
            pass
    try:
        with open(FALLBACK) as f:
            return f.read().strip() or None
    except OSError:
        return None


def save_key(v):
    v = v.strip().replace("sessionKey=", "")
    if keyring:
        try:
            keyring.set_password(SERVICE, ACCOUNT, v)
            return
        except Exception:
            pass
    with open(FALLBACK, "w") as f:
        f.write(v)
    try:
        os.chmod(FALLBACK, 0o600)
    except OSError:
        pass


def instructions():
    if sys.platform == "darwin":
        keys = "Cmd+Option+I"
        extra = "   Safari only: first turn on Settings > Advanced > Show features for web developers.\n"
    else:
        keys = "F12 (or Ctrl+Shift+I)"
        extra = ""
    return (
        "ClaudeMeter needs your claude.ai login cookie to read your usage.\n\n"
        "1. Open https://claude.ai in your browser and make sure you are logged in.\n"
        f"2. Press {keys} to open Developer Tools.\n"
        + extra +
        "3. Go to the cookies list:\n"
        "   Chrome, Edge, Brave: Application tab > Cookies > https://claude.ai\n"
        "   Safari: Storage tab > Cookies\n"
        "   Firefox: Storage tab > Cookies > https://claude.ai\n"
        "4. Find the row named sessionKey and copy its Value (it starts with sk-ant-sid).\n"
        "5. Paste it below and click Save.\n\n"
        "Keep it private. It works like your password and is stored in your system keychain."
    )


MAC_SCRIPT = """on run argv
set r to display dialog (item 1 of argv) default answer "" with hidden answer with title "ClaudeMeter" buttons {"Cancel", "Open claude.ai", "Save"} default button "Save" cancel button "Cancel"
return (button returned of r) & linefeed & (text returned of r)
end run"""


def prompt_mac(msg):
    while True:
        r = subprocess.run(["osascript", "-e", MAC_SCRIPT, msg], capture_output=True, text=True)
        if r.returncode != 0:
            return None
        button, _, value = r.stdout.rstrip("\n").partition("\n")
        if button == "Open claude.ai":
            webbrowser.open("https://claude.ai")
            continue
        return value


def prompt_other(msg):
    try:
        import tkinter as tk
        from tkinter import simpledialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        v = simpledialog.askstring("ClaudeMeter", msg, show="*", parent=root)
        root.destroy()
        return v
    except Exception:
        import getpass
        print(msg)
        return getpass.getpass("sessionKey: ")


def prompt_key():
    msg = instructions()
    while True:
        v = prompt_mac(msg) if sys.platform == "darwin" else prompt_other(msg)
        if not v or not v.strip():
            return
        v = v.strip().replace("sessionKey=", "")
        if v.startswith("sk-ant-"):
            save_key(v)
            return
        msg = "That does not look like a sessionKey (it should start with sk-ant-sid). Try again.\n\n" + instructions()


class ApiError(Exception):
    def __init__(self, code, body, headers):
        self.code = code
        self.body = body or ""
        h = {k.lower(): v for k, v in (headers or {}).items()}
        self.cloudflare = "cf-mitigated" in h or "just a moment" in self.body.lower() or "challenge-platform" in self.body
        super().__init__(f"HTTP {code}")

    def label(self):
        if self.cloudflare:
            return f"Blocked by Cloudflare ({self.code})"
        if self.code == 401:
            return "Session key rejected (401), set a new one"
        if self.code == 403:
            return "Access denied (403), set a new session key"
        return f"Error {self.code}"


def api(path, key):
    url = "https://claude.ai/api" + path
    headers = {"Cookie": f"sessionKey={key}", "Accept": "application/json"}
    if creq:
        r = creq.get(url, headers=headers, impersonate="chrome", timeout=20)
        if r.status_code >= 400:
            raise ApiError(r.status_code, r.text, dict(r.headers))
        return r.json()
    req = urllib.request.Request(url, headers=dict(headers, **{"User-Agent": UA}))
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise ApiError(e.code, e.read().decode(errors="replace"), dict(e.headers))


def check():
    key = load_key()
    if not key:
        print("No session key saved. Run with --prompt-key")
        return
    print(f"Key: {key[:14]}... ({len(key)} chars)")
    print("HTTP client: " + ("curl_cffi (chrome)" if creq else "urllib"))
    try:
        orgs = api("/organizations", key)
        for o in orgs:
            print(f"Org: {o.get('name')} {o.get('uuid')} {o.get('capabilities')}")
        org = next((o for o in orgs if "chat" in (o.get("capabilities") or [])), orgs[0])
        print(json.dumps(api(f"/organizations/{org['uuid']}/usage", key), indent=2))
    except ApiError as e:
        print(e.label())
        print(e.body[:500])


def parse_time(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromisoformat(s.split(".")[0] + "+00:00")
        except ValueError:
            return None


def until(dt):
    if not dt:
        return "--"
    s = int((dt - datetime.now(timezone.utc)).total_seconds())
    if s <= 0:
        return "now"
    h, m = s // 3600, (s % 3600) // 60
    if h >= 24:
        return f"in {h // 24}d {h % 24}h"
    return f"in {h}h {m}m" if h else f"in {m}m"


def bucket(d):
    if not isinstance(d, dict):
        return 0.0, None
    return float(d.get("utilization") or 0), parse_time(d.get("resets_at"))


def font(size):
    for f in FONTS:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def make_icon(text, color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, 63, 63), radius=14, fill=color)
    size = 42 if len(text) <= 2 else 30
    f = font(size)
    try:
        d.text((32, 33), text, font=f, fill="white", anchor="mm")
    except (ValueError, TypeError):
        d.text((8, 20), text, font=f, fill="white")
    return img


class Meter:
    def __init__(self):
        self.key = load_key()
        self.org = None
        self.running = True
        self.wake = threading.Event()
        self.session_text = "Session (5h): --"
        self.weekly_text = "Weekly (7d): --"
        self.status_text = "Starting"
        self.icon = pystray.Icon(
            "ClaudeMeter",
            make_icon("--", GRAY),
            "Claude",
            menu=pystray.Menu(
                pystray.MenuItem(lambda i: self.session_text, None, enabled=False),
                pystray.MenuItem(lambda i: self.weekly_text, None, enabled=False),
                pystray.MenuItem(lambda i: self.status_text, None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Refresh", lambda: self.wake.set()),
                pystray.MenuItem("Open Usage Page", lambda: webbrowser.open("https://claude.ai/settings/usage")),
                pystray.MenuItem("Set Session Key...", lambda: threading.Thread(target=self.change_key, daemon=True).start()),
                pystray.MenuItem("Quit", self.quit),
            ),
        )

    def change_key(self):
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--prompt-key"]
        else:
            cmd = [sys.executable, os.path.abspath(__file__), "--prompt-key"]
        subprocess.run(cmd)
        self.key = load_key()
        self.org = None
        self.wake.set()

    def show(self, text, color, status, tooltip=None):
        self.icon.icon = make_icon(text, color)
        self.icon.title = tooltip or f"Claude: {status}"
        self.status_text = status
        self.icon.update_menu()

    def refresh(self):
        if not self.key:
            self.show("?", WARN, "No session key set")
            return
        try:
            if not self.org:
                orgs = api("/organizations", self.key)
                org = next((o for o in orgs if "chat" in (o.get("capabilities") or [])), orgs[0])
                self.org = org["uuid"]
            data = api(f"/organizations/{self.org}/usage", self.key)
        except ApiError as e:
            self.org = None
            self.show("!", WARN if e.code in (401, 403) else GRAY, e.label())
            return
        except Exception:
            self.org = None
            self.show("--", GRAY, "Network error")
            return
        s_used, s_reset = bucket(data.get("five_hour"))
        w_used, w_reset = bucket(data.get("seven_day"))
        s_left = max(0, 100 - round(s_used))
        w_left = max(0, 100 - round(w_used))
        shown = min(s_left, w_left)
        color = CRIT if shown <= 10 else WARN if shown <= 25 else NORMAL
        self.session_text = f"Session (5h): {s_left}% left, resets {until(s_reset)}"
        self.weekly_text = f"Weekly (7d): {w_left}% left, resets {until(w_reset)}"
        self.show(str(shown), color, f"Updated {datetime.now().strftime('%H:%M')}", f"Claude: {shown}% left")

    def loop(self, icon):
        icon.visible = True
        if not self.key:
            self.change_key()
        while self.running:
            self.refresh()
            self.wake.wait(INTERVAL)
            self.wake.clear()

    def quit(self):
        self.running = False
        self.wake.set()
        self.icon.stop()

    def run(self):
        self.icon.run(setup=self.loop)


LABEL = "com.justinoros.claudemeter"
HOME_DIR = os.path.join(os.path.expanduser("~"), ".claude-meter")
INSTALLED = os.path.join(HOME_DIR, "claude_meter.py")
MAC_PLIST = os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", LABEL + ".plist")
LINUX_DESKTOP = os.path.join(os.path.expanduser("~"), ".config", "autostart", "claudemeter.desktop")
WIN_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"


def runner():
    if os.name == "nt":
        w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if os.path.exists(w):
            return w
    return sys.executable


def install():
    os.makedirs(HOME_DIR, exist_ok=True)
    if os.path.abspath(__file__) != os.path.abspath(INSTALLED):
        shutil.copy2(os.path.abspath(__file__), INSTALLED)
    py = runner()
    if sys.platform == "darwin":
        uid = str(os.getuid())
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], capture_output=True)
        os.makedirs(os.path.dirname(MAC_PLIST), exist_ok=True)
        log = os.path.join(HOME_DIR, "claude-meter.log")
        with open(MAC_PLIST, "wb") as f:
            plistlib.dump({
                "Label": LABEL,
                "ProgramArguments": [py, INSTALLED],
                "RunAtLoad": True,
                "KeepAlive": {"SuccessfulExit": False},
                "ProcessType": "Interactive",
                "StandardOutPath": log,
                "StandardErrorPath": log,
            }, f)
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", MAC_PLIST], check=True)
        where = MAC_PLIST
    elif os.name == "nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "ClaudeMeter", 0, winreg.REG_SZ, f'"{py}" "{INSTALLED}"')
        subprocess.Popen([py, INSTALLED], creationflags=0x00000008 | 0x00000200)
        where = "HKCU\\" + WIN_RUN + "\\ClaudeMeter"
    else:
        os.makedirs(os.path.dirname(LINUX_DESKTOP), exist_ok=True)
        with open(LINUX_DESKTOP, "w") as f:
            f.write(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=ClaudeMeter\n"
                f'Exec="{py}" "{INSTALLED}"\n'
                "X-GNOME-Autostart-enabled=true\n"
            )
        subprocess.Popen([py, INSTALLED], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        where = LINUX_DESKTOP
    print(f"Installed to {INSTALLED}")
    print(f"Starts at login via {where}")
    print("ClaudeMeter is now running. Quit any other copy you started manually.")


def uninstall():
    if sys.platform == "darwin":
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
        if os.path.exists(MAC_PLIST):
            os.remove(MAC_PLIST)
    elif os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, "ClaudeMeter")
        except OSError:
            pass
    elif os.path.exists(LINUX_DESKTOP):
        os.remove(LINUX_DESKTOP)
    print("Removed from login. Quit the running copy from its menu if it is still open.")
    print(f"Files remain in {HOME_DIR} (delete that folder to remove everything, including the venv).")


if __name__ == "__main__":
    if "--install" in sys.argv:
        install()
        sys.exit(0)
    if "--uninstall" in sys.argv:
        uninstall()
        sys.exit(0)
    if "--prompt-key" in sys.argv:
        prompt_key()
    elif "--check" in sys.argv:
        check()
    else:
        Meter().run()
