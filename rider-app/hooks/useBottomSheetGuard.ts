import { useCallback, useRef, type RefObject } from 'react';
import { useFocusEffect } from '@react-navigation/native';
import type BottomSheet from '@gorhom/bottom-sheet';

type LayoutEvent = { nativeEvent: { layout: { height: number } } };

/**
 * Keeps a permanently-visible bottom sheet (enablePanDownToClose={false})
 * from getting stuck off-screen.
 *
 * @gorhom/bottom-sheet resolves percentage snap points against its host
 * container's measured height. Two layout races can hand it a 0-height
 * container:
 *
 *  1. Cold start / fresh install — the screen mounts before the container
 *     reports a real height, so the mount animation targets garbage detents
 *     and the sheet never appears.
 *  2. Tab detach/re-attach — with react-native-screens, leaving the tab (or
 *     backgrounding the app) can fire a transient 0-height layout pass on
 *     the hidden scene. All detents collapse, the sheet's current index
 *     resolves to -1 (closed), and on the next container resize the library
 *     re-evaluates from that index and pins the sheet to its *closed*
 *     position — permanently, with no gesture to bring it back.
 *
 * Recovery layers (each snaps back to the last user-chosen index):
 *  - first real-height layout → initial snap (fixes race 1)
 *  - onChange(-1) → immediate snap; with pan-down-to-close disabled the
 *    sheet can never legitimately close, so -1 is always the layout race
 *  - real-height layout while the sheet reports closed → snap (a -1 that
 *    happened while the scene was hidden)
 *  - tab focus while the sheet reports closed → snap (re-attach passes that
 *    fire no layout event at all)
 */
export function useBottomSheetGuard(
  sheetRef: RefObject<BottomSheet | null>,
  initialIndex = 0,
) {
  const lastGoodIndex = useRef(initialIndex);
  const isClosed = useRef(false);
  const didInit = useRef(false);

  const snapBack = useCallback(() => {
    // Defer one frame so the sheet's own internal measurement has settled.
    requestAnimationFrame(() => sheetRef.current?.snapToIndex(lastGoodIndex.current));
  }, [sheetRef]);

  const handleSheetChange = useCallback((index: number) => {
    if (index === -1) {
      isClosed.current = true;
      snapBack();
      return;
    }
    isClosed.current = false;
    lastGoodIndex.current = index;
  }, [snapBack]);

  const handleContainerLayout = useCallback((e: LayoutEvent) => {
    if (e.nativeEvent.layout.height <= 0) return;
    if (!didInit.current || isClosed.current) {
      didInit.current = true;
      snapBack();
    }
  }, [snapBack]);

  useFocusEffect(
    useCallback(() => {
      if (didInit.current && isClosed.current) snapBack();
    }, [snapBack]),
  );

  return { handleSheetChange, handleContainerLayout };
}
