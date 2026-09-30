# Apple Podcasts replacement feeds — deploy notes

## What this is
`build-feeds.py` re-fetches the 6 platform (muse.ai) source feeds and re-emits
them as Apple-Podcasts-compliant RSS 2.0, adding the tags Apple requires but
the platform omits:

- `<itunes:author>Kirk Moore</itunes:author>`
- `<itunes:owner>` → name `Kirk Moore`, email `medix.amc@gmail.com`
- `<itunes:category text="…"/>` per show (see below)
- `<itunes:type>episodic</itunes:type>`
- Also normalizes `<itunes:explicit>false</itunes:explicit>` → `no`
  (Apple's spec only allows `yes`/`no`/`clean`).

Every item keeps its original title, description, pubDate, `<guid>` and
enclosure URL/length/type byte-for-byte, so Apple will not see duplicates
or "new" episodes.

| File | Show | Category |
|---|---|---|
| `today-with-kirk.xml` | Today with Kirk | Society & Culture |
| `learning-under-pressure.xml` | Learning Under Pressure | Education |
| `before-cullman.xml` | Before Cullman | History |
| `the-spin-cycle.xml` | The Spin Cycle | News |
| `lantern-stories.xml` | Lantern Stories | Kids & Family |
| `big-sister-energy.xml` | Big Sister Energy | Kids & Family |
| `how-we-got-here.xml` | How We Got Here | History |

How We Got Here has no platform source feed yet (pilot not published) —
`build-feeds.py` emits a channel-only feed (title, author, owner, category,
cover `how-we-got-here-cover.jpg` 3000x3000 on Pages, keywords, zero items)
until the first episode publishes, when the source URL goes into the config.
Keywords: history, lived history, ordinary people, ancient rome, world war 1,
american history, true stories.

### Item title rewrites (TITLE_REWRITES in build-feeds.py)
- "Dominoes: How We Got Here" → "Dominoes: The First Domino" (Sept 30 2026):
  the Dominoes pilot's title collided with the new "How We Got Here" show.
  GUIDs/enclosures untouched, so Apple sees the same episode renamed.

Big Sister Energy is a filtered view of the Today with Kirk source feed (only
items whose title starts with "Big Sister Energy"), with its own title,
description, cover (`big-sister-energy-cover.jpg`, 3000x3000, hosted on Pages)
and keywords. Safe keywords only — never diagnoses or labels (Kirk's rule,
Sept 30 2026).

## Regenerate (safe to run any time; takes no arguments)
```
cd ~/workspace/podcasts/apple-feeds && python3 build-feeds.py
```

## Hosting — LIVE (Sept 30 2026)

Repo: `KirkM832/kl-farm-podcasts`, GitHub Pages from main/root.

### BUG FOUND & FIXED Sept 30 2026 — the workflow never actually ran
The original `feeds.yml` embedded `build-feeds.py` as a heredoc
(`cat > build-feeds.py <<'PYEOF'`) with the terminator `PYEOF` indented to
match the YAML block. An indented terminator does NOT close the heredoc, so
bash swallowed the rest of the step (including `python3 build-feeds.py`) into
the file, warned "here-document delimited by end-of-file", and exited 0.
Result: green checks that built nothing; the repo's XML files were the
hand-uploaded originals and the daily schedule refreshed nothing.
Fix: `build-feeds.py` is now a real file in the repo and the workflow just
runs `python3 build-feeds.py`. Lesson: a green workflow run is not proof the
build ran — check the step logs for the script's own output ("wrote ...").

## Daily redeploy command (for the cron, once hosting exists)
```
cd ~/workspace/podcasts/apple-feeds && python3 build-feeds.py && <push-to-host>
```
e.g. with the GitHub Pages repo checked out at `~/workspace/podcasts/apple-feeds/site/`:
```
cd ~/workspace/podcasts/apple-feeds && python3 build-feeds.py && cp *.xml site/ && cd site && git add -A && git -c user.name="shadow" -c user.email="shadow@localhost" commit -qm "feed refresh $(date +%F)" && git push -q
```
Then in Podcasts Connect, Kirk submits the new `https://<user>.github.io/kirk-podcasts/<slug>.xml` URLs (one per show).

## Caveats
- Episode audio and cover art still live on Muse's servers
  (`muse.ai/podcasts/media/…`). The replacement feeds reference those URLs;
  if the platform ever moves them, the feeds need rebuilding (the script
  re-fetches enclosure URLs from the source each run, so a rebuild picks up
  new URLs automatically — but already-published Apple episodes would keep
  pointing at the old audio).
- `source/*.xml` are the last-fetched platform copies, kept for diffing.
