/* Pull the geometry core out of index.html so the tests exercise the shipped file. */
const fs = require('fs');
const html = fs.readFileSync(__dirname + '/index.html', 'utf8');
const m = /<script id="kumiko-core">([\s\S]*?)<\/script>/.exec(html);
if (!m) { console.error('core script not found in index.html'); process.exit(1); }
fs.writeFileSync(__dirname + '/extracted.js', m[1] + '\nmodule.exports = Kumiko;\n');
console.log('extracted', m[1].length, 'chars from index.html');
