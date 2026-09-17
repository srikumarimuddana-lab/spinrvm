const { withAppBuildGradle, withXcodeProject } = require('@expo/config-plugins');
const {
  stripPostHogGradleApply,
  rewritePostHogBundlePhases,
} = require('../../shared/expo/stripPostHogCliUpload');

// Must be listed immediately AFTER 'posthog-react-native/expo' in app.config.ts.
// That plugin always wires JS sourcemap upload via posthog-cli (Gradle +
// posthog-xcode.sh). We do not ship @posthog/cli on EAS, so those hooks fail
// the production Android bundle. Native session-replay wiring is left intact.
module.exports = function withSkipPostHogCliUpload(config) {
  config = withAppBuildGradle(config, (cfg) => {
    if (cfg.modResults.language === 'groovy') {
      cfg.modResults.contents = stripPostHogGradleApply(cfg.modResults.contents);
    }
    return cfg;
  });
  config = withXcodeProject(config, (cfg) => {
    rewritePostHogBundlePhases(cfg.modResults);
    return cfg;
  });
  return config;
};
