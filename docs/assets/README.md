# Town screenshots

This directory holds the per-tier screenshots embedded in the GitHub Pages site.

To populate / refresh them, run from the repo root:

```bash
pip install pyxel
./cwatts town --screenshot docs/assets/
```

That iterates through every Wh threshold in the tech tree, opens a pyxel
window, snaps a 4× scaled PNG, closes the window, and moves on. The site
references the resulting filenames (`town-50wh.png`, `town-500wh.png`, etc.)
automatically.

The site falls back to text placeholders if an image is missing, so you
can ship the page before generating screenshots — but it looks much better
once they're in.
