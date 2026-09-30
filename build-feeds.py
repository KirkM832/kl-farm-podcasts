#!/usr/bin/env python3
"""Build Apple-Podcasts-compliant RSS feeds from the platform (muse.ai) source feeds.

The platform feeds are missing itunes:author, itunes:owner and itunes:category,
which Apple Podcasts Connect validation requires. This script re-fetches each
source feed and re-emits it with those tags added, preserving every item's
title, description, pubDate, guid and enclosure URL verbatim (changing GUIDs
or enclosure URLs would make Apple treat episodes as new/duplicates).

Shows may override the source channel's title/description/cover/link via the
optional keys below, and may restrict items to a title prefix (used for
Big Sister Energy, whose episodes publish inside the Today with Kirk feed).

Usage:  python3 build-feeds.py        (no arguments; refreshes all 6 files)
Output: <slug>.xml files in this directory.

After hosting is chosen, set BASE_URL below to the public base URL so the
atom self-links point at the new feed locations, then re-run.
"""

import subprocess
import xml.etree.ElementTree as ET

# Set this once the files are hosted (e.g. "https://USER.github.io/kirk-podcasts").
# Used only for the atom:link rel="self" tag. Leave empty to omit self links.
BASE_URL = ""

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ATOM_NS = "http://www.w3.org/2005/Atom"

TODAY_WITH_KIRK = "https://muse.ai/podcasts/feed/1258569844016700/303f3fe1-4139-4551-8d5a-5b06b43d6b72"
PAGES = "https://KirkM832.github.io/kl-farm-podcasts"

SHOWS = [
    {
        "slug": "today-with-kirk",
        "source": TODAY_WITH_KIRK,
        "category": "Society & Culture",
    },
    {
        "slug": "learning-under-pressure",
        "source": "https://muse.ai/podcasts/feed/1258569844016700/0087914e-9832-4aeb-931c-e2c0d4949232",
        "category": "Education",
    },
    {
        "slug": "before-cullman",
        "source": "https://muse.ai/podcasts/feed/1258569844016700/964f6b2e-ff99-4dc8-95ef-fabb89bc9115",
        "category": "History",
    },
    {
        "slug": "the-spin-cycle",
        "source": "https://muse.ai/podcasts/feed/1258569844016700/b972c63d-5f58-45af-9b24-01c756f1bfdb",
        "category": "News",
    },
    {
        "slug": "lantern-stories",
        "source": "https://muse.ai/podcasts/feed/1258569844016700/4908ca32-a69a-41a9-bd1c-b618bb1f8340",
        "category": "Kids & Family",
    },
    {
        "slug": "big-sister-energy",
        "source": TODAY_WITH_KIRK,
        "category": "Kids & Family",
        "title": "Big Sister Energy",
        "description": (
            "Big Sister Energy is a daily show for teen girls, hosted by Maya "
            "and Zoe - your fun big sisters who actually get it. Confidence, "
            "friendships, big emotions, dating, money sense, goals, and study "
            "skills, plus listener call-ins and everyday fun stuff. Real talk, "
            "zero lectures."
        ),
        "cover": f"{PAGES}/big-sister-energy-cover.jpg",
        "link": f"{PAGES}/big-sister-energy.xml",
        "keywords": "teen advice,big sister advice,confidence for teens,teen girls,friendship,teen life,self esteem",
        "item_prefix": "Big Sister Energy",
    },
]

AUTHOR = "Kirk Moore"
OWNER_EMAIL = "medix.amc@gmail.com"


def fetch(url):
    out = subprocess.run(
        ["curl", "-sL", "--max-time", "30", url],
        capture_output=True, text=True, check=True,
    ).stdout
    if not out.strip().startswith("<"):
        raise RuntimeError(f"Unexpected response fetching {url}: {out[:120]!r}")
    return out


def explicit(value):
    """Normalize platform 'true'/'false' to Apple's 'yes'/'no'."""
    v = (value or "").strip().lower()
    if v in ("yes", "true", "explicit"):
        return "yes"
    return "no"


def sub(parent, tag, text=None, attrib=None):
    el = ET.SubElement(parent, tag, attrib or {})
    if text is not None:
        el.text = text
    return el


