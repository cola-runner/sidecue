// Development-only asset build. AppKit loads the resulting PNGs at runtime.
import { mkdir, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const sharp = require('sharp');
const destination = fileURLToPath(new URL('../sidecue/assets/', import.meta.url));
await mkdir(destination, { recursive: true });
const base = 'https://raw.githubusercontent.com/lucide-icons/lucide/0.468.0';
for (const name of ['audio-lines', 'arrow-up', 'copy', 'pin', 'pin-off', 'plus', 'trash-2', 'file-text', 'info', 'check']) {
  const response = await fetch(`${base}/icons/${name}.svg`);
  if (!response.ok) throw new Error(`${name}: ${response.status}`);
  const svg = await response.text();
  for (const [variant, color] of Object.entries({ ink: '#626B66', accent: '#217354', white: '#FFFFFF' })) {
    await sharp(Buffer.from(svg.replace('currentColor', color)))
      .resize(32, 32).png().toFile(`${destination}/${name}-${variant}.png`);
  }
}
const license = await fetch(`${base}/LICENSE`);
if (!license.ok) throw new Error(`License: ${license.status}`);
await writeFile(`${destination}/LUCIDE-LICENSE`, await license.text());
