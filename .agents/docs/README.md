# PCR map

One-line gist per route. Open the record before changing that area. There is no `docs/decisions/`; PCR here is the decision trail.

| Area | Record | Gist |
|---|---|---|
| What this is / non-goals | [../README.md](../../README.md) (enrolled) | Offline robot flow canvas; not a chat LLM; v1 is FakeAdapter only. |
| Fonts, Inter / JBM, light/dark, reduced motion | [theme-and-fonts.md](theme-and-fonts.md) | §17 is current. Package-static OFL fonts. Colour/icon only when motion is reduced. |
| Locale, catalogs, codes-only | [i18n.md](i18n.md) | `navigator.languages` (`zh*` → Chinese); catalogs as ES modules; codes on the wire; no r-token keys. |
| What the browser may store | [settings-split.md](settings-split.md) | `roboact.*` display prefs only. Timeouts and safety stay on the server. |
| Bind, Host/Origin, no auth | [auth-and-network.md](auth-and-network.md) | Auth deferred; no `--host`; SSH forwarding for remote use. A40 on. A39 deferred. |
| MIT, GitHub, no PyPI, hygiene | [licence-and-repo.md](licence-and-repo.md) | Copyright line is exact. `git ls-files` private-IP scan. First push is `main`; then branch + PR. |
| Executor, adapter, pauses | [execution-model.md](execution-model.md) | Codes in records. `motion` default. `precondition_wait_s`. No auto-retry after crash. |
| Python / uv / ruff / pyright | [toolchain.md](toolchain.md) | Python 3.12. No rclpy. `scripts/check.sh`. |
| Product copy / how to run | [../../README.md](../../README.md) | Install with uv, SSH forward, acceptance table. |
| Visual spec | [../../docs/ui-spec.md](../../docs/ui-spec.md) | Layout, status words, §17 fonts/themes, §18 language. |
| Colour math | [../../docs/design/contrast.py](../../docs/design/contrast.py) | WCAG helpers; grid dots are decorative. |
