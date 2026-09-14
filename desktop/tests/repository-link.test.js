const { test } = require('node:test');
const assert = require('node:assert/strict');
const { REPOSITORY_URL, repositoryWindowHandler } = require('../repository-link');

test('opens the fixed repository in the system browser and never creates a renderer', async () => {
  const opened = [];
  const handler = repositoryWindowHandler(async (url) => opened.push(url), assert.fail);
  assert.deepEqual(handler({ url: REPOSITORY_URL }), { action: 'deny' });
  assert.deepEqual(opened, [REPOSITORY_URL]);
});

test('refuses every other URL, including lookalike hosts and local schemes', () => {
  const handler = repositoryWindowHandler(assert.fail, assert.fail);
  for (const url of [
    'https://github.com/', `${REPOSITORY_URL}/unexpected`,
    'https://github.com.evil.test/RRRRUDDDD/chaoxing-gui',
    'https://github.com@evil.test/RRRRUDDDD/chaoxing-gui',
    'http://github.com/RRRRUDDDD/chaoxing-gui',
    'file:///C:/Windows/System32/cmd.exe', 'javascript:alert(1)',
  ]) assert.deepEqual(handler({ url }), { action: 'deny' });
});

test('reports synchronous and asynchronous browser launch errors', async () => {
  const errors = [];
  const failure = new Error('no browser');
  for (const open of [() => { throw failure; }, async () => { throw failure; }]) {
    const handler = repositoryWindowHandler(open, (error) => errors.push(error));
    assert.deepEqual(handler({ url: REPOSITORY_URL }), { action: 'deny' });
  }
  await Promise.resolve();
  assert.deepEqual(errors, [failure, failure]);
});
