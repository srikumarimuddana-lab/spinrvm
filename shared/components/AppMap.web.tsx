import React, { useMemo } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import type { ThemeColors } from '../theme/index';

const AppMap = React.forwardRef((props: any, ref: any) => {
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);

    return (
        <View style={[props.style, styles.container]}>
            <Text style={styles.text}>Map View is not supported on Web in this demo.</Text>
            <Text style={styles.subtext}>Please use the mobile app (Android/iOS) for full map experience.</Text>
        </View>
    );
});

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        container: {
            justifyContent: 'center',
            alignItems: 'center',
            backgroundColor: colors.surfaceLight,
        },
        text: {
            color: colors.textDim,
            fontSize: 16,
            fontWeight: '600',
            textAlign: 'center',
        },
        subtext: {
            color: colors.textDim,
            fontSize: 14,
            marginTop: 8,
            textAlign: 'center',
        }
    });
}

export default AppMap;
