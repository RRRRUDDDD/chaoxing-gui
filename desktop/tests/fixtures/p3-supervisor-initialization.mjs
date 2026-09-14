// Protocol-only fixture. It never launches the executable in a start request.
import { appendFileSync } from 'node:fs';
import { createInterface } from 'node:readline';

const [record, mode, delayMs] = process.argv.slice(2);
const input = createInterface({ input: process.stdin });
let initialized = false;
let timer;
const reply = (request, result, error) => process.stdout.write(`${JSON.stringify({ id: request.id, ok: !error, result, error })}\n`);
input.on('line', (line) => {
  const request = JSON.parse(line);
  appendFileSync(record, `${line}\n`);
  if (request.operation === 'initialize') {
    if (mode === 'stall-initialize') return;
    timer = setTimeout(() => {
      initialized = true;
      reply(request, { protocolVersion: mode === 'invalid-protocol' ? 99 : 1 });
    }, Number(delayMs));
  } else if (request.operation === 'start') {
    if (!initialized) { reply(request, null, 'Supervisor must initialize before starting a host'); return; }
    if (mode !== 'stall-start') reply(request, { pid: process.pid, executable: request.specification.executable });
  } else if (request.operation === 'finish') {
    reply(request, { fallbackUsed: false, remaining: [], observed: [] });
  }
});
input.on('close', () => clearTimeout(timer));
