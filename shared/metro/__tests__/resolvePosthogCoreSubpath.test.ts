import fs from 'fs';
import os from 'os';
import path from 'path';

const { resolvePosthogCoreSubpath } = require('../resolvePosthogCoreSubpath') as {
  resolvePosthogCoreSubpath: (
    projectRoot: string,
    moduleName: string,
  ) => { type: string; filePath: string } | null;
};

function writeFile(filePath: string, contents: string): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, contents);
}

describe('resolvePosthogCoreSubpath', () => {
  let tmp: string;

  beforeEach(() => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'posthog-core-metro-'));
  });

  afterEach(() => {
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('maps @posthog/core/surveys onto the hoisted CJS file', () => {
    const surveys = path.join(tmp, 'node_modules/@posthog/core/dist/surveys/index.js');
    writeFile(surveys, 'module.exports = {};');

    expect(resolvePosthogCoreSubpath(tmp, '@posthog/core/surveys')).toEqual({
      type: 'sourceFile',
      filePath: surveys,
    });
  });

  it('maps nested posthog-react-native/@posthog/core surveys', () => {
    const surveys = path.join(
      tmp,
      'node_modules/posthog-react-native/node_modules/@posthog/core/dist/surveys/index.js',
    );
    writeFile(surveys, 'module.exports = {};');

    expect(resolvePosthogCoreSubpath(tmp, '@posthog/core/surveys')).toEqual({
      type: 'sourceFile',
      filePath: surveys,
    });
  });

  it('maps @posthog/core/error-tracking and vendor files', () => {
    const errorTracking = path.join(
      tmp,
      'node_modules/@posthog/core/dist/error-tracking/index.js',
    );
    const vendor = path.join(tmp, 'node_modules/@posthog/core/dist/vendor/foo.js');
    writeFile(errorTracking, 'module.exports = {};');
    writeFile(vendor, 'module.exports = {};');

    expect(resolvePosthogCoreSubpath(tmp, '@posthog/core/error-tracking')?.filePath).toBe(
      errorTracking,
    );
    expect(resolvePosthogCoreSubpath(tmp, '@posthog/core/vendor/foo')?.filePath).toBe(vendor);
  });

  it('does not intercept the package root or unrelated modules', () => {
    expect(resolvePosthogCoreSubpath(tmp, '@posthog/core')).toBeNull();
    expect(resolvePosthogCoreSubpath(tmp, 'posthog-react-native')).toBeNull();
  });

  it('rejects path traversal', () => {
    expect(resolvePosthogCoreSubpath(tmp, '@posthog/core/../secret')).toBeNull();
  });

  it('returns null when the CJS file is missing', () => {
    expect(resolvePosthogCoreSubpath(tmp, '@posthog/core/surveys')).toBeNull();
  });
});
