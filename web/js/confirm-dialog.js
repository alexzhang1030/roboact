import { t } from './i18n.js'

let dlg = null
let pending = null

function ensure() {
  if (dlg) return dlg
  dlg = document.createElement('dialog')
  dlg.className = 'confirm-dialog'
  dlg.id = 'confirm-dialog'
  dlg.setAttribute('aria-labelledby', 'confirm-dialog-title')
  dlg.innerHTML = [
    '<h2 id="confirm-dialog-title"></h2>',
    '<p id="confirm-dialog-body"></p>',
    '<div class="confirm-actions">',
    '<button type="button" data-confirm="cancel"></button>',
    '<button type="button" class="primary" data-confirm="run"></button>',
    '</div>',
  ].join('')
  dlg.querySelector('[data-confirm="cancel"]').addEventListener('click', () => settle(false))
  dlg.querySelector('[data-confirm="run"]').addEventListener('click', () => settle(true))
  dlg.addEventListener('cancel', (ev) => {
    ev.preventDefault()
    settle(false)
  })
  dlg.addEventListener('click', (ev) => {
    if (ev.target !== dlg) return
    const rect = dlg.getBoundingClientRect()
    const inside = ev.clientX >= rect.left && ev.clientX <= rect.right
      && ev.clientY >= rect.top && ev.clientY <= rect.bottom
    if (!inside) settle(false)
  })
  dlg.addEventListener('keydown', onKey)
  document.body.appendChild(dlg)
  return dlg
}

function buttons() {
  return [
    dlg.querySelector('[data-confirm="cancel"]'),
    dlg.querySelector('[data-confirm="run"]'),
  ]
}

function onKey(ev) {
  ev.stopPropagation()
  const [cancelBtn, runBtn] = buttons()
  if (ev.key === 'Tab') {
    ev.preventDefault()
    const list = [cancelBtn, runBtn]
    let i = list.indexOf(document.activeElement)
    if (i < 0) i = ev.shiftKey ? 0 : -1
    const dir = ev.shiftKey ? -1 : 1
    list[(i + dir + list.length) % list.length].focus()
    return
  }
  if (ev.key === 'Enter') {
    const cur = document.activeElement
    if (cur === cancelBtn || cur === runBtn) {
      ev.preventDefault()
      cur.click()
    }
  }
}

function settle(ok) {
  const cur = pending
  if (!cur) return
  pending = null
  if (dlg && dlg.open) dlg.close()
  const back = cur.returnTo
  if (back && back.isConnected && typeof back.focus === 'function') back.focus()
  cur.resolve(ok)
}

export function openConfirm({ title, body, returnTo }) {
  const el = ensure()
  const [cancelBtn, runBtn] = buttons()
  el.querySelector('#confirm-dialog-title').textContent = title
  el.querySelector('#confirm-dialog-body').textContent = body
  cancelBtn.textContent = t('confirm.cancel')
  runBtn.textContent = t('confirm.run')
  return new Promise((resolve) => {
    if (pending) pending.resolve(false)
    pending = { resolve, returnTo: returnTo || null }
    if (!el.open) el.showModal()
    cancelBtn.focus()
  })
}
