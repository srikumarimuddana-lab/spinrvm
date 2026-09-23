# Keep profile-setup auto-select test aligned with required eligibility dates

Updated the profile-setup service-area auto-selection test to enter the newly required DOB and licence issue date before asserting the Create Profile action is enabled. No runtime code changes. Verification: driver-app Jest attempted using the available sibling dependency tree, but the suite fails during Jest Expo setup because that tree lacks `@react-native-community/datetimepicker` and has incompatible Expo module/type packages. Rollback by reverting the test-only field inputs; no DB/flag effect.
