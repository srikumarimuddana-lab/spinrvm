module.exports = function (api) {
    api.cache(true);
    return {
        presets: ['babel-preset-expo'],
        plugins: [
            [
                'module-resolver',
                {
                    root: ['./'],
                    alias: {
                        '@shared': '../shared',
                        '@': './'
                    },
                    extensions: ['.ios.js', '.android.js', '.js', '.ts', '.tsx', '.json'],
                },
            ],
            // react-native-worklets/plugin MUST be last (Babel processes in reverse
            // order). Reanimated 4 moved the worklets transform into this package;
            // the old 'react-native-reanimated/plugin' path is deprecated in v4.
            'react-native-worklets/plugin',
        ],
    };
};
