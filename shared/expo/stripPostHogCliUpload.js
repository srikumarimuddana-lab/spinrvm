'use strict';

/**
 * posthog-react-native/expo always applies tooling/posthog.gradle and wraps
 * the iOS bundle phase with posthog-xcode.sh. Those hooks exec `posthog-cli`
 * to upload JS sourcemaps (error tracking). We use PostHog for session replay
 * only, do not install @posthog/cli, and EAS has no global CLI — Gradle then
 * fails with: A problem occurred starting process 'command 'posthog-cli''.
 *
 * These helpers undo just those upload hooks. Native replay + onNewIntent stay.
 */

const POSTHOG_GRADLE_APPLY =
  /(?:^[ \t]*project\.ext\.posthogReactNativeSkipOnConflict\s*=\s*(?:true|false)[ \t]*\r?\n)?^[ \t]*apply from: new File\(\["node", "--print", "require\('path'\)\.join\(require\('path'\)\.dirname\(require\.resolve\('posthog-react-native'\)\), '\.\.', 'tooling', 'posthog\.gradle'\)"\]\.execute\(\)\.text\.trim\(\)\)[ \t]*\r?\n(?:\r?\n)?/gm;

const POSTHOG_XCODE_WRAPPER =
  /(?:\/bin\/sh\s+)?`"\$NODE_BINARY" --print "require\('path'\)\.join\(require\('path'\)\.dirname\(require\.resolve\('posthog-react-native'\)\), '\.\.', 'tooling', 'posthog-xcode\.sh'\)"`\s*/g;

function stripPostHogGradleApply(contents) {
  if (typeof contents !== 'string') return contents;
  return contents.replace(POSTHOG_GRADLE_APPLY, '');
}

function stripPostHogXcodeWrapper(script) {
  if (typeof script !== 'string') return script;
  let next = script.replace(/^export POSTHOG_[A-Z0-9_]+=.*(?:\r?\n|$)/gm, '');
  next = next.replace(POSTHOG_XCODE_WRAPPER, '');
  next = next.replace(/[ \t]*--posthog-skip-on-conflict(?:\s+--)?[ \t]*/g, ' ');
  return next;
}

function decodePbxShellScript(shellScript) {
  if (typeof shellScript !== 'string') return null;
  try {
    return JSON.parse(shellScript);
  } catch {
    return null;
  }
}

function rewritePostHogBundlePhases(xcodeProject) {
  const section =
    xcodeProject &&
    xcodeProject.hash &&
    xcodeProject.hash.project &&
    xcodeProject.hash.project.objects &&
    xcodeProject.hash.project.objects.PBXShellScriptBuildPhase;
  if (!section) return 0;

  let rewritten = 0;
  for (const key of Object.keys(section)) {
    if (key.endsWith('_comment')) continue;
    const phase = section[key];
    const raw = decodePbxShellScript(phase.shellScript);
    if (!raw || !raw.includes('posthog-xcode.sh')) continue;
    phase.shellScript = JSON.stringify(stripPostHogXcodeWrapper(raw));
    rewritten += 1;
  }
  return rewritten;
}

module.exports = {
  stripPostHogGradleApply,
  stripPostHogXcodeWrapper,
  rewritePostHogBundlePhases,
};
