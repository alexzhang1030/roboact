import { useEffect, useState } from 'react'
import { Stage, type Act } from '../kit/stage'
import { Bar } from '../kit/window'
import { PORT_POSE, PORT_TARGET, RUN_KEY, copy, doneSeconds, stepsLabel } from './copy'
import doneIcon from '../icons/state-done.svg'
import idleIcon from '../icons/state-idle.svg'
import runningIcon from '../icons/state-running.svg'
import startIcon from '../icons/node-start.svg'
import toolIcon from '../icons/node-tool.svg'
import writeIcon from '../icons/mark-write.svg'

type LayoutName = 'wide' | 'compact'
type NodeStatus = 'idle' | 'running' | 'done'
type RunWord = 'off' | 'running' | 'completed'

type Geom = {
  nodeW: number
  titleH: number
  start: { x: number; y: number }
  pose: { x: number; y: number }
  grip: { x: number; y: number }
  rest: { x: number; y: number }
}

const WIDE: Geom = {
  // Wider than the product's 240px node so "Go to pose" and "Done · 0.4 s" share the title row without clipping.
  nodeW: 280,
  titleH: 32,
  start: { x: 240, y: 200 },
  pose: { x: 560, y: 200 },
  grip: { x: 880, y: 200 },
  rest: { x: 480, y: 520 },
}

const COMPACT: Geom = {
  nodeW: 168,
  titleH: 48,
  start: { x: -220, y: 100 },
  pose: { x: 20, y: 100 },
  grip: { x: 200, y: 100 },
  rest: { x: 180, y: 420 },
}

const geomOf = (layout: LayoutName): Geom => (layout === 'compact' ? COMPACT : WIDE)

type S = {
  onCanvas: boolean
  dragging: boolean
  leaving: boolean
  edge: boolean
  fade: boolean
  gx: number
  gy: number
  steps: number
  run: RunWord
  start: NodeStatus
  pose: NodeStatus
  grip: NodeStatus
}

const initial: S = {
  onCanvas: false,
  dragging: false,
  leaving: false,
  edge: false,
  fade: false,
  gx: 0,
  gy: 0,
  steps: 0,
  run: 'off',
  start: 'idle',
  pose: 'idle',
  grip: 'idle',
}

const poster: S = {
  ...initial,
  onCanvas: true,
  edge: true,
  steps: 3,
  run: 'completed',
  start: 'done',
  pose: 'done',
  grip: 'done',
}

const idlePatch: Partial<S> = {
  fade: true,
  run: 'off',
  start: 'idle',
  pose: 'idle',
  grip: 'idle',
  steps: 0,
}

async function script(a: Act<S>) {
  const g = geomOf(a.layout === 'compact' ? 'compact' : 'wide')
  // Hold below the port row so the lifted card's bottom is not 1px off a resting node.
  const offset = { x: g.nodeW / 2, y: 1 + g.titleH + 22 }

  a.mark('start')
  await a.wait(450)

  a.mark('node')
  const row = a.rect('grip-row')
  const slot = a.rect('grip-slot')
  await a.move({ x: row.x + row.width * 0.82, y: row.y + row.height * 0.5 }, { duration: 520 })
  await a.drag(
    { x: slot.x + offset.x, y: slot.y + offset.y },
    (p) => {
      a.put({
        onCanvas: true,
        dragging: true,
        gx: Math.round(p.x - offset.x),
        gy: Math.round(p.y - offset.y),
      })
    },
    { duration: 980 },
  )
  await a.set({ dragging: false })
  await a.wait(280)

  a.mark('edge')
  await a.move({ at: 'pose-out', x: 0.5, y: 0.5 }, { duration: 420 })
  await a.cursor('crosshair')
  await a.drag({ at: 'grip-in', x: 0.5, y: 0.5 }, undefined, { duration: 640, cursor: 'preserve' })
  await a.set({ edge: true })
  await a.wait(420)

  a.mark('run')
  await a.move('run', { duration: 420 })
  await a.click()
  await a.set({ run: 'running', steps: 1, start: 'running' })
  const watch = a.layout === 'compact' ? { x: 186, y: 360 } : { x: 700, y: 460 }
  await a.move(watch, { duration: 400 })
  await a.wait(280)
  await a.set({ start: 'done', steps: 2, pose: 'running' })
  await a.wait(760)
  await a.set({ pose: 'done', steps: 3, grip: 'running' })
  await a.wait(680)
  await a.set({ grip: 'done', run: 'completed' })
  a.mark('hold')
  await a.wait(2000)

  a.mark('reset')
  await a.move({ at: 'grip-hold', x: 0.5, y: 0.5 }, { duration: 400 })
  const back = { x: row.x + row.width * 0.82, y: row.y + row.height * 0.5 }
  await a.drag(
    back,
    (p, _from, t) => {
      a.put({
        dragging: true,
        gx: Math.round(p.x - offset.x),
        gy: Math.round(p.y - offset.y),
        ...(t > 0.42 ? idlePatch : {}),
      })
    },
    { duration: 960 },
  )
  await a.set({ ...idlePatch, leaving: true, dragging: false })
  await a.wait(280)
  await a.set({
    onCanvas: false,
    edge: false,
    leaving: false,
    fade: false,
    dragging: false,
    gx: 0,
    gy: 0,
  })
  await a.move('rest', { duration: 420 })
  await a.wait(360)
  a.mark('seam')
}

