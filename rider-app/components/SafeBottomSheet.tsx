import React from 'react';
import { View, ScrollView, StyleSheet, type ViewStyle } from 'react-native';

interface BottomSheetProps {
  children: React.ReactNode;
  index?: number;
  snapPoints?: (string | number)[];
  enablePanDownToClose?: boolean;
  backgroundStyle?: ViewStyle;
  handleIndicatorStyle?: ViewStyle;
  onChange?: (index: number) => void;
}

const SafeBottomSheet = React.forwardRef<any, BottomSheetProps>(
  ({ children, snapPoints, index = 0, backgroundStyle, handleIndicatorStyle }, _ref) => {
    const height = snapPoints?.[index] ?? '50%';
    return (
      <View style={[styles.sheet, { height: height as any }, backgroundStyle]}>
        <View style={[styles.handle, handleIndicatorStyle]} />
        {children}
      </View>
    );
  },
);

function BottomSheetScrollViewComponent({
  children,
  contentContainerStyle,
  ...rest
}: any) {
  return (
    <ScrollView
      style={{ flex: 1 }}
      contentContainerStyle={contentContainerStyle}
      showsVerticalScrollIndicator={false}
      nestedScrollEnabled
      {...rest}
    >
      {children}
    </ScrollView>
  );
}

function BottomSheetViewComponent({ children, style, ...rest }: any) {
  return <View style={[{ flex: 1 }, style]} {...rest}>{children}</View>;
}

const styles = StyleSheet.create({
  sheet: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    backgroundColor: '#fff',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: -3 },
    shadowOpacity: 0.12,
    shadowRadius: 8,
    elevation: 12,
    overflow: 'hidden',
  },
  handle: {
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: '#E0E0E0',
    alignSelf: 'center',
    marginTop: 10,
    marginBottom: 6,
  },
});

export { BottomSheetScrollViewComponent as BottomSheetScrollView };
export { BottomSheetViewComponent as BottomSheetView };
export default SafeBottomSheet;
