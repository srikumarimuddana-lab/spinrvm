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
                        '@': './',
                        '@components': './components',
                        '@hooks': './hooks',
                        '@styles': './styles',
                        '@types': './types'
                    },
                    extensions: ['.ios.js', '.android.js', '.js', '.ts', '.tsx', '.json'],
                },
            ],
            // react-native-reanimated/plugin MUST be last (Babel processes in reverse order)
            'react-native-reanimated/plugin',
        ],
    };
};
