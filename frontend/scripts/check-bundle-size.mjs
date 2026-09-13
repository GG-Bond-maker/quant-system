import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { gzipSync } from 'node:zlib';

const dist = join(process.cwd(), 'dist', 'assets');
const maxRaw = 750 * 1024;
const maxGzip = 250 * 1024;
const offenders = [];

for (const file of readdirSync(dist)) {
  if (!/\.(js|css)$/.test(file)) continue;
  const bytes = readFileSync(join(dist, file));
  const raw = bytes.byteLength;
  const gzip = gzipSync(bytes).byteLength;
  if (raw > maxRaw || gzip > maxGzip) offenders.push({ file, raw, gzip });
}

if (offenders.length) {
  for (const item of offenders) {
    console.error(`${item.file}: ${item.raw} bytes raw, ${item.gzip} bytes gzip`);
  }
  process.exit(1);
}
console.log(`Bundle budget passed: raw <= ${maxRaw} B, gzip <= ${maxGzip} B`);