function useScheme(): 'light' | 'dark' {
  const [theme, setTheme] = useState<'light' | 'dark'>('light')
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const apply = () => setTheme(mq.matches ? 'dark' : 'light')
    apply()
    mq.addEventListener('change', apply)
    return () => mq.removeEventListener('change', apply)
  }, [])
  return theme
}

function Ico({ src, kind }: { src: string; kind?: string }) {
  return <span className="ico" data-kind={kind} style={{ ['--ico' as string]: `url("${src}")` }} />
}

function statusKind(status: NodeStatus): string | undefined {
  if (status === 'running') return 'running'
  if (status === 'done') return 'done'
  return undefined
}

function statusIcon(status: NodeStatus): string {
  if (status === 'running') return runningIcon
  if (status === 'done') return doneIcon
  return idleIcon
}

function statusWord(status: NodeStatus, seconds: string): string {
  if (status === 'running') return copy.statusRunning
  if (status === 'done') return doneSeconds(seconds)
  return copy.idle
}

function Node({
  x,
  y,
  title,
  icon,
  write,
  status,
  seconds,
  ports,
  hold,
  nodeTarget,
  leaving,
  live,
}: {
  x: number
  y: number
  title: string
  icon: string
  write?: boolean
  status: NodeStatus
  seconds: string
  ports: 'start' | 'pose' | 'grip'
  hold?: boolean
  nodeTarget?: string
  leaving?: boolean
  live?: boolean
}) {
  return (
    <div
      className={live ? 'fn fn-grip' : 'fn'}
      data-status={status === 'running' ? 'running' : 'idle'}
      data-leaving={leaving || undefined}
      data-target={nodeTarget}
      style={live ? { transform: `translate(${x}px, ${y}px)` } : { left: x, top: y }}
    >
      <div className="fn-title">
        <Ico src={icon} />
        {write ? <Ico src={writeIcon} /> : null}
        <span className="fn-name">{title}</span>
        <span className="fn-st">
          <Ico src={statusIcon(status)} kind={statusKind(status)} />
          <span className="fn-st-word">{statusWord(status, seconds)}</span>
        </span>
      </div>
      {ports === 'start' ? (
        <div className="fn-port out">
          <span className="fn-port-name">{copy.initialData}</span>
          <span className="handle out" data-target="start-out">
            <span className="dot" />
          </span>
        </div>
      ) : null}
      {ports === 'pose' ? (
        <>
          <div className="fn-port">
            <span className="handle in">
              <span className="dot" />
            </span>
            <Bar w={48} h={6} />
            <span style={{ flex: 1 }} />
            <span className="fn-port-name">{PORT_POSE}</span>
            <span className="handle out" data-target="pose-out" data-cursor="crosshair">
              <span className="dot" />
            </span>
          </div>
          <div className="fn-param">
            <Bar w={88} h={6} />
          </div>
        </>
      ) : null}
      {ports === 'grip' ? (
        <>
          <div className="fn-port">
            <span className="handle in" data-target="grip-in" data-cursor="crosshair">
              <span className="dot" />
            </span>
            <span className="fn-port-name">{PORT_TARGET}</span>
            <span style={{ flex: 1 }} />
            <Bar w={40} h={6} />
          </div>
          <div className="fn-param">
            <Bar w={72} h={6} />
          </div>
          {hold ? <i className="marker hold" data-target="grip-hold" /> : null}
        </>
      ) : null}
    </div>
  )
}

function curve(a: { x: number; y: number }, b: { x: number; y: number }): string {
  const dx = Math.max(36, Math.abs(b.x - a.x) * 0.45)
  // Leave the source to the right and enter the target from the left.
  // Flipping the handles when a dragged node passes the port jumps the curve.
  return `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`
}

function portOut(node: { x: number; y: number }, g: Geom): { x: number; y: number } {
  return { x: node.x + g.nodeW, y: node.y + 1 + g.titleH + 12 }
}

function portIn(node: { x: number; y: number }, g: Geom): { x: number; y: number } {
  return { x: node.x, y: node.y + 1 + g.titleH + 12 }
}

