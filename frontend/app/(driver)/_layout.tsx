import React from 'react';
import { Tabs } from 'expo-router';
import { Ionicons, MaterialCommunityIcons } from '@expo/vector-icons';
import { View, Platform } from 'react-native';

// Consistent theme with rider app (white and red)
const COLORS = {
  primary: '#FF3B30', // Vibrant Red
  background: '#FFFFFF',
  surface: '#FFFFFF',
  surfaceLight: '#F5F5F5',
  text: '#1A1A1A',
  textDim: '#666666',
  border: '#E5E7EB',
};

export default function DriverLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: {
          backgroundColor: COLORS.background,
          borderTopWidth: 1,
          borderTopColor: COLORS.border,
          height: Platform.OS === 'ios' ? 88 : 65,
          paddingBottom: Platform.OS === 'ios' ? 28 : 10,
          paddingTop: 8,
          elevation: 0,
        },
        tabBarActiveTintColor: COLORS.primary,
        tabBarInactiveTintColor: '#999',
        tabBarLabelStyle: {
          fontSize: 11,
          fontWeight: '600',
          letterSpacing: 0.2,
        },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: 'Drive',
          tabBarIcon: ({ color, size }) => (
            <View style={{ alignItems: 'center', justifyContent: 'center' }}>
              <MaterialCommunityIcons name="steering" size={size} color={color} />
            </View>
          ),
        }}
      />
      <Tabs.Screen
        name="rides"
        options={{
          title: 'Rides',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="car-sport" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="earnings"
        options={{
          title: 'Earnings',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="wallet" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: 'Profile',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="person-circle" size={size} color={color} />
          ),
        }}
      />
      {/* Hidden screens — accessible via navigation but not shown in tab bar */}
      <Tabs.Screen name="ride-detail" options={{ href: null, headerShown: false }} />
      <Tabs.Screen name="settings" options={{ href: null, headerShown: false }} />
      <Tabs.Screen name="notifications" options={{ href: null, headerShown: false }} />
      <Tabs.Screen name="chat" options={{ href: null, headerShown: false }} />
    </Tabs>
  );
}
