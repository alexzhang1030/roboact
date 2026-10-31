# PCR map

One-line gist per route. Open the record before changing that area. There is no `docs/decisions/`; PCR here is the decision trail.

| Area | Record | Gist |
|---|---|---|
| What this is / non-goals | [../README.md](../../README.md) (enrolled) | Offline robot flow canvas; not a chat LLM; v1 is FakeAdapter only. |
| Fonts, Inter / JBM, light/dark | [theme-and-fonts.md](theme-and-fonts.md) | §17 is current. Package-static OFL fonts. Three-way appearance, no white flash. |
| Locale, catalogs, codes-only | [i18n.md](i18n.md) | `navigator.languages` (`zh*` → Chinese); catalogs as ES modules; codes on the wire; missing zh → en. |
| What the browser may store | [settings-split.md](settings-split.md) | `roboact.*` display prefs only. Timeouts and safety stay on the server. |
| Bind, Host/Origin, no auth | [auth-and-network.md](auth-and-network.md) | Loopback only. A40 checks. A39 deferred. SSH forward to open remotely. |
| MIT, GitHub, no PyPI | [licence-and-repo.md](licence-and-repo.md) | Copyright line is exact. First push is `main`; then branch + PR. |
| Executor, adapter, pauses | [execution-model.md](execution-model.md) | Codes in records. `motion` default. `precondition_wait_s`. No auto-retry after crash. |
| Product copy / how to run | [../../README.md](../../README.md) | Install, SSH forward, acceptance table. |
| Visual spec | [../../docs/ui-spec.md](../../docs/ui-spec.md) | Layout, status words, §17 fonts/themes. |
| Colour math | [../../docs/design/contrast.py](../../docs/design/contrast.py) | WCAG helpers; grid dots are decorative. |
