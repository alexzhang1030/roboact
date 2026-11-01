import { chromium } from 'playwright-core'
import { mkdirSync, rmSync } from 'node:fs'
import { spawnSync } from 'node:child_process'

const chrome = process.env.CHROME_PATH
const url = 'http://127.0.0.1:5179/'

async function shoot(theme) {
  const dir = `/tmp/hero-frames-${theme}`
  rmSync(dir, { recursive: true, force: true })
  mkdirSync(dir)
  const browser = await chromium.launch({ executablePath: chrome })
  const page = await browser.newPage({
    viewport: { width: 1200, height: 750 },
    deviceScaleFactor: 1,
    colorScheme: theme,
  })
  if (theme === 'dark') {
    await page.addInitScript(() => {
      window.__stageshow = { theme: 'dark' }
    })
  }
  await page.goto(url, { waitUntil: 'networkidle' })
  await page.waitForFunction(() => document.querySelector('.ss-stage')?.getAttribute('data-mark') === 'start')
  const duration = await page.evaluate(() => new Promise((resolve) => {
    const stage = document.querySelector('.ss-stage')
    const t0 = performance.now()
    const obs = new MutationObserver(() => {
      if (stage.getAttribute('data-mark') === 'seam') {
        obs.disconnect()
        resolve(performance.now() - t0)
      }
    })
    obs.observe(stage, { attributes: true, attributeFilter: ['data-mark'] })
  }))
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForFunction(() => document.querySelector('.ss-stage')?.getAttribute('data-mark') === 'start')
  const start = Date.now()
  const fps = 8
  let i = 0
  while (Date.now() - start < duration + 150) {
    const target = start + (i * 1000) / fps
    const wait = target - Date.now()
    if (wait > 0) await page.waitForTimeout(wait)
    await page.locator('.ss-stage').screenshot({ path: `${dir}/${String(i).padStart(4, '0')}.png` })
    i += 1
  }
  await browser.close()
  const wall = (Date.now() - start) / 1000
  const fpsOut = (i / wall).toFixed(3)
  console.log(theme, 'frames', i, 'loopMs', Math.round(duration), 'playback', fpsOut)
  const mp4 = theme === 'light' ? 'media/hero.mp4' : `/tmp/hero-${theme}.mp4`
  const gif = `media/hero-${theme}.gif`
  const palette = `/tmp/hero-${theme}-palette.png`
  const run = (args) => {
    const result = spawnSync('ffmpeg', ['-y', ...args], { stdio: 'inherit' })
    if (result.status !== 0) process.exit(result.status ?? 1)
  }
  run(['-framerate', fpsOut, '-i', `${dir}/%04d.png`, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', mp4])
  run(['-i', mp4, '-vf', 'fps=8,scale=1200:-1:flags=lanczos,palettegen=stats_mode=diff:max_colors=96', '-update', '1', palette])
  run(['-i', mp4, '-i', palette, '-lavfi', 'fps=8,scale=1200:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3', gif])
  rmSync(dir, { recursive: true, force: true })
}

await shoot('light')
await shoot('dark')