export default function Hero() {
  const theme = useScheme()
  return (
    <Stage<S>
      width={1200}
      height={750}
      compact={{ below: 800, width: 390, height: 640 }}
      initial={initial}
      poster={poster}
      script={script}
      play="loop"
      theme={theme}
      cursor={{ at: 'rest', idle: false }}
      className="hero"
    >
      {(s, _rect, layout) => {
        const g = geomOf(layout === 'compact' ? 'compact' : 'wide')
        const live = s.dragging || s.leaving
        const gripAt = live || (s.onCanvas && s.gx !== 0) ? { x: s.gx, y: s.gy } : g.grip
        const showGrip = s.onCanvas || s.dragging || s.leaving
        const startOut = portOut(g.start, g)
        const poseIn = portIn(g.pose, g)
        const poseOut = portOut(g.pose, g)
        const gripIn = portIn(gripAt, g)
        const wide = layout !== 'compact'
        return (
          <div className="app" style={{ ['--node-w' as string]: `${g.nodeW}px`, ['--title-h' as string]: `${g.titleH}px` }}>
            <header className="top">
              <div className="top-vis">
                {wide ? (
                  <div className="top-left">
                    <span className="flow-name">{copy.flowDemo}</span>
                    <Bar w={52} h={8} />
                    <Bar w={84} h={8} />
                  </div>
                ) : (
                  <span />
                )}
                <div className="top-mid">
                  <button type="button" className="btn primary">
                    {copy.run} <span className="kbd">{RUN_KEY}</span>
                    <i className="aim" data-target="run" />
                  </button>
                  {wide ? (
                    <button type="button" className="btn" disabled>
                      {copy.step}
                    </button>
                  ) : null}
                  {wide ? (
                    <button type="button" className="btn" disabled={s.run !== 'running'}>
                      {copy.stop}
                    </button>
                  ) : null}
                  <span className="run-word">
                    {s.run === 'running' ? copy.running : s.run === 'completed' ? copy.completed : ''}
                  </span>
                </div>
                <div className="top-right">
                  {wide ? <span className="sim">{copy.sim}</span> : null}
                  <span className="steps">{stepsLabel(s.steps)}</span>
                  {wide ? <span className="conn">{copy.connected}</span> : null}
                </div>
              </div>
            </header>

            {wide ? (
              <aside className="palette">
                <div className="group">{copy.flowControl}</div>
                <div className="row">
                  <Ico src={startIcon} />
                  <span>{copy.start}</span>
                </div>
                <div className="row">
                  <Bar w={16} h={16} r={2} />
                  <Bar w={88} h={6} />
                </div>
                <div className="group">{copy.fakeAdapter}</div>
                <div className="row">
                  <Ico src={toolIcon} />
                  <span>{copy.goToPose}</span>
                </div>
                <div className="row" data-target="grip-row">
                  <Ico src={toolIcon} />
                  <span>{copy.gripper}</span>
                </div>
                {Array.from({ length: 16 }, (_, i) => (
                  <div className="row" key={i}>
                    <Bar w={16} h={16} r={2} />
                    <Bar w={64 + ((i * 17) % 48)} h={6} />
                  </div>
                ))}
              </aside>
            ) : (
              <i className="pick" data-target="grip-row" style={{ left: 96, top: -40 }} />
            )}

            <div className="canvas" />

            {wide ? (
              <aside className="right-panel" aria-hidden>
                <Bar w={120} h={8} />
                <Bar w={200} h={6} />
                <Bar w={160} h={6} />
                <Bar w={180} h={6} />
                <Bar w={96} h={6} />
              </aside>
            ) : null}

            <Node
              x={g.start.x}
              y={g.start.y}
              title={copy.start}
              icon={startIcon}
              status={s.start}
              seconds="0.2"
              ports="start"
            />
            <Node
              x={g.pose.x}
              y={g.pose.y}
              title={copy.goToPose}
              icon={toolIcon}
              write
              status={s.pose}
              seconds="0.4"
              ports="pose"
            />
            {showGrip ? (
              <Node
                x={gripAt.x}
                y={gripAt.y}
                title={copy.gripper}
                icon={toolIcon}
                write
                status={s.grip}
                seconds="0.2"
                ports="grip"
                hold
                nodeTarget="grip-node"
                leaving={s.leaving}
                live
              />
            ) : null}

            <svg className="wires" width="1680" height="980" aria-hidden>
              <path className="wire" d={curve(startOut, poseIn)} />
              {s.edge ? (
                <path className="wire wire-new" data-leaving={s.leaving || undefined} d={curve(poseOut, gripIn)} />
              ) : null}
            </svg>

            <i className="marker" data-target="grip-slot" style={{ left: g.grip.x, top: g.grip.y }} />
            <i className="marker" data-target="rest" style={{ left: g.rest.x, top: g.rest.y }} />
          </div>
        )
      }}
    </Stage>
  )
}
