import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import test from 'node:test';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const quote = value => `'${value.replaceAll("'", "''")}'`;
const windowsTest = process.platform === 'win32' ? test : test.skip;
const digest = value => createHash('sha256').update(value).digest('hex');

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'chaoxing-nsis-content-'));
  t.after(() => {
    assert.ok(root.startsWith(path.join(os.tmpdir(), 'chaoxing-nsis-content-')));
    fs.rmSync(root, { recursive: true, force: true });
  });
  const bytes = new Map([
    ['chaoxing-gui-tauri.exe', 'MZ fixture host, never executed'],
    ['backend/chaoxing-backend.exe', 'MZ fixture backend, never executed'],
    ['backend/_internal/nested 中文/model.onnx', 'complete nested dependency'],
  ]);
  for (const [name, value] of bytes) {
    fs.mkdirSync(path.dirname(path.join(root, 'payload', name)), { recursive: true });
    fs.writeFileSync(path.join(root, 'payload', name), value);
  }
  const files = [...bytes].map(([name, value]) => ({ path: name, length: Buffer.byteLength(value), sha256: digest(value) }));
  const manifest = path.join(root, 'manifest.json');
  fs.writeFileSync(manifest, JSON.stringify({ files }));
  return { root, bytes, files, manifest };
}

function run(f, entries = f.files, { listingType = 'Nsis', hash = true } = {}) {
  const listing = path.join(f.root, 'listing.txt');
  fs.writeFileSync(listing, `7-Zip\n\n--\nPath = fixture.exe\nType = ${listingType}\n\n----------\n` +
    entries.map(entry => `Path = ${entry.path.replaceAll('/', '\\')}\nSize = ${entry.length ?? ''}\nAttributes = ${entry.attributes ?? 'A'}\n\n`).join(''));
  const code = `
    $ErrorActionPreference = 'Stop'
    . ${quote(path.join(desktop, 'scripts/package-common.ps1'))}
    . ${quote(path.join(desktop, 'scripts/nsis-content.ps1'))}
    $entries = @(Read-NsisContentListing ([IO.File]::ReadAllText(${quote(listing)})))
    $manifest = Read-PackageJson ${quote(f.manifest)}
    Assert-NsisContent -Entries $entries -Manifest $manifest ${hash ? `-ExtractedDirectory ${quote(path.join(f.root, 'payload'))}` : ''}
  `;
  const result = spawnSync('pwsh', ['-NoProfile', '-EncodedCommand', Buffer.from(code, 'utf16le').toString('base64')], {
    encoding: 'utf8', windowsHide: true, timeout: 20_000,
  });
  assert.ifError(result.error);
  return { ...result, output: `${result.stdout}\n${result.stderr}` };
}

windowsTest('NSIS complete payload matches the portable manifest including nested Unicode dependencies', t => {
  const f = fixture(t);
  const entries = [{ path: '$PLUGINSDIR/System.dll', length: 12288 }, ...f.files];
  const result = run(f, entries);
  assert.equal(result.status, 0, result.output);
});

windowsTest('NSIS missing, foreign, duplicate, traversal and wrong-format payloads are rejected before extraction', t => {
  const f = fixture(t);
  for (const entries of [
    f.files.slice(0, -1),
    [...f.files, { path: 'fake-backend.exe', length: 5 }],
    [...f.files, { path: 'resources/app.asar', length: 5 }],
    [...f.files, { ...f.files[0], path: 'CHAOXING-GUI-TAURI.EXE' }],
    [...f.files, { path: '../outside.txt', length: 5 }],
    [...f.files, { path: '$PLUGINSDIR/fake-backend.exe', length: 5 }],
    [...f.files, { path: 'backend/alias', length: 5, attributes: 'L' }],
  ]) {
    const result = run(f, entries, { hash: false });
    assert.notEqual(result.status, 0, result.output);
  }
  assert.notEqual(run(f, f.files, { listingType: 'zip', hash: false }).status, 0);
});

windowsTest('NSIS equal-size content tampering and extracted junctions cannot pass hash verification', t => {
  const f = fixture(t);
  const name = f.files[2].path;
  const file = path.join(f.root, 'payload', name);
  fs.writeFileSync(file, 'x'.repeat(f.files[2].length));
  assert.notEqual(run(f).status, 0);
  fs.writeFileSync(file, f.bytes.get(name));
  const parent = path.dirname(file);
  const moved = path.join(f.root, 'moved');
  fs.renameSync(parent, moved);
  fs.symlinkSync(moved, parent, 'junction');
  assert.notEqual(run(f).status, 0);
});

windowsTest('NSIS generated uninstaller and decoder metadata are distinct from application payload', t => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.root, 'payload/uninstall.exe'), 'MZ generated uninstaller fixture');
  const entries = [...f.files, { path: 'uninstall.exe', length: null },
    { path: '[NSIS].nsi', length: 123 }, { path: '[LICENSE].txt', length: 456 }];
  assert.equal(run(f, entries).status, 0);
  assert.notEqual(run(f, [...f.files, { path: 'unexpected.exe', length: null }], { hash: false }).status, 0);
  fs.writeFileSync(path.join(f.root, 'payload/uninstall.exe'), 'invalid executable header');
  assert.notEqual(run(f, entries).status, 0);
});

