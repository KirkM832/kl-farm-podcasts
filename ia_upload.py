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

Failure behavior (fixed Oct 1, 2026): the first pending episode is a canary.
If its download or upload fails, the script prints exactly why in plain
English and exits 1 immediately, so a bad Archive.org key or a broken
download shows up as a red X -- never a fake green checkmark. After the
canary, remaining episodes continue past individual failures, but ANY
failure still makes the run exit 1 at the end.

Link-check patience (fixed Oct 1, 2026): Archive.org can take 10+ minutes
to publish the public download link after a PUT, so the post-upload check
waits up to ~15 minutes instead of giving up at 6. Files already live on
Archive.org are skipped, never re-uploaded, so re-runs resume cheaply.

No license metadata is set on the Archive.org items (Kirk picks that later).
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
COLLECTION = "opensource_audio"


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def safe_filename(guid):
    return re.sub(r"[^A-Za-z0-9_-]", "_", guid) + ".mp3"


def ascii_header(s):
    return "".join(c if 32 <= ord(c) < 127 else "?" for c in (s or ""))[:200]


def download(url, tmp):
    """Fetch an episode MP3. Returns (True, '') or (False, reason)."""
    r = sh(["curl", "-sS", "-L", "--max-time", "900", "--retry", "2",
            "-o", tmp, url])
    if r.returncode != 0:
        return False, "curl exit %d: %s" % (r.returncode, r.stderr.strip()[:300])
    if not os.path.exists(tmp):
        return False, "curl reported success but no file was written"
    size = os.path.getsize(tmp)
    if size < 1024:
        return False, "downloaded file is only %d bytes (not audio)" % size
    return True, ""


def ia_put(identifier, filename, filepath, title, access, secret):
    """PUT one file to the Internet Archive via its S3 API.

    Returns (http_code, curl_stderr, response_body_snippet). NOTE: secrets
    travel in argv here; GitHub-hosted runners are single-tenant per job,
    and the keys live in repo Secrets otherwise. Acceptable here.

    x-archive-auto-make-bucket:1 is REQUIRED on the first PUT for a new
    identifier -- without it Archive.org answers 404 (NoSuchBucket)
    instead of creating the item.
    """
    url = "https://s3.us.archive.org/%s/%s" % (identifier, filename)
    body_file = "/tmp/ia_put_body.txt"
    cmd = [
        "curl", "-sS", "-o", body_file, "-w", "%{http_code}",
        "--max-time", "900", "--retry", "2",
        "-X", "PUT",
        "-H", "Authorization: LOW %s:%s" % (access, secret),
        "-H", "x-archive-auto-make-bucket: 1",
        "-H", "x-archive-meta-title: %s" % ascii_header(title),
        "-H", "x-archive-meta-creator: %s" % CREATOR,
        "-H", "x-archive-meta-mediatype: audio",
        "-H", "x-archive-meta-collection: %s" % COLLECTION,
        "--data-binary", "@%s" % filepath,
        url,
    ]
    r = sh(cmd)
    body = ""
    try:
        with open(body_file, "r", errors="replace") as f:
            body = f.read(600)
    except OSError:
        pass
    return r.stdout.strip(), r.stderr.strip(), body


def ia_file_live(identifier, filename):
    """One quick HEAD check: is the public download URL already serving the file?

    Used to skip re-uploading files that reached Archive.org on an earlier
    run (the old link-checker sometimes gave up before IA published the
    link, even though the file was fine). No retries, no sleeping.
    """
    url = "https://archive.org/download/%s/%s" % (identifier, filename)
    r = sh(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
            "-I", "-L", "--max-time", "30", url])
    return r.stdout.strip() == "200"


def ia_head_ok(identifier, filename, tries=30):
    """Confirm the public download URL serves the file.

    IA ingest can take 10+ minutes before the public link appears, so this
    waits up to ~15 minutes (30 tries x 30s) before giving up.
    """
    url = "https://archive.org/download/%s/%s" % (identifier, filename)
    for _ in range(tries):
        r = sh(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                "-I", "-L", "--max-time", "30", url])
        if r.stdout.strip() == "200":
            return True
        time.sleep(30)
    return False


