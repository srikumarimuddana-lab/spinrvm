import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

const AppMap = React.forwardRef((props: any, ref: any) => {
    return (
        <View style={[props.style, styles.container]}>
            <Text style={styles.text}>Map View is not supported on Web in this demo.</Text>
            <Text style={styles.subtext}>Please use the mobile app (Android/iOS) for full map experience.</Text>
        </View>
    );
});

const styles = StyleSheet.create({
    container: {
        justifyContent: 'center',
        alignItems: 'center',
        backgroundColor: '#F0F0F0',
    },
    text: {
        color: '#666',
        fontSize: 16,
        fontWeight: '600',
        textAlign: 'center',
    },
    subtext: {
        color: '#999',
        fontSize: 14,
        marginTop: 8,
        textAlign: 'center',
    }
});

export default AppMap;
