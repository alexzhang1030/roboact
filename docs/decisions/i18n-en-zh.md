# i18n: English and zh-CN

**Decided:** Two catalogs, `web/i18n/en.js` and `web/i18n/zh-CN.js`, plain ES modules, no build. Same key set. `t()` uses the active locale, then English, then an empty string — never the key name.

First load reads `navigator.languages`. The first entry starting with `zh` is Chinese; anything else, including French or a missing list, is English. That rule lives in one function, `detectBrowserLang` in `web/js/prefs.js`. Flip `FOLLOW_BROWSER_LANG` to `false` (and the matching constant in `web/index.html`) for always-English. ui-spec §18.1 originally said first load is always English; PRD v1.4.6 and Alex’s correction override that.

Manual pick persists as `roboact.lang` = `system` | `en` | `zh-CN`. Follow-browser clears back to the detector. A second tab follows via `storage`. Adapter titles: `labels[locale]`, then `labels.en`, then `name`, then `id`. Operator-typed names, notes, values, and units are not translated.

**Why:** Alex asked for English and Chinese, default English, and “read the browser”. Catalogs as modules keep the zero-build rule. One detector function makes a later flip a one-line change.

**Who:** Alex (rule), Steve (catalogs, no hardcoded UI), Rams (§18 copy and overflow)

**Date:** 2026-10-31

**Spec:** [PRD §5 language / 19.6](../prd.md), [UI spec §18](../ui-spec.md) (the §18.1 override note)
