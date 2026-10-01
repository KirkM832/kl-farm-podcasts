#!/usr/bin/env python3
"""Upload episode MP3s to the Internet Archive; maintain enclosure-overrides.json.

Idempotent: skips any episode GUID already present in enclosure-overrides.json.
Designed to run in GitHub Actions (ia-upload.yml) with IA_ACCESS_KEY and
IA_SECRET_KEY in the environment. Also runnable locally for testing.

One archive.org item per show; files are named <episode-guid>.mp3.
Public download URLs look like:
    https://archive.org/download/<item-id>/<episode-guid>.mp3
and never expire, which is what Apple Podcasts requires of enclosures.

The daily feeds.yml workflow rebuilds the RSS feeds with build-feeds.py, which
rewrites each item's enclosure URL from enclosure-overrides.json when present.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

PAGES = "https://KirkM832.github.io/kl-farm-podcasts"

# feed slug -> (archive.org item identifier, human-readable show title)
SHOWS = {
    "today-with-kirk": ("kl-farm-today-with-kirk", "Today with Kirk"),
    "learning-under-pressure": ("kl-farm-learning-under-pressure", "Learning Under Pressure"),
    "before-cullman": ("kl-farm-before-cullman", "Before Cullman"),
    "the-spin-cycle": ("kl-farm-the-spin-cycle", "The Spin Cycle"),
    "lantern-stories": ("kl-farm-lantern-stories", "Lantern Stories"),
    "big-sister-energy": ("kl-farm-big-sister-energy", "Big Sister Energy"),
    "how-we-got-here": ("kl-farm-how-we-got-here", "How We Got Here"),
    "one-damn-thing-after-another": ("kl-farm-one-damn-thing-after-another", "One Damn Thing After Another"),
    "the-workshop": ("kl-farm-the-workshop", "The Workshop"),
}

CREATOR = "Kirk Moore"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
COLLECTION = "opensource_audio"


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def safe_filename(guid):
    return re.sub(r"[^A-Za-z0-9_-]", "_", guid) + ".mp3"


def ascii_header(s):
    return "".join(c if 32 <= ord(c) < 127 else "?" for c in (s or ""))[:200]


def ia_put(identifier, filename, filepath, title, access, secret):
    """PUT one file to the Internet Archive via its S3 API. Returns HTTP code."""
    url = "https://s3.us.archive.org/%s/%s" % (identifier, filename)
    # NOTE: secrets travel in argv here; GitHub-hosted runners are single-tenant
    # per job, and the keys live in repo Secrets otherwise. Acceptable here.
    cmd = [
        "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
        "--max-time", "900", "--retry", "2",
        "-X", "PUT",
        "-H", "Authorization: LOW %s:%s" % (access, secret),
        "-H", "x-archive-meta-title: %s" % ascii_header(title),
        "-H", "x-archive-meta-creator: %s" % CREATOR,
        "-H", "x-archive-meta-mediatype: audio",
        "-H", "x-archive-meta-collection: %s" % COLLECTION,
        "-H", "x-archive-meta-licenseurl: %s" % LICENSE_URL,
        "--data-binary", "@%s" % filepath,
        url,
    ]
    r = sh(cmd)
    return r.stdout.strip()


def ia_head_ok(identifier, filename, tries=8):
    """Confirm the public download URL serves the file (allows IA a little time)."""
    url = "https://archive.org/download/%s/%s" % (identifier, filename)
    for _ in range(tries):
        r = sh(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                "-I", "--max-time", 30, url])
        if r.stdout.strip() == "200":
            return True
        time.sleep(15)
    return False


def main():
    access = os.environ.get("IA_ACCESS_KEY", "")
    secret = os.environ.get("IA_SECRET_KEY", "")
    if not access or not secret:
        print("IA_ACCESS_KEY / IA_SECRET_KEY not set", file=sys.stderr)
        return 2
    here = os.path.dirname(os.path.abspath(__file__))
    ov_path = os.path.join(here, "enclosure-overrides.json")
    overrides = {}
    if os.path.exists(ov_path):
        with open(ov_path) as f:
            overrides = json.load(f)

    changed = False
    for slug, (identifier, show_title) in SHOWS.items():
        try:
            raw = urllib.request.urlopen("%s/%s.xml" % (PAGES, slug), timeout=60).read()
        except Exception as e:
            print("[%s] feed fetch failed: %s" % (slug, e))
            continue
        try:
            channel = ET.fromstring(raw).find("channel")
        except Exception as e:
            print("[%s] feed parse failed: %s" % (slug, e))
            continue
        if channel is None:
            continue
        for item in channel.findall("item"):
            guid_el = item.find("guid")
            guid = guid_el.text.strip() if guid_el is not None and guid_el.text else None
            if not guid or guid in overrides:
                continue
            enc = item.find("enclosure")
            if enc is None or not enc.get("url"):
                continue
            title = item.findtext("title") or guid
            print("[%s] uploading %s (%s) ..." % (slug, guid, title[:60]), flush=True)
            tmp = "/tmp/%s.mp3" % re.sub(r"[^A-Za-z0-9_-]", "_", guid)
            r = sh(["curl", "-sL", "--max-time", "900", "-o", tmp, enc.get("url")])
            if r.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 1024:
                print("[%s] download failed for %s" % (slug, guid))
                continue
            filename = safe_filename(guid)
            code = ia_put(identifier, filename, tmp,
                          "%s: %s" % (show_title, title), access, secret)
            try:
                os.remove(tmp)
            except OSError:
                pass
            if code != "200":
                print("[%s] IA PUT failed for %s: http %s" % (slug, guid, code))
                continue
            if not ia_head_ok(identifier, filename):
                print("[%s] IA verify failed for %s" % (slug, guid))
                continue
            overrides[guid] = "https://archive.org/download/%s/%s" % (identifier, filename)
            changed = True
            print("[%s] OK -> %s" % (slug, overrides[guid]))

    if changed:
        with open(ov_path, "w") as f:
            json.dump(overrides, f, indent=1)
            f.write("\n")
        print("enclosure-overrides.json updated (%d entries)" % len(overrides))
    else:
        print("nothing new to upload")
    return 0


if __name__ == "__main__":
    sys.exit(main())
