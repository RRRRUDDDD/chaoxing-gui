import { expect, it } from 'vitest';
import { filterLogs, formatLogsForCopy, redactLogText } from './logPresentation';

const logs = [
  { seq: 1, level: 'INFO', message: 'Course loaded', timestamp: 1 },
  { seq: 2, level: 'warn', message: 'Course skipped', timestamp: 2 },
  { seq: 3, level: 'error', message: 'Network failed', timestamp: 3 },
];
it('combines severity and keyword filters without changing the cache', () => {
  expect(filterLogs(logs, 'warnings', 'COURSE')).toEqual([logs[1]]);
  expect(filterLogs(logs, 'errors')).toEqual([logs[2]]);
  expect(filterLogs(logs, 'all', 'missing')).toEqual([]);
  expect(filterLogs(logs)).toEqual(logs);
  expect(logs).toHaveLength(3);
});
it.each([
  '{"Cookie":"sid=top-secret"}', '{"Authorization":"Basic top-secret"}',
  'password=top-secret', '"password": "top-secret"', "'api_key': 'top-secret'",
  'access_token=top-secret&next=1', 'Cookie: sid=top-secret; second=hidden',
  'Authorization: Bearer top-secret', 'https://name:top-secret@example.com/path',
  'https://example.com/?token=top-secret&next=1', 'Bearer top-secret',
])('masks common secret forms: %s', (text) => {
  expect(redactLogText(text)).not.toContain('top-secret');
  expect(redactLogText(text)).toContain('[REDACTED]');
});
it('copies only supplied results, preserving time and severity', () => {
  const text = formatLogsForCopy(filterLogs(logs, 'errors'));
  expect(text).toContain('[error] Network failed');
  expect(text).not.toContain('Course');
  expect(formatLogsForCopy([{ message: 'token=secret' }])).toContain('[--:--:--] [info] token=[REDACTED]');
});
