import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { before, after } from 'node:test';
import { fileURLToPath } from 'node:url';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const callback = path.join(desktop, 'scripts/bundle-sign.ps1');
const version = JSON.parse(fs.readFileSync(path.join(desktop, 'package.json'), 'utf8')).version;
const windowsTest = process.platform === 'win32' ? test : test.skip;
const unavailableThumbprint = '0'.repeat(40);
const marker = '__TAURI_BUNDLE_TYPE_VAR_NSS';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const quote = value => `'${String(value).replaceAll("'", "''")}'`;
let work;
let unsignedPe;
let signedSource;

function powershell(args) {
  const result = spawnSync('pwsh.exe', ['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', ...args], {
    encoding: 'utf8', windowsHide: true, timeout: 30_000, maxBuffer: 4 * 1024 * 1024,
  });
  assert.ifError(result.error);
  return { ...result, output: `${result.stdout}\n${result.stderr}` };
}

function code(source) {
  const script = `$ErrorActionPreference = 'Stop'; [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); ${source}`;
  return powershell(['-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')]);
}

function succeeds(result) { assert.equal(result.status, 0, result.output); }
function fails(result, expression) {
  assert.notEqual(result.status, 0, result.output);
  assert.match(result.output, expression);
}

function removeWithin(parent, directory) {
  assert.ok(path.resolve(directory).startsWith(path.resolve(parent) + path.sep));
  fs.rmSync(directory, { recursive: true, force: true });
}

before(() => {
  if (process.platform !== 'win32') return;
  work = fs.mkdtempSync(path.join(os.tmpdir(), 'chaoxing-signing-tests-'));
  const assembly = path.join(work, 'unsigned-fixture.dll');
  // Compile an inert PE, never run it, and never create/import a certificate.
  const prepared = code(`
    Add-Type -TypeDefinition 'public sealed class BundleSignFixture { public int Value() { return 7; } }' -OutputAssembly ${quote(assembly)} -OutputType Library | Out-Null
    $unsigned = Get-AuthenticodeSignature -LiteralPath ${quote(assembly)}
    if ($unsigned.Status -ne 'NotSigned') { throw 'Fixture must be an unsigned PE' }
    $vendor = $null
    foreach ($candidate in @((Join-Path $PSHOME 'pwsh.exe'), (Join-Path $env:WINDIR 'System32/cmd.exe'))) {
      $signature = Get-AuthenticodeSignature -LiteralPath $candidate
      if ($signature.Status -eq 'Valid' -and $signature.SignatureType -eq 'Authenticode') {
        $vendor = $candidate
        break
      }
    }
    @{ signedSource=$vendor } | ConvertTo-Json -Compress
  `);
  succeeds(prepared);
  unsignedPe = fs.readFileSync(assembly);
  signedSource = JSON.parse(prepared.stdout.trim()).signedSource;
});

after(() => {
  if (work) removeWithin(os.tmpdir(), work);
});

function fixture(t) {
  const root = fs.mkdtempSync(path.join(work, 'case-'));
  const links = [];
  t.after(() => {
    for (const link of links.reverse()) {
      const info = fs.lstatSync(link, { throwIfNoEntry: false });
      if (info) {
        assert.ok(info.isSymbolicLink(), 'only unlink the junction this fixture created');
        assert.ok(path.resolve(link).startsWith(root + path.sep));
        fs.unlinkSync(link);
      }
    }
    removeWithin(work, root);
  });
  const backend = path.join(root, '资源 staging/backend');
  const manifest = path.join(root, '资源 staging/backend-manifest.json');
  const host = path.join(root, 'host 中文/chaoxing-gui-tauri.exe');
  const recordDirectory = path.join(root, 'unique capture 中文');
  const record = path.join(recordDirectory, 'host.json');
  const relative = '_internal/保持/helper.dll';
  const resource = path.join(backend, relative);
  fs.mkdirSync(path.dirname(resource), { recursive: true });
  fs.mkdirSync(path.dirname(host), { recursive: true });
  fs.mkdirSync(recordDirectory);
  fs.writeFileSync(path.join(backend, 'chaoxing-backend.exe'), unsignedPe);
  fs.writeFileSync(resource, unsignedPe);
  fs.writeFileSync(host, Buffer.concat([unsignedPe, Buffer.from(marker)]));
  const document = {
    schemaVersion: 1, kind: 'chaoxing-backend', version, entryPoint: 'chaoxing-backend.exe',
    files: ['chaoxing-backend.exe', relative].map(name => ({ path: name, length: unsignedPe.length, sha256: hash(unsignedPe) })),
    directories: ['_internal', '_internal/保持'],
  };
  const saveManifest = () => fs.writeFileSync(manifest, JSON.stringify(document));
  saveManifest();
  return { root, backend, manifest, host, record, recordDirectory, resource, relative, document, saveManifest, links };
}