def build(show):
    raw = fetch(show["source"])
    src = ET.fromstring(raw)
    src_ch = src.find("channel")

    def t(el, name):
        n = el.find(name)
        return n.text if n is not None else None

    ET.register_namespace("itunes", ITUNES_NS)
    ET.register_namespace("atom", ATOM_NS)

    rss = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(rss, "channel")

    title = show.get("title") or t(src_ch, "title")
    description = show.get("description") or t(src_ch, "description")
    language = t(src_ch, "language") or "en"
    cover = show.get("cover")
    if not cover:
        img_el = src_ch.find(f"{{{ITUNES_NS}}}image")
        if img_el is not None:
            cover = img_el.get("href")

    sub(ch, "title", title)
    # link: fall back to the source feed URL
    atom_link = src_ch.find(f"{{{ATOM_NS}}}link")
    link = show.get("link") or (atom_link.get("href") if atom_link is not None else show["source"])
    sub(ch, "link", link)
    sub(ch, "description", description)
    sub(ch, "language", language)
    sub(ch, f"{{{ITUNES_NS}}}author", AUTHOR)
    owner = sub(ch, f"{{{ITUNES_NS}}}owner")
    sub(owner, f"{{{ITUNES_NS}}}name", AUTHOR)
    sub(owner, f"{{{ITUNES_NS}}}email", OWNER_EMAIL)
    sub(ch, f"{{{ITUNES_NS}}}category", attrib={"text": show["category"]})
    sub(ch, f"{{{ITUNES_NS}}}type", "episodic")
    sub(ch, f"{{{ITUNES_NS}}}explicit", explicit(t(src_ch, f"{{{ITUNES_NS}}}explicit")))
    sub(ch, f"{{{ITUNES_NS}}}summary", description)
    if show.get("keywords"):
        sub(ch, f"{{{ITUNES_NS}}}keywords", show["keywords"])
    if cover:
        sub(ch, f"{{{ITUNES_NS}}}image", attrib={"href": cover})
    if BASE_URL:
        sub(ch, f"{{{ATOM_NS}}}link", attrib={
            "href": f"{BASE_URL.rstrip('/')}/{show['slug']}.xml",
            "rel": "self",
            "type": "application/rss+xml",
        })

    items = src_ch.findall("item")
    prefix = show.get("item_prefix")
    if prefix:
        items = [it for it in items if (t(it, "title") or "").startswith(prefix)]

    for item in items:
        it = ET.SubElement(ch, "item")
        sub(it, "title", t(item, "title"))
        desc = t(item, "description")
        sub(it, "description", desc)
        sub(it, f"{{{ITUNES_NS}}}summary", desc)
        guid_el = item.find("guid")
        if guid_el is not None:
            g = sub(it, "guid", guid_el.text,
                    {"isPermaLink": guid_el.get("isPermaLink", "false")})
        sub(it, "pubDate", t(item, "pubDate"))
        enc = item.find("enclosure")
        if enc is not None:
            sub(it, "enclosure", attrib={
                "url": enc.get("url"),
                "length": enc.get("length"),
                "type": enc.get("type"),
            })
        dur = t(item, f"{{{ITUNES_NS}}}duration")
        if dur:
            sub(it, f"{{{ITUNES_NS}}}duration", dur)
        sub(it, f"{{{ITUNES_NS}}}explicit",
            explicit(t(item, f"{{{ITUNES_NS}}}explicit")))
        ep_img = item.find(f"{{{ITUNES_NS}}}image")
        if ep_img is not None and ep_img.get("href"):
            sub(it, f"{{{ITUNES_NS}}}image", attrib={"href": ep_img.get("href")})

    ET.indent(rss, space="  ")
    xml = ET.tostring(rss, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml


def main():
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    for show in SHOWS:
        xml = build(show)
        # sanity: re-parse what we wrote
        ET.fromstring(xml)
        path = os.path.join(here, f"{show['slug']}.xml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(xml)
        n_items = xml.count("<item>")
        print(f"wrote {path} ({len(xml)} bytes, {n_items} items)")


if __name__ == "__main__":
    main()
