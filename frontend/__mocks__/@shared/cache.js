module.exports = {
  appCache: {
    get: jest.fn(() => Promise.resolve(null)),
    set: jest.fn(() => Promise.resolve()),
    clearUserCache: jest.fn(() => Promise.resolve()),
  },
  CACHE_KEYS: {
    USER_PROFILE: 'user_profile',
    DRIVER_PROFILE: 'driver_profile',
  },
  CACHE_CONFIG: {
    USER_PROFILE_TTL: 300000,
  },
};
