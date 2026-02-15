declare module 'expo-linear-gradient' {
    import { ComponentType } from 'react';
    import { ViewProps } from 'react-native';

    interface LinearGradientProps extends ViewProps {
        colors: string[];
        start?: { x: number; y: number };
        end?: { x: number; y: number };
        locations?: number[];
    }

    export const LinearGradient: ComponentType<LinearGradientProps>;
}
