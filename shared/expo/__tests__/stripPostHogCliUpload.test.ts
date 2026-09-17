const {
  stripPostHogGradleApply,
  stripPostHogXcodeWrapper,
  rewritePostHogBundlePhases,
} = require('../stripPostHogCliUpload') as {
  stripPostHogGradleApply: (contents: string) => string;
  stripPostHogXcodeWrapper: (script: string) => string;
  rewritePostHogBundlePhases: (xcodeProject: {
    hash: { project: { objects: { PBXShellScriptBuildPhase: Record<string, { shellScript?: string }> } } };
  }) => number;
};

const GRADLE_APPLY = `apply from: new File(["node", "--print", "require('path').join(require('path').dirname(require.resolve('posthog-react-native')), '..', 'tooling', 'posthog.gradle')"].execute().text.trim())

android {`;

const XCODE_WRAPPED =
  'export POSTHOG_SKIP_ON_CONFLICT=1\n' +
  '`"$NODE_BINARY" --print "require(\'path\').join(require(\'path\').dirname(require.resolve(\'posthog-react-native\')), \'..\', \'tooling\', \'posthog-xcode.sh\')"` ' +
  '`"$NODE_BINARY" --print "require(\'path\').dirname(require.resolve(\'react-native/package.json\')) + \'/scripts/react-native-xcode.sh\'"`';

describe('stripPostHogCliUpload', () => {
  it('removes the posthog.gradle apply so release bundling does not exec posthog-cli', () => {
    const stripped = stripPostHogGradleApply(GRADLE_APPLY);
    expect(stripped).toBe('android {');
    expect(stripped).not.toContain('posthog.gradle');
  });

  it('also drops the skip-on-conflict gradle ext line', () => {
    const input =
      'project.ext.posthogReactNativeSkipOnConflict = true\n' + GRADLE_APPLY;
    expect(stripPostHogGradleApply(input)).toBe('android {');
  });

  it('leaves unrelated gradle alone', () => {
    const input = 'apply from: "sentry.gradle"\nandroid {}\n';
    expect(stripPostHogGradleApply(input)).toBe(input);
  });

  it('unwraps posthog-xcode.sh and keeps the React Native bundle script', () => {
    const stripped = stripPostHogXcodeWrapper(XCODE_WRAPPED);
    expect(stripped).not.toContain('posthog-xcode.sh');
    expect(stripped).toContain('react-native-xcode.sh');
    expect(stripped).not.toContain('POSTHOG_SKIP_ON_CONFLICT');
  });

  it('rewrites PBX shell-script phases that wrap posthog-xcode.sh', () => {
    const project = {
      hash: {
        project: {
          objects: {
            PBXShellScriptBuildPhase: {
              '1': { shellScript: JSON.stringify(XCODE_WRAPPED) },
              '1_comment': 'Bundle React Native code and images',
            },
          },
        },
      },
    };
    expect(rewritePostHogBundlePhases(project)).toBe(1);
    const next = JSON.parse(
      project.hash.project.objects.PBXShellScriptBuildPhase['1'].shellScript,
    );
    expect(next).not.toContain('posthog-xcode.sh');
    expect(next).toContain('react-native-xcode.sh');
  });
});
