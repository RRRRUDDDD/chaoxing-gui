// Local supervisor regression fixture. It never imports application code.
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

if (process.argv.includes('--grandchild')) {
  setInterval(() => {}, 1000);
} else {
  if (process.argv.includes('--tree')) {
    spawn(process.execPath, [fileURLToPath(import.meta.url), '--grandchild'], {
      windowsHide: true, stdio: 'ignore',
    });
  }
  process.stdin.resume();
  process.stdin.on('end', () => process.exit(0));
  setTimeout(() => console.log('stdin-open'), 50);
  setInterval(() => {}, 1000);
}
