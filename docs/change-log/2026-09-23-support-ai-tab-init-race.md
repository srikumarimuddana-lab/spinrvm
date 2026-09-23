# Change impact note — Support AI tab initialization

- **Issue:** Rider CI exposed two AI-chat contact-copy tests that could not find the chat input; the no-email Contact assertion also counted the always-present Contact tab icon.
- **Root cause:** `aiMode` initially represented both “configuration request pending” and “assistant hidden.” The fallback effect therefore replaced `initialTab="chat"` with FAQ before the enabled response arrived. The icon count included the tab navigation itself.
- **Fix:** Represent pending AI configuration separately, and only redirect from chat after the server confirms the assistant is hidden. The contact test now expects only the navigation icon when no email is configured.
- **Impact and blast radius:** Shared SupportScreen used by rider and driver Help surfaces. Existing chat initialization works when enabled; hidden mode still redirects to FAQ. No backend, data, payment, ride, or safety behavior changes. Low, isolated UI state risk.
- **User experience:** A rider or driver entering Help with the chat tab selected will remain there while configuration loads if AI chat is enabled. No visible change otherwise.
- **Verification:** Rider Jest shared contact suite (12/12 passed); driver wrapper test (1/1 passed). Driver Jest does not collect the shared suite. No production build or visual regression run.
- **Rollback:** Revert this commit; no data or configuration changes.
