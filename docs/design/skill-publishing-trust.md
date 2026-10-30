# Skill publishing without a manual gate

Status: plan, 2026-10-30. Owner direction: anyone — also people without a GitHub account or a website — must be able
to publish a skill and have it installable quickly, without the founder approving each one by hand.

## What the 2026-10 incident showed

- Verification ran `pip install .` on prompt-only skills; every external skill failed and stayed quarantined
  (fixed in #188; five more skills have sat in quarantine with that failure since July).
- The security scanner reads only `.py` files. A skill's `SKILL.md` — the only thing a skill consists of — was never
  content-checked. Since #188 a clean skill of a new publisher is auto-cleared on publish, so this gap is now live.
- Publisher trust is set only by an admin (`PUT /v1/admin/publishers/{slug}/trust`). There is no path to `verified`,
  yet the SDK installs nothing below `verified`. A new publisher can therefore publish nothing anyone can install.
- The website says "Publish → get verified" and lists `unverified` as "manual" install; the SDK refuses it outright.
- The daily admin digest is logged as "sent" while the mail server rejects the admin address on every run, so a
  stuck review queue is never noticed.

## Principle

A skill runs no code. Its risk is in its text (instructions to exfiltrate data, to run remote code, to override the
agent's rules, hidden characters, embedded secrets), and text can be checked on every upload. So for skills the
content check of the package carries the decision, and publisher trust follows from clean content instead of from a
manual review. Code packages keep their existing gates (verification, static/AI scan, container sandbox).

## Steps (one PR each)

1. **Skill content check.** `app/trust/skill_scan.py`: deterministic rules over every text file of a skill —
   hidden/bidi/tag characters, agent-rule overrides, exfiltration phrasing, credential-file access combined with
   sending, pipe-to-shell / encoded-command execution, embedded secrets, long encoded blobs; external links reported
   as information. Verification runs it for skills: *high* fails verification (publisher gets the reason), *medium*
   keeps the version in the review queue (no auto-clear), clean skills go live at once. The security scanner records
   the same findings for skills so they show up for admins.
2. **Automatic `verified` for publishers.** A publisher whose account email is confirmed and whose skill passed
   verification with a clean content check becomes `verified` automatically (recorded with a timestamp and an audit
   row). If a later version of an auto-verified publisher is quarantined by the security scanner, the automatic
   grant is withdrawn. Admin-set levels are never touched by the automation. `trusted` and `curated` stay manual.
3. **Honest pages.** Skills get their own verification card (content check, files, no code) instead of the code
   score; the developer page and FAQ describe the real path to `verified` and the real install behaviour.
4. **Admin queue alert that arrives.** The digest names each version waiting in review with its age and reason, and
   logs per recipient whether delivery succeeded instead of always logging "sent".

Not in scope here: an SDK release (step 2 makes clean skills installable server-side), the AI scanner provider (prod
has no `ANTHROPIC_API_KEY`, so the deterministic check must stand on its own), the noisy code scanner
(`os.environ` = high), the server's drift from `main`, and the mailbox for the admin address (an ops action).

## Limits, said plainly

Pattern rules do not catch every cleverly worded instruction. They catch the common and the blatant cases; together
with immutable versions (artifact hash), the full SKILL.md visible on the package page, reports and takedown, the
residual risk of a text-only package is small — and much smaller than the current state, where packages wait
unseen for weeks.
