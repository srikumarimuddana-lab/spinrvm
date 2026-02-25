module.exports = function (api) {
    api.cache(true);
    return {
        presets: ['babel-preset-expo'],
        plugins: [
            'react-native-reanimated/plugin',
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
        ],
    };
};
