// Shared geo defaults used when GPS is unavailable (emulator, denied
// permission, first-launch cold cache). Spinr is a Saskatchewan-first
// service, so the fallback is Saskatoon — never Toronto.
// Lat/Lng match the admin-dashboard DEFAULT_CENTER constant.
export const DEFAULT_LATITUDE = 52.13;
export const DEFAULT_LONGITUDE = -106.67;

export const DEFAULT_COORDS = {
    latitude: DEFAULT_LATITUDE,
    longitude: DEFAULT_LONGITUDE,
};
