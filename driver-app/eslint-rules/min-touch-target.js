/**
 * spinr/min-touch-target
 *
 * Design-audit follow-up to the SPACING/FONT token rule in eslint.config.js
 * (see its comment block for the "warn + pre-existing-violations" posture
 * this rule matches). That PR (#4951) deliberately left out the MIN_TOUCH
 * (44pt Apple HIG touch-target) half of the finding because a plain
 * `no-restricted-syntax` selector can't compute an *effective* touch-target
 * size — that depends on width/height combined with hitSlop, which a single
 * AST selector has no way to add together. This is a real custom rule
 * (`create(context)` with its own resolution logic) instead.
 *
 * MIN_TOUCH mirrors shared/utils/responsive.ts's `MIN_TOUCH = 44` constant.
 * It is duplicated here (not imported) because this file runs under plain
 * Node as part of ESLint's flat config, not through the app's TS/Metro
 * pipeline — same reason the SPACING/FONT rule above matches on property
 * *names*, not by importing the actual SPACING/FONT objects.
 *
 * Design goal: false negatives are fine, false positives are not. This
 * rule only flags an element when it can *fully* resolve both the width
 * and the height (via `style` — an inline object, a `StyleSheet.create`
 * reference, or an array of those two shapes — and `hitSlop`, if present)
 * down to literal numbers, and the resolved effective size is still under
 * 44pt on either axis. Anything it can't confidently resolve (dynamic
 * styles, spreads, conditional style arrays, non-literal hitSlop, or no
 * explicit width/height/minWidth/minHeight at all — i.e. a touchable that
 * sizes itself from content + padding) is silently skipped rather than
 * flagged. That intentionally misses real violations (e.g. the very common
 * "padding: 8, no explicit size" pattern) in exchange for not crying wolf
 * on cases it can't actually reason about.
 *
 * `Button` (react-native's built-in) is deliberately not covered — it's
 * platform-rendered and doesn't accept a `style` prop that changes its
 * touch-target size, so there's nothing for this rule to check.
 */

'use strict';

const MIN_TOUCH = 44; // shared/utils/responsive.ts MIN_TOUCH

const TARGET_COMPONENTS = new Set([
  'TouchableOpacity',
  'TouchableHighlight',
  'TouchableWithoutFeedback',
  'Pressable',
]);

const SIZE_KEYS = ['width', 'height', 'minWidth', 'minHeight'];

/** Manual full-tree walk (node.parent is already set by ESLint's own
 * traversal by the time Program:exit fires) — needed because a file's
 * `const styles = StyleSheet.create({...})` block conventionally sits
 * *below* the component that uses it, so a normal single-pass, in-order
 * listener would see the JSX before the style definitions exist. */
function walk(node, visit) {
  if (!node || typeof node.type !== 'string') return;
  visit(node);
  for (const key of Object.keys(node)) {
    if (key === 'parent') continue;
    const value = node[key];
    if (Array.isArray(value)) {
      for (const child of value) {
        if (child && typeof child.type === 'string') walk(child, visit);
      }
    } else if (value && typeof value.type === 'string') {
      walk(value, visit);
    }
  }
}

function numericLiteral(node) {
  if (!node) return undefined;
  if (node.type === 'Literal' && typeof node.value === 'number') return node.value;
  if (
    node.type === 'UnaryExpression' &&
    node.operator === '-' &&
    node.argument.type === 'Literal' &&
    typeof node.argument.value === 'number'
  ) {
    return -node.argument.value;
  }
  return undefined;
}

/** Extract {width, height, minWidth, minHeight} numeric literals from a
 * style ObjectExpression. Missing/non-literal keys are left undefined. */
function readSizeProps(objectExpression) {
  const sizes = {};
  for (const prop of objectExpression.properties) {
    if (prop.type !== 'Property' || prop.computed) continue;
    const keyName = prop.key.type === 'Identifier' ? prop.key.name : prop.key.value;
    if (!SIZE_KEYS.includes(keyName)) continue;
    const n = numericLiteral(prop.value);
    if (n !== undefined) sizes[keyName] = n;
  }
  return sizes;
}

/** Build a Map<styleVarName, Map<propName, sizes>> for every top-level
 * `const X = StyleSheet.create({...})` in the file. */
function collectStyleSheets(programNode) {
  const sheets = new Map();
  walk(programNode, (node) => {
    if (
      node.type !== 'CallExpression' ||
      node.callee.type !== 'MemberExpression' ||
      node.callee.object.type !== 'Identifier' ||
      node.callee.object.name !== 'StyleSheet' ||
      node.callee.property.type !== 'Identifier' ||
      node.callee.property.name !== 'create'
    ) {
      return;
    }
    const arg = node.arguments[0];
    if (!arg || arg.type !== 'ObjectExpression') return;
    const parent = node.parent;
    if (!parent || parent.type !== 'VariableDeclarator' || parent.id.type !== 'Identifier') return;

    const styleVarName = parent.id.name;
    const propMap = new Map();
    for (const prop of arg.properties) {
      if (prop.type !== 'Property' || prop.computed) continue;
      const propName = prop.key.type === 'Identifier' ? prop.key.name : prop.key.value;
      if (prop.value.type !== 'ObjectExpression') continue;
      propMap.set(propName, readSizeProps(prop.value));
    }
    sheets.set(styleVarName, propMap);
  });
  return sheets;
}