function invoke(f, file, overrides = {}) {
  const parameters = {
    Path: file, CertificateThumbprint: unavailableThumbprint, ExpectedHostPath: f.host,
    HostRecordPath: f.record, BackendDirectory: f.backend, BackendManifestPath: f.manifest, ...overrides,
  };
  return powershell(['-File', callback, ...Object.entries(parameters).flatMap(([key, value]) => [`-${key}`, value])]);
}

function updateResourceRecord(f, bytes) {
  fs.writeFileSync(f.resource, bytes);
  const entry = f.document.files.find(value => value.path === f.relative);
  entry.length = bytes.length;
  entry.sha256 = hash(bytes);
  f.saveManifest();
}

windowsTest('Bundle callback preserves exact unsigned backend EXE/DLL bytes without requiring a certificate', t => {
  const f = fixture(t);
  for (const resource of [f.resource, path.join(f.backend, 'chaoxing-backend.exe')]) {
    const before = fs.readFileSync(resource);
    const result = invoke(f, resource);
    succeeds(result);
    const report = JSON.parse(result.stdout);
    assert.equal(report.action, 'preserved-backend-resource');
    assert.equal(report.signatureStatus, 'NotSigned');
    assert.equal(report.sha256, hash(before));
    assert.deepEqual(fs.readFileSync(resource), before);
  }
  assert.equal(fs.existsSync(f.record), false);
});

windowsTest('Bundle callback preserves an existing valid vendor signature with its original signer', t => {
  if (!signedSource) { t.skip('No existing valid embedded vendor signature is available'); return; }
  const f = fixture(t);
  const bytes = fs.readFileSync(signedSource);
  updateResourceRecord(f, bytes);
  const result = invoke(f, f.resource);
  succeeds(result);
  const report = JSON.parse(result.stdout);
  assert.equal(report.signatureStatus, 'Valid');
  assert.ok(report.signerThumbprint);
  assert.notEqual(report.signerThumbprint, unavailableThumbprint);
  assert.deepEqual(fs.readFileSync(f.resource), bytes);
  assert.equal(fs.existsSync(f.record), false);
});

windowsTest('Bundle callback rejects an invalid existing signature even when its file hash matches the manifest', t => {
  if (!signedSource) { t.skip('No existing valid embedded vendor signature is available'); return; }
  const f = fixture(t);
  const bytes = fs.readFileSync(signedSource);
  const pe = bytes.readUInt32LE(0x3c);
  const section = pe + 24 + bytes.readUInt16LE(pe + 20);
  const rawOffset = bytes.readUInt32LE(section + 20);
  assert.ok(rawOffset > 0 && rawOffset < bytes.length);
  bytes[rawOffset] ^= 1; // Corrupt signed content in the owned copy only.
  updateResourceRecord(f, bytes);
  fails(invoke(f, f.resource), /Invalid existing backend resource signature/);
  assert.deepEqual(fs.readFileSync(f.resource), bytes);
  assert.equal(fs.existsSync(f.record), false);
});

windowsTest('Backend length/hash changes and unlisted resources fail before any signing', t => {
  const f = fixture(t);
  const changed = Buffer.from(unsignedPe);
  changed[changed.length - 1] ^= 1;
  fs.writeFileSync(f.resource, changed);
  fails(invoke(f, f.resource), /Backend resource length\/hash mismatch/);
  fs.writeFileSync(f.resource, Buffer.concat([unsignedPe, Buffer.from('changed length')]));
  fails(invoke(f, f.resource), /Backend resource length\/hash mismatch/);
  const unlisted = path.join(f.backend, 'unlisted.dll');
  fs.writeFileSync(unlisted, unsignedPe);
  fails(invoke(f, unlisted), /not in the backend manifest/);
  assert.deepEqual(fs.readFileSync(unlisted), unsignedPe);
  assert.equal(fs.existsSync(f.record), false);
});

