# Homepage hero

A looping fake of the flow canvas for the README. React and the Stageshow kit live only in this folder. `web/` stays build-free.

Design size 1200×750. Below 800px the stage switches to a 390×640 crop: the canvas and Run, with the palette off the top.

## Run

```bash
cd demo/hero
npm install
CHROME_PATH=/usr/local/bin/google-chrome npm run dev
# http://127.0.0.1:5179/
```

`CHROME_PATH` is only for filming. The dev server binds `127.0.0.1`.

## Re-film

From `demo/hero`, with the dev server up. `scripts/film.mjs` is the Stageshow contact sheet. `scripts/export-loop.mjs` writes `media/hero.mp4` and `media/hero.gif` at 1200×750 @2× (2400×1500).

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

CHROME_PATH=/usr/local/bin/google-chrome node scripts/jumps.mjs \
  http://127.0.0.1:5179/ --ms 14000 --width 1200

CHROME_PATH=/usr/local/bin/google-chrome node scripts/layout.mjs \
  http://127.0.0.1:5179/ --width 1200 --delay 7000 --select .ss-stage

CHROME_PATH=/usr/local/bin/google-chrome node scripts/export-loop.mjs \
  http://127.0.0.1:5179/
```

The kit is Stageshow (MIT), copied under `kit/`. Icons are the product SVGs. Fonts are the repo's Inter and JetBrains Mono.
