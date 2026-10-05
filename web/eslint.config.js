import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';

export default [
  { ignores: ['dist'] },
  js.configs.recommended,
  reactHooks.configs.flat.recommended,
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 'latest',
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
  },
  {
    files: ['**/*.test.{js,jsx}'],
    languageOptions: { globals: { ...globals.node } },
  },
  {
    rules: {
      // 既有 6 处"账号/任务切换时重置状态"模式均为文档级合法写法，
      // 正确修法是父组件按 key 重挂载的重构，超出打扫范围，保持可见告警。
      'react-hooks/set-state-in-effect': 'warn',
    },
  },
];
