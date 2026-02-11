import { Stack } from 'expo-router';
import { useEffect } from 'react';
import { useFonts } from 'expo-font';

export default function RootLayout() {
  // We can load fonts here similar to the Rider app
  
  return (
    <Stack screenOptions={{ headerShown: false }}>
      <Stack.Screen name="index" />
      <Stack.Screen name="login" />
      <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
    </Stack>
  );
}