# UI spec

Canvas + top bar + palette + inspector. Status words and chrome come from `web/i18n/*.js`. Backend sends codes.

**Themes:** light / dark / follow system (`roboact.theme`). Fonts: Inter (UI), JetBrains Mono (values), system CJK. Formula: `docs/design/contrast.py`. Text ≥ 4.5:1; chrome ≥ 3:1.

**Language:** follow browser / English / Chinese (`roboact.lang`). First `zh*` in `navigator.languages` → Chinese, else English. Missing zh key falls back to English.

**Connection drop (top bar):**

- `operator_lost` → Paused · page disconnected
- `adapter_lost` → Paused · robot controller lost (Continue off while disconnected)

Stop is not e-stop. Images only on the Output tab. Layout mockups: [design/](design/).

### 17.3 Tokens

| token | use | light | dark |
|---|---|---|---|
| `--bg` | canvas | `#F6F6F4` | `#141413` |
| `--grid` | grid dots | `#E2E2DD` | `#2A2A27` |
| `--surface` | panels, nodes | `#FFFFFF` | `#1E1E1C` |
| `--surface2` | code, disabled | `#FAFAF8` | `#232321` |
| `--text` | body | `#1A1A18` | `#ECECE8` |
| `--muted` | secondary | `#5C5C57` | `#A3A39C` |
| `--border` | node stroke | `#C9C9C3` | `#3D3D39` |
| `--control` | control stroke | `#85857F` | `#73736C` |
| `--edge` | edges, ports | `#8A8A84` | `#77776F` |
| `--hair` | rules | `#E6E6E1` | `#2E2E2B` |
| `--blue` | running, primary | `#1F6FEB` | `#4C8DF6` |
| `--green` | done | `#1A7F37` | `#3FB950` |
| `--red` | error, adapter lost | `#C62828` | `#F06A6A` |
| `--amber` | pause, unknown | `#8F6000` | `#D4A72C` |
| `--redsoft` | error wash | `#FBEAEA` | `#3A2222` |
| `--ambersoft` | pause wash | `#FBF3E0` | `#3A3020` |
| `--onblue` | on primary | `#FFFFFF` | `#0B1220` |

### 17.4 Contrast (`design/contrast.py`)

| pair | light | dark | min |
|---|---|---|---|
| `--text` / `--surface` | 17.43 | 14.10 | 4.5 |
| `--muted` / `--surface` | 6.72 | 6.58 | 4.5 |
| `--muted` / `--surface2` | 6.43 | 6.20 | 4.5 |
| `--amber` / `--surface` | 5.47 | 7.44 | 4.5 |
| `--amber` / `--ambersoft` | 4.95 | 5.77 | 4.5 |
| `--red` / `--surface` | 5.62 | 5.55 | 4.5 |
| `--red` / `--redsoft` | 4.83 | 4.87 | 4.5 |
| `--blue` / `--surface` | 4.63 | 5.13 | 4.5 |
| `--green` / `--surface` | 5.08 | 6.57 | 4.5 |
| `--onblue` / `--blue` | 4.63 | 5.75 | 4.5 |
| `--edge` / `--bg` | 3.21 | 4.08 | 3 |
| `--control` / `--surface` | 3.71 | 3.50 | 3 |
| `--control` / `--bg` | 3.43 | 3.86 | 3 |
| `--grid` / `--bg` | 1.20 | 1.28 | decorative |