function script(code) {
  const result = spawnSync('pwsh', ['-NoProfile', '-EncodedCommand', Buffer.from(`
    $ErrorActionPreference = 'Stop'
    . ${quote(path.join(desktop, 'scripts/package-common.ps1'))}
    . ${quote(path.join(desktop, 'scripts/nsis-content.ps1'))}
    ${code}
  `, 'utf16le').toString('base64')], { encoding: 'utf8', windowsHide: true, timeout: 20_000 });
  assert.ifError(result.error);
  return { ...result, output: `${result.stdout}\n${result.stderr}` };
}

windowsTest('NSIS host expectation accounts for exactly the Tauri bundle marker without modifying the source', t => {
  const f = fixture(t);
  const host = path.join(f.root, 'chaoxing-gui-tauri.exe');
  const original = 'MZ inert host __TAURI_BUNDLE_TYPE_VAR_UNK complete code bytes';
  fs.writeFileSync(host, original);
  const result = script(`Get-NsisHostExpectation -HostPath ${quote(host)} | ConvertTo-Json -Compress`);
  assert.equal(result.status, 0, result.output);
  const expected = JSON.parse(result.stdout);
  assert.equal(expected.sha256, digest(original.replace('_VAR_UNK', '_VAR_NSS')));
  assert.equal(expected.sourceSha256, digest(original));
  assert.equal(expected.length, Buffer.byteLength(original));
  assert.equal(expected.signed, false);
  assert.equal(fs.readFileSync(host, 'utf8'), original);
  for (const invalid of [original.replace('_VAR_UNK', '_VAR_NSS'), original + '__TAURI_BUNDLE_TYPE_VAR_UNK', 'MZ missing marker']) {
    fs.writeFileSync(host, invalid);
    assert.notEqual(script(`Get-NsisHostExpectation -HostPath ${quote(host)}`).status, 0);
  }
});

windowsTest('NSIS artifact record binds both artifacts and validates every byte with a distinct host hash', t => {
  const f = fixture(t);
  const version = JSON.parse(fs.readFileSync(path.join(desktop, 'package.json'), 'utf8')).version;
  const installer = path.join(f.root, `chaoxing-gui-tauri-setup-${version}-windows-x64.exe`);
  const portable = path.join(f.root, `chaoxing-gui-tauri-portable-${version}-windows-x64.zip`);
  const host = path.join(f.root, 'chaoxing-gui-tauri.exe');
  const original = 'MZ inert host __TAURI_BUNDLE_TYPE_VAR_UNK complete code bytes';
  const bundled = original.replace('_VAR_UNK', '_VAR_NSS');
  fs.writeFileSync(host, original);
  fs.writeFileSync(installer, 'MZ inert installer; never executed');
  fs.writeFileSync(portable, 'inert archive identity fixture; never extracted');
  const files = f.files.map(file => file.path === 'chaoxing-gui-tauri.exe'
    ? { path: file.path, length: Buffer.byteLength(original), sha256: digest(original) } : file);
  fs.writeFileSync(f.manifest, JSON.stringify({ schemaVersion: 1, kind: 'chaoxing-gui-tauri-portable', version,
    entryPoint: 'chaoxing-gui-tauri.exe', platform: 'windows-x64', files, directories: [] }));
  const create = script(`$expected = Get-NsisHostExpectation -HostPath ${quote(host)}
    Write-NsisArtifactManifest -InstallerPath ${quote(installer)} -PortablePath ${quote(portable)} -HostExpectation $expected`);
  assert.equal(create.status, 0, create.output);
  fs.writeFileSync(path.join(f.root, 'payload/chaoxing-gui-tauri.exe'), bundled);
  const validate = () => script(`$portableManifest = Read-PackageJson ${quote(f.manifest)}
    $expected = Read-NsisPayloadManifest -InstallerPath ${quote(installer)} -PortablePath ${quote(portable)} -PortableManifest $portableManifest
    Assert-NsisContent -Entries @($expected.files) -Manifest $expected -ExtractedDirectory ${quote(path.join(f.root, 'payload'))}`);
  assert.equal(validate().status, 0);
  fs.writeFileSync(path.join(f.root, 'payload/chaoxing-gui-tauri.exe'), bundled.replace('complete', 'tampered'));
  assert.notEqual(validate().status, 0);
  fs.writeFileSync(path.join(f.root, 'payload/chaoxing-gui-tauri.exe'), bundled);
  fs.appendFileSync(installer, 'changed');
  assert.notEqual(validate().status, 0);
  fs.writeFileSync(installer, 'MZ inert installer; never executed');
  fs.appendFileSync(portable, 'changed');
  assert.notEqual(validate().status, 0);
  fs.writeFileSync(portable, 'inert archive identity fixture; never extracted');
  fs.rmSync(`${installer}.manifest.json`);
  assert.notEqual(validate().status, 0);
});

windowsTest('NSIS signed-host capture must originate from the exact unsigned compiler output', t => {
  const f = fixture(t);
  const host = path.join(f.root, 'chaoxing-gui-tauri.exe');
  const record = path.join(f.root, 'signed-host.json');
  const original = 'MZ inert host __TAURI_BUNDLE_TYPE_VAR_UNK code bytes';
  fs.writeFileSync(host, original);
  fs.writeFileSync(record, JSON.stringify({ schemaVersion: 1, kind: 'chaoxing-gui-tauri-signed-nsis-host',
    sourcePath: host, bundleType: 'nsis', preSignSha256: '0'.repeat(64), preSignLength: original.length,
    length: original.length + 200, sha256: '1'.repeat(64), signerThumbprint: 'A'.repeat(40) }));
  assert.notEqual(script(`Get-NsisHostExpectation -HostPath ${quote(host)} -SignedHostRecordPath ${quote(record)}`).status, 0);
});
