# Homepage hero

A looping fake of the flow canvas for the README. React and the Stageshow kit live only in this folder. `web/` stays build-free.

Design size 1200×750. A narrower page scales that whole frame; there is no separate phone crop.

## Run

```bash
cd demo/hero
npm install
CHROME_PATH=/usr/local/bin/google-chrome npm run dev
# http://127.0.0.1:5179/
```

`CHROME_PATH` is only for filming. The dev server binds `127.0.0.1`.

## Re-film

From `demo/hero`, with the dev server up. `scripts/film.mjs` writes the contact sheet. It pulls in `browser.mjs`, `film-checks.mjs`, `jumps-core.mjs`, `layout-core.mjs`, `motion-watch.mjs`, and `verification.mjs`.

```bash
CHROME_PATH=/usr/local/bin/google-chrome node scripts/film.mjs \
  http://127.0.0.1:5179/ --width 1200 --every 400 --count 30 --pad 0 \
  --out /tmp/hero-light.png

CHROME_PATH=/usr/local/bin/google-chrome node scripts/film.mjs \
  http://127.0.0.1:5179/ --dark --width 1200 --every 400 --count 30 --pad 0 \
  --out /tmp/hero-dark.png

CHROME_PATH=/usr/local/bin/google-chrome node scripts/film.mjs \
  http://127.0.0.1:5179/ --width 390 --at hold --delay 500 --still --pad 0 \
  --out /tmp/hero-390.png
```

`node scripts/export-loop.mjs` rewrites `media/hero-light.gif`, `media/hero-dark.gif`, and `media/hero.mp4` at 1200×750. The kit under `kit/` is the Stageshow slice this demo imports (MIT, `kit/LICENSE`). Icons are the product SVGs. Fonts are the repo's Inter and JetBrains Mono.
