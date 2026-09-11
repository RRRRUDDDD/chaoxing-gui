// Exercise the original Electron main/preload/store with a synthetic backend.
// Only the Python entry point and userData path are substituted in this fixture.
const fs = require('node:fs');
const path = require('node:path');
const { app } = require('electron');
const processes = require('node:child_process');
app.commandLine.appendSwitch('force-device-scale-factor', '1');

if (!process.env.P2_ELECTRON_PROFILE || !process.env.P2_FIXTURE_ROOT) {
  throw new Error('P2 fixture requires isolated profiles');
}
fs.mkdirSync(process.env.P2_ELECTRON_PROFILE, { recursive: true });
app.setPath('userData', process.env.P2_ELECTRON_PROFILE);
if (process.env.P2_HIDE_WINDOW === '1') {
  app.on('browser-window-created', (_event, window) => window.hide());
}
const spawn = processes.spawn;
processes.spawn = (command, args, options) => {
  if (command === 'python' && args?.length === 1 && args[0] === 'app.py') {
    return spawn(process.env.P2_PYTHON || 'python', [path.join(__dirname, 'p2_backend.py')], options);
  }
  return spawn(command, args, options);
};
require('../../main.js');
