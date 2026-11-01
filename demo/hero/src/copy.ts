/**
 * On-screen copy. Every quoted string is verbatim from web/i18n/en.js.
 * Placeholders are filled with the example numbers the hero brief allows.
 * Keys are named in the comment so a later catalog edit is easy to spot.
 */

export const copy = {
  /** flow.demo */
  flowDemo: 'Fake adapter demo',
  /** run.start */
  run: 'Run',
  /** run.step */
  step: 'Step',
  /** run.stop */
  stop: 'Stop',
  /** run.word.running */
  running: 'Running',
  /** run.word.completed */
  completed: 'Completed',
  /** run.target_sim */
  sim: 'Sim',
  /** conn.ok */
  connected: 'Connected',
  /** palette.control */
  flowControl: 'Flow control',
  /** adapter.fake */
  fakeAdapter: 'Fake adapter',
  /** node.start */
  start: 'Start',
  /** node.fake_set_pose */
  goToPose: 'Go to pose',
  /** node.fake_gripper */
  gripper: 'Gripper',
  /** status.idle */
  idle: 'Idle',
  /** status.running */
  statusRunning: 'Running',
  /** status.done */
  done: 'Done',
  /** port.data — Start's real out-port */
  initialData: 'Initial data',
} as const

/** steps.normal — "Steps {used} / limit {limit}". The brief fixes the limit at 1000. */
export function stepsLabel(used: number): string {
  return `Steps ${used} / limit 1000`
}

/**
 * status.summary.took is not in web/i18n/en.js. The brief's summary line is "Took 0.4 s".
 */
export function tookSeconds(s: string): string {
  return `Took ${s} s`
}

/**
 * Not en.js keys. Fake-adapter port titles in roboact/adapter.py
 * (OutputSpec pose, ParamSpec target). The brief names these ports.
 */
export const PORT_POSE = 'pose'
export const PORT_TARGET = 'target'
