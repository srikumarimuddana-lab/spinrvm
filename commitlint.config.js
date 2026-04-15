// Commitlint configuration — enforces Conventional Commits format.
// https://www.conventionalcommits.org/en/v1.0.0/
//
// Allowed types match the project's git-workflow convention:
//   feat | fix | refactor | docs | test | chore | perf | ci
//
// Run manually:  echo "feat: add thing" | npx commitlint
// CI check:      configured via husky commit-msg hook
/** @type {import('@commitlint/types').UserConfig} */
module.exports = {
  extends: ['@commitlint/config-conventional'],
  rules: {
    // Restrict to the types used in this project (see rules/common/git-workflow.md)
    'type-enum': [
      2,
      'always',
      ['feat', 'fix', 'refactor', 'docs', 'test', 'chore', 'perf', 'ci'],
    ],
    // Scope is optional but must be lower-case when provided
    'scope-case': [2, 'always', 'lower-case'],
    // Subject must not end with a period
    'subject-full-stop': [2, 'never', '.'],
    // Header max length — GitHub truncates at ~72 chars in the UI
    'header-max-length': [2, 'always', 72],
    // Body lines should wrap at 100 chars for readability
    'body-max-line-length': [1, 'always', 100],
  },
};
