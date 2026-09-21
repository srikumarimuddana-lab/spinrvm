module.exports = {
  __esModule: true,
  default: function PostHogMock() {
    this.identify = () => {};
    this.stopSessionRecording = () => {};
    this.startSessionRecording = () => {};
    this.shutdown = () => {};
  },
};
