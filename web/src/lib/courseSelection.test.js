import { expect, it } from 'vitest';
import { restoreCourseSelection } from './courseSelection';

const courses = [{ courseId: '1' }, { courseId: '2' }];

it('restores only the current account selection intersected with actual courses', () => {
  const config = { selectedCoursesByAccount: { alice: ['obsolete', 2, 2], bob: ['1'] } };
  expect(restoreCourseSelection(config, 'alice', courses)).toEqual({ ids: ['2'], saved: true });
  expect(restoreCourseSelection(config, 'bob', courses)).toEqual({ ids: ['1'], saved: true });
});

it('preserves an empty or fully stale saved selection', () => {
  expect(restoreCourseSelection({ selectedCoursesByAccount: { alice: ['gone'] } }, 'alice', courses).ids).toEqual([]);
  expect(restoreCourseSelection({ selectedCoursesByAccount: { alice: [] } }, 'alice', courses).ids).toEqual([]);
});

it('ignores old unscoped selections and explicitly checks every course for new accounts', () => {
  expect(restoreCourseSelection({ selectedCourses: ['foreign'] }, 'new', courses)).toEqual({ ids: ['1', '2'], saved: false });
  expect(restoreCourseSelection({ selectedCoursesByAccount: { alice: ['1'] } }, 'new', courses).ids).toEqual(['1', '2']);
});
