# The accessibility sessions

At least two of the ten. Same tasks, same script, same thresholds — the only difference is how they
work, and what you watch for.

## Do not

* do not sit them in front of a set-up you prepared;
* do not turn on the screen reader for them;
* do not "quickly fix" the zoom level.

They use **their own** tools, the way they normally do. Anything you configure is something the
product did not have to get right.

## Watch for these, and write them in `notes`

* **Focus** — can they see or hear where they are? A focus ring that disappears is a failure even if
  the task is completed.
* **Order** — does tabbing move through the screen in the order the screen reads?
* **Announcements** — when the screen changes after a click, is the change announced, or does it
  happen silently?
* **The consent sheet** — is it reachable and readable, or does it only exist visually?
* **Buttons that are not buttons** — anything they cannot reach with a keyboard.

## Automated checks

Run whatever automated accessibility checker you like **in addition**. It finds contrast and label
problems quickly and it is worth doing. It is **not** a substitute for these two sessions and it may
never be recorded as one: a page can pass every automated rule and still be unusable.

## Recording

Same row in `results.csv`, with `assistive = screenreader | keyboard | magnification`. Their outcomes
count in the 8-of-10 exactly like everyone else's.
