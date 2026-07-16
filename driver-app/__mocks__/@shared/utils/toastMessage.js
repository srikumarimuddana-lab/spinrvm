// Passthrough to the real implementation: driver jest maps every '@shared/*'
// import into __mocks__, but the clamp is pure logic worth testing for real.
module.exports = require('../../../../shared/utils/toastMessage');