windowsTest('Backend manifest schema, duplicate paths, and traversal entries are refused', t => {
  const f = fixture(t);
  const original = structuredClone(f.document);
  const variants = [
    { ...original, kind: 'portable' },
    { ...original, files: [...original.files, { ...original.files[1], path: f.relative.toUpperCase() }] },
    { ...original, files: [...original.files, { ...original.files[1], path: '../outside.dll' }] },
    { ...original, files: original.files.map(entry => ({ ...entry, length: String(entry.length) })) },
  ];
  for (const document of variants) {
    fs.writeFileSync(f.manifest, JSON.stringify(document));
    fails(invoke(f, f.resource), /manifest|Unsafe relative path/i);
  }
  assert.deepEqual(fs.readFileSync(f.resource), unsignedPe);
});

windowsTest('Bundle callback rejects resource junctions and record paths inside the frozen backend', t => {
  const f = fixture(t);
  fails(invoke(f, f.host, { HostRecordPath: path.join(f.backend, 'capture.json') }), /overlapping/);
  const internal = path.join(f.backend, '_internal');
  const external = path.join(f.root, 'external sentinel');
  assert.ok(internal.startsWith(f.root + path.sep) && external.startsWith(f.root + path.sep));
  fs.renameSync(internal, external);
  fs.symlinkSync(external, internal, 'junction');
  f.links.push(internal);
  const before = fs.readFileSync(path.join(external, '保持/helper.dll'));
  fails(invoke(f, f.resource), /reparse|junction/i);
  assert.deepEqual(fs.readFileSync(path.join(external, '保持/helper.dll')), before);
});

windowsTest('NSIS host requires one NSS marker before it can reach the signer', t => {
  const f = fixture(t);
  for (const text of ['', '__TAURI_BUNDLE_TYPE_VAR_UNK', marker + marker, marker + '__TAURI_BUNDLE_TYPE_VAR_UNK']) {
    const bytes = Buffer.concat([unsignedPe, Buffer.from(text)]);
    fs.writeFileSync(f.host, bytes);
    fails(invoke(f, f.host), /exactly one __TAURI_BUNDLE_TYPE_VAR_NSS/);
    assert.deepEqual(fs.readFileSync(f.host), bytes);
    assert.equal(fs.existsSync(f.record), false);
  }
});

windowsTest('Host capture refuses old records, missing capture directories, and invalid input paths', t => {
  const f = fixture(t);
  const old = '{"previous":"must survive"}';
  fs.writeFileSync(f.record, old);
  fails(invoke(f, f.host), /overwrite an existing host record/);
  assert.equal(fs.readFileSync(f.record, 'utf8'), old);
  fs.unlinkSync(f.record);
  fails(invoke(f, f.host, { HostRecordPath: path.join(f.root, 'absent', 'host.json') }), /existing unique capture directory/);
  fails(invoke(f, path.join(f.root, 'missing.exe')), /existing regular file/);
  fails(invoke(f, f.backend), /existing regular file/);
  fails(invoke(f, f.host, { CertificateThumbprint: 'not-a-thumbprint' }), /Invalid signing certificate thumbprint/);
  assert.equal(fs.existsSync(f.record), false);
});

windowsTest('A valid marker without the requested private key fails without signing or publishing a record', t => {
  const f = fixture(t);
  const before = fs.readFileSync(f.host);
  fails(invoke(f, f.host), /certificate with private key is unavailable/);
  assert.deepEqual(fs.readFileSync(f.host), before);
  assert.deepEqual(fs.readdirSync(f.recordDirectory), []);
  // A sibling with the same backend prefix must take the ordinary signer path.
  const sibling = path.join(f.root, '资源 staging/backend-other.dll');
  fs.writeFileSync(sibling, unsignedPe);
  fails(invoke(f, sibling), /certificate with private key is unavailable/);
  assert.deepEqual(fs.readFileSync(sibling), unsignedPe);
  assert.equal(fs.existsSync(f.record), false);
});
