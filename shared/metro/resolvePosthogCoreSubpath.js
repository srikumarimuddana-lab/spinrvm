'use strict';

const fs = require('fs');
const path = require('path');

const PREFIX = '@posthog/core/';

/**
 * Metro has `unstable_enablePackageExports = false` so Sentry loads CJS
 * (Hermes release crash if ESM namespaces are frozen — expo/expo#36589).
 * `@posthog/core` only exposes subpaths via package.json `exports`
 * (`./surveys` → `dist/surveys/index.js`), so Metro's legacy resolver
 * cannot find `@posthog/core/surveys`. Map those subpaths onto the CJS
 * files under `dist/` without turning package-exports back on.
 */
function resolvePosthogCoreSubpath(projectRoot, moduleName) {
  if (typeof moduleName !== 'string' || !moduleName.startsWith(PREFIX)) {
    return null;
  }
  const sub = moduleName.slice(PREFIX.length);
  if (!sub || sub === '.' || sub.includes('..') || path.isAbsolute(sub)) {
    return null;
  }

  const rels = [];
  if (sub.endsWith('.js') || sub.endsWith('.mjs')) {
    rels.push(sub.replace(/\.mjs$/, '.js'));
  } else {
    rels.push(path.join(sub, 'index.js'));
    rels.push(`${sub}.js`);
  }

  const nodeModuleRoots = [
    path.join(projectRoot, 'node_modules'),
    path.join(projectRoot, 'node_modules', 'posthog-react-native', 'node_modules'),
  ];

  for (const nm of nodeModuleRoots) {
    for (const rel of rels) {
      const filePath = path.join(nm, '@posthog', 'core', 'dist', rel);
      if (fs.existsSync(filePath)) {
        return { type: 'sourceFile', filePath };
      }
    }
  }
  return null;
}

module.exports = { resolvePosthogCoreSubpath };
