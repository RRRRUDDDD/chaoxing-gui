import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const crate = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src-tauri');
const read = (name) => fs.readFileSync(path.join(crate, name), 'utf8');
const sorted = (items) => [...items].sort();

// A command must be declared to tauri-build, granted to the main window and
// registered with the handler; missing any one of them breaks it at runtime.
test('IPC commands, capability grants and handlers stay in sync', () => {
  const manifest = read('build.rs').match(/commands\(&\[([\s\S]*?)\]\)/)[1];
  const declared = [...manifest.matchAll(/"([a-z_]+)"/g)].map((match) => match[1]);
  const handler = read('src/lib.rs').match(/generate_handler!\[([\s\S]*?)\]/)[1];
  const registered = handler.split(',').map((name) => name.trim()).filter(Boolean);
  const capability = JSON.parse(read('capabilities/main.json'));
  const granted = capability.permissions
    .filter((permission) => permission.startsWith('allow-'))
    .map((permission) => permission.slice('allow-'.length).replaceAll('-', '_'));

  assert.deepEqual(sorted(registered), sorted(declared));
  assert.deepEqual(sorted(granted), sorted(declared));
  for (const command of ['close_prompt_shown', 'close_choice', 'preferences_read', 'preferences_write']) {
    assert.ok(declared.includes(command), `${command} is not declared`);
  }
  assert.deepEqual(capability.windows, ['main']);
  // The page may only listen for the close prompt, never emit host events.
  const core = capability.permissions.filter((permission) => permission.startsWith('core:'));
  assert.deepEqual(sorted(core), ['core:event:allow-listen', 'core:event:allow-unlisten']);
});

test('the tray feature is enabled for the close-to-tray action', () => {
  assert.match(read('Cargo.toml'), /^tauri = \{[^}]*features = \[[^\]]*"tray-icon"/m);
});
