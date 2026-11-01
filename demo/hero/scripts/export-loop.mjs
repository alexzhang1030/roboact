// One full loop to MP4 and a GIF, at the 1200×750 design size, 2×.
// film.mjs does the contact sheets. This is the README fallback.
//
//   CHROME_PATH=... node scripts/export-loop.mjs http://127.0.0.1:5179
import { launch } from './browser.mjs'
import { mkdirSync, rmSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { resolve } from 'node:path'

const url = process.argv[2]
if (!url) {
  console.error('usage: node scripts/export-loop.mjs <url>')
  process.exit(2)
}

const fps = 10
const frames = resolve('media/.frames')
rmSync(frames, { recursive: true, force: true })
mkdirSync(frames, { recursive: true })

const browser = await launch()
const page = await browser.newPage({
  viewport: { width: 1200, height: 750 },
  deviceScaleFactor: 2,
})
await page.goto(url, { waitUntil: 'networkidle' })
await page.waitForFunction(() => document.querySelector('.ss-stage')?.getAttribute('data-mark') === 'start')
const duration = await page.evaluate(() => new Promise((resolvePromise) => {
  const stage = document.querySelector('.ss-stage')
  const origin = performance.now()
  const arm = () => {
    if (stage.getAttribute('data-mark') === 'seam') resolvePromise(performance.now() - origin)
  }
  new MutationObserver(arm).observe(stage, { attributes: true, attributeFilter: ['data-mark'] })
}))
// The wait above already consumed the loop. Reload and shoot the next one, timed to the measured length.
await page.reload({ waitUntil: 'networkidle' })
await page.waitForFunction(() => document.querySelector('.ss-stage')?.getAttribute('data-mark') === 'start')
const start = Date.now()
let i = 0
while (Date.now() - start < duration + 200) {
  const target = start + (i * 1000) / fps
  const wait = target - Date.now()
  if (wait > 0) await page.waitForTimeout(wait)
  await page.locator('.ss-stage').screenshot({ path: `${frames}/${String(i).padStart(4, '0')}.png` })
  i += 1
}
await browser.close()
const wall = (Date.now() - start) / 1000
// Screenshots run slower than the target fps, so play the frames back over the
// wall clock they covered. Otherwise the file is a sped-up loop.
const fpsOut = (i / wall).toFixed(3)
console.log(`captured ${i} frames over ${wall.toFixed(3)} s (stage loop ${Math.round(duration)} ms), playback ${fpsOut} fps`)

const mp4 = resolve('media/hero.mp4')
const gif = resolve('media/hero.gif')
const palette = resolve('media/.palette.png')
const run = (args) => {
  const result = spawnSync('ffmpeg', ['-y', ...args], { stdio: 'inherit' })
  if (result.status !== 0) process.exit(result.status ?? 1)
}
run(['-framerate', fpsOut, '-i', `${frames}/%04d.png`, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', mp4])
run(['-i', mp4, '-vf', 'fps=8,scale=2400:-1:flags=lanczos,palettegen=stats_mode=diff:max_colors=96', palette])
run(['-i', mp4, '-i', palette, '-lavfi', 'fps=8,scale=2400:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3', gif])
rmSync(frames, { recursive: true, force: true })
rmSync(palette, { force: true })
