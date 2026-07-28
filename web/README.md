# Configurator

`index.html` is the whole app — self-contained, no build step, no network. Open it in a
browser, or serve the directory.

## Tests

Both are headless and check the file that actually ships: the geometry core is extracted
from `index.html` rather than read from a separate copy.

```bash
# geometry core, cross-checked against the Python generator's measured output
node extract.js && node core.test.js ./extracted.js

# the real page in Chromium: controls, warnings, downloads, both themes
npm install --no-save playwright && node page.test.js
```

`core.test.js` compares part volumes to `kumiko_lamp.py` (base 0.00%, post 0.06%, adapter
ring 0.25%), checks pattern segment counts slat for slat, and verifies every surface is
closed via the area-weighted normal integral. `page.test.js` drives the sliders, confirms
an unassemblable configuration blocks export, and loads a downloaded STL back as a mesh.