def put_error_advice(code):
    if code == "403":
        return ("Archive.org REJECTED the keys (HTTP 403). "
                "Fix: repo Settings > Secrets > Actions -- delete IA_ACCESS_KEY "
                "and IA_SECRET_KEY and re-create them with the exact values "
                "from archive.org S3 keys page (no extra spaces).")
    if code == "401":
        return ("Archive.org says unauthorized (HTTP 401). Same fix as 403: "
                "re-create IA_ACCESS_KEY / IA_SECRET_KEY exactly.")
    return "Archive.org returned HTTP %s." % code


def main():
    access = os.environ.get("IA_ACCESS_KEY", "").strip()
    secret = os.environ.get("IA_SECRET_KEY", "").strip()
    if not access or not secret:
        print("IA_ACCESS_KEY / IA_SECRET_KEY are not set. "
              "Add them under repo Settings > Secrets > Actions.", file=sys.stderr)
        return 2

    here = os.path.dirname(os.path.abspath(__file__))
    ov_path = os.path.join(here, "enclosure-overrides.json")
    overrides = {}
    if os.path.exists(ov_path):
        with open(ov_path) as f:
            overrides = json.load(f)

    # ---- gather every pending episode across all feeds ----
    pending = []  # (slug, identifier, show_title, guid, title, url)
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
                print("[%s] episode %s has no enclosure URL, skipping" % (slug, guid))
                continue
            title = item.findtext("title") or guid
            pending.append((slug, identifier, show_title, guid, title, enc.get("url")))

    print("Found %d episode(s) still needing upload (%d already uploaded)." %
          (len(pending), len(overrides)), flush=True)
    if not pending:
        print("nothing new to upload")
        return 0

    def upload_one(slug, identifier, show_title, guid, title, url, canary):
        """Returns (True, '') on success or (False, reason)."""
        filename = safe_filename(guid)
        final_url = "https://archive.org/download/%s/%s" % (identifier, filename)
        if ia_file_live(identifier, filename):
            print("[%s] already on Archive.org, skipping upload" % guid, flush=True)
            return True, final_url
        tmp = "/tmp/%s.mp3" % re.sub(r"[^A-Za-z0-9_-]", "_", guid)
        ok, reason = download(url, tmp)
        if not ok:
            return False, "DOWNLOAD FAILED for %s (%s): %s" % (guid, title[:60], reason)
        code, err, body = ia_put(identifier, filename, tmp,
                                 "%s: %s" % (show_title, title), access, secret)
        try:
            os.remove(tmp)
        except OSError:
            pass
        if code != "200":
            advice = put_error_advice(code)
            detail = " curl said: %s" % err[:200] if err else ""
            srv = (" archive.org said: %s" % " ".join(body.split())[:300]
                   if body.strip() else "")
            return False, "UPLOAD FAILED for %s (%s): %s%s%s" % (
                guid, title[:60], advice, detail, srv)
        if not ia_head_ok(identifier, filename):
            return False, ("UPLOAD PROBLEM for %s (%s): the file reached "
                           "Archive.org but the public link never came up." %
                           (guid, title[:60]))
        return True, final_url

    # ---- canary: first episode must work, or fail fast with a clear reason ----
    slug, identifier, show_title, guid, title, url = pending[0]
    print("Canary: testing with [%s] %s ..." % (slug, title[:60]), flush=True)
    ok, result = upload_one(slug, identifier, show_title, guid, title, url, True)
    if not ok:
        print(result, file=sys.stderr)
        print("STOPPING: fix the problem above, then re-run the workflow.", file=sys.stderr)
        return 1
    overrides[guid] = result
    print("[%s] OK -> %s" % (slug, result), flush=True)

    # ---- remaining episodes: keep going past single failures, but report them ----
    failed = 0
    for slug, identifier, show_title, guid, title, url in pending[1:]:
        print("[%s] uploading %s (%s) ..." % (slug, guid, title[:60]), flush=True)
        ok, result = upload_one(slug, identifier, show_title, guid, title, url, False)
        if ok:
            overrides[guid] = result
            print("[%s] OK -> %s" % (slug, result), flush=True)
        else:
            failed += 1
            print(result, file=sys.stderr)

    with open(ov_path, "w") as f:
        json.dump(overrides, f, indent=1)
        f.write("\n")

    done = len(pending) - failed
    print("enclosure-overrides.json updated (%d entries)" % len(overrides))
    print("Uploaded %d of %d pending episode(s), %d failed." % (done, len(pending), failed))
    if failed:
        print("Some episodes failed -- see lines above. Re-run the workflow "
              "to retry just the failures (successes are skipped).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