/** Resolve a single style array element (ObjectExpression or a
 * `styles.foo` MemberExpression) to a sizes object, or `null` if it's
 * anything this rule can't confidently reason about. */
function resolveStyleElement(node, styleSheets) {
  if (!node) return null;
  if (node.type === 'ObjectExpression') return readSizeProps(node);
  if (
    node.type === 'MemberExpression' &&
    !node.computed &&
    node.object.type === 'Identifier' &&
    node.property.type === 'Identifier'
  ) {
    const propMap = styleSheets.get(node.object.name);
    if (!propMap || !propMap.has(node.property.name)) return null;
    return propMap.get(node.property.name);
  }
  return null;
}

/** Resolve a `style={...}` JSX attribute value to a merged sizes object,
 * or `null` if any part of it is unresolvable (dynamic style, conditional
 * array entry, spread, etc.) — unresolvable means "skip this element". */
function resolveStyleAttr(expression, styleSheets) {
  if (!expression) return null;
  if (expression.type === 'ArrayExpression') {
    const merged = {};
    for (const el of expression.elements) {
      const sizes = resolveStyleElement(el, styleSheets);
      if (sizes === null) return null; // any unresolvable entry -> bail
      Object.assign(merged, sizes);
    }
    return merged;
  }
  return resolveStyleElement(expression, styleSheets);
}

/** Resolve a `hitSlop={...}` JSX attribute to {top,bottom,left,right}
 * (all defaulting to 0), or `null` if it's present but not a literal
 * number/object this rule can read — treated the same as "assume it
 * compensates" by the caller (i.e. skip rather than flag). */
function resolveHitSlop(expression) {
  const zero = { top: 0, bottom: 0, left: 0, right: 0 };
  if (!expression) return zero;
  const n = numericLiteral(expression);
  if (n !== undefined) return { top: n, bottom: n, left: n, right: n };
  if (expression.type === 'ObjectExpression') {
    const hs = { ...zero };
    let sawUnresolvable = false;
    for (const prop of expression.properties) {
      if (prop.type !== 'Property' || prop.computed) continue;
      const keyName = prop.key.type === 'Identifier' ? prop.key.name : prop.key.value;
      if (!(keyName in hs)) continue;
      const val = numericLiteral(prop.value);
      if (val === undefined) {
        sawUnresolvable = true;
      } else {
        hs[keyName] = val;
      }
    }
    return sawUnresolvable ? null : hs;
  }
  return null;
}

module.exports = {
  meta: {
    type: 'suggestion',
    docs: {
      description:
        'Flag TouchableOpacity/TouchableHighlight/TouchableWithoutFeedback/Pressable elements whose fully-resolved style + hitSlop size is under the 44pt Apple HIG touch-target minimum (MIN_TOUCH in shared/utils/responsive.ts).',
    },
    schema: [],
    messages: {
      tooSmall:
        'Effective touch target is {{width}}x{{height}}pt (style + hitSlop), below the {{min}}pt Apple HIG minimum (MIN_TOUCH in shared/utils/responsive.ts). Increase width/height/minWidth/minHeight or hitSlop so both axes reach {{min}}pt.',
    },
  },
  create(context) {
    let styleSheets = new Map();

    return {
      Program(node) {
        styleSheets = collectStyleSheets(node);
      },
      JSXOpeningElement(node) {
        if (node.name.type !== 'JSXIdentifier' || !TARGET_COMPONENTS.has(node.name.name)) return;

        let styleAttrValue = null;
        let hitSlopAttrValue = null;
        let hasStyleAttr = false;
        let hasHitSlopAttr = false;

        for (const attr of node.attributes) {
          if (attr.type !== 'JSXAttribute' || attr.name.type !== 'JSXIdentifier') continue;
          if (attr.name.name === 'style') {
            hasStyleAttr = true;
            styleAttrValue =
              attr.value && attr.value.type === 'JSXExpressionContainer' ? attr.value.expression : null;
          } else if (attr.name.name === 'hitSlop') {
            hasHitSlopAttr = true;
            hitSlopAttrValue =
              attr.value && attr.value.type === 'JSXExpressionContainer' ? attr.value.expression : null;
          }
        }

        if (!hasStyleAttr) return; // no size info at all -> unresolvable, skip (false-negative accepted)

        const sizes = resolveStyleAttr(styleAttrValue, styleSheets);
        if (!sizes) return; // dynamic/unresolvable style -> skip

        const baseWidth = sizes.width ?? sizes.minWidth;
        const baseHeight = sizes.height ?? sizes.minHeight;
        if (baseWidth === undefined || baseHeight === undefined) return; // no literal size on one axis -> skip

        const hitSlop = hasHitSlopAttr ? resolveHitSlop(hitSlopAttrValue) : { top: 0, bottom: 0, left: 0, right: 0 };
        if (hitSlop === null) return; // hitSlop present but not readable -> assume it compensates, skip

        const effectiveWidth = baseWidth + hitSlop.left + hitSlop.right;
        const effectiveHeight = baseHeight + hitSlop.top + hitSlop.bottom;

        if (effectiveWidth >= MIN_TOUCH && effectiveHeight >= MIN_TOUCH) return;

        context.report({
          node,
          messageId: 'tooSmall',
          data: {
            width: String(effectiveWidth),
            height: String(effectiveHeight),
            min: String(MIN_TOUCH),
          },
        });
      },
    };
  },
};
