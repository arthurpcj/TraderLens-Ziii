# Third-Party Notices — vendored JS/CSS for the HTML pivot

The HTML pivot (`src/pivot.py` → `reports/pivot_latest.html`) inlines the
following third-party libraries from this directory. They are vendored
(checked into the repo) so the generated HTML is self-contained and
works fully offline, with no external CDN dependency.

All bundled files are MIT-licensed; their original copyright notices and
license terms apply unchanged. This file collects the attributions in
one place per the MIT license requirement.

---

## jQuery 3.7.1

- **File**: `jquery.min.js`
- **Upstream**: https://jquery.com (project) · https://github.com/jquery/jquery
- **License**: [MIT](https://github.com/jquery/jquery/blob/main/LICENSE.txt)
- **Copyright**: © OpenJS Foundation and other contributors
- The minified file retains its license header:
  `(c) OpenJS Foundation and other contributors | jquery.org/license`

## jQuery UI 1.13.2

- **File**: `jquery-ui.min.js`
- **Upstream**: https://jqueryui.com · https://github.com/jquery/jquery-ui
- **License**: [MIT](https://github.com/jquery/jquery-ui/blob/main/LICENSE.txt)
- **Copyright**: © jQuery Foundation and other contributors
- The minified file retains its license header:
  `Copyright jQuery Foundation and other contributors; Licensed MIT`

## PivotTable.js

- **Files**: `pivot.min.js`, `pivot.min.css`
- **Upstream**: https://pivottable.js.org · https://github.com/nicolaskruchten/pivottable
- **License**: [MIT](https://github.com/nicolaskruchten/pivottable/blob/master/LICENSE.md)
- **Copyright**: © Nicolas Kruchten

The minified `pivot.min.*` files in this directory were stripped of
their license header by the upstream minification step. The original
copyright + MIT license apply as recorded above; the unminified source
at the upstream link is the authoritative copy.

---

## Why these are vendored

TraderLens follows a stdlib-first ethos (see [ADR-001](../../docs/decisions/001-drop-ibflex.md))
and the HTML pivot is designed to be a single self-contained file the
user can open offline, send as an email attachment, or diff side-by-side
against another run. CDN dependencies would defeat that. Versions are
pinned here so the generated HTML is reproducible.
