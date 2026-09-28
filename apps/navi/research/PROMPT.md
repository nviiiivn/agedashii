# The research prompt

Paste the block below into a **new** session. It does the next unfinished
section of `../CHOICES.md`, waits for your pick, writes it down, and stops.
Close that session and paste the same block into a new one for the next section.

One section per session on purpose: 13 sections of full research is ~400 page
reads, far more than one session can hold without starting to guess.

Before the first run, the Flet session writes its findings on navmaster, vialix,
Kblack, Ride-Track, MOTO-HUB and MotorcycleLeanAngleSensor to
`00-already-checked.md` in this folder.

```
Read apps/navi/CHOICES.md, apps/navi/research/00-already-checked.md, and every file in apps/navi/research/.
Find the LOWEST-numbered section of CHOICES.md that has no apps/navi/research/NN-*.md file yet. Do ONLY that section. Research only, no code.

Depth:
- Sections 1-13: at least 6 different web searches, using the phrases I'd type into Google plus the section's "search:" lines. Also check GitHub topic pages, repo search sorted by stars AND by recently updated, and awesome-lists. Open and read the top 5 results of every search, not just the titles.
- Sections 14-15: 2-3 searches, short.

Rules:
- No licence file = ruled out. Say so.
- Don't re-research anything in 00-already-checked.md. Reuse those findings.
- If you can't verify something, say so. Don't guess.
- Already decided: vector tiles (styled per theme), and posted speed limits from OSM maxspeed with the typical-for-road-class number as the fallback. Builds can happen on the Pi (arm64 aapt2 override) or on Tower. My style is alola.lol / css.alola.lol, NOT Material Design. It must work fully offline and be fast.

For every option, give: name, link, licence, last release/commit date, stars/activity, whether it works on Android, whether it's fully offline, speed, how it fits my rules, known problems.

Write the full research to apps/navi/research/NN-<topic>.md. It must include the list of every search you ran and the top 5 results for each, with a note on why each is in or out.
In chat, show me ONLY a short comparison table plus ONE line saying what you'd pick and why. Then stop and wait.
When I pick, write my pick and my reason on that section's "Pick:" line in CHOICES.md, and say "done: section NN". Nothing else.
```
