# Third-party software

- **SheetJS Community Edition 0.20.3** — Apache-2.0. Source and distribution: https://cdn.sheetjs.com/xlsx-0.20.3/package/ . Builds fetch the identical file from the versioned npm distribution https://registry.npmjs.org/@e965/xlsx/-/xlsx-0.20.3.tgz because the official CDN can reject hosted runners. Its SHA-256 is verified against the original pinned file. Local `dist/vendor/xlsx.full.min.js` includes its version/copyright header.
- **Pyodide 0.27.7** — Mozilla Public License 2.0, with CPython and other bundled components under their respective licenses. Source and license information: https://github.com/pyodide/pyodide/tree/0.27.7 . NPM distribution: https://registry.npmjs.org/pyodide/-/pyodide-0.27.7.tgz . Its runtime is included in `dist/vendor/pyodide/`.
- **DM Sans and Manrope** — Google Fonts, SIL Open Font License. The stylesheet requests these fonts remotely with system-font fallbacks.

No third-party service receives workbook contents through this application's code.
