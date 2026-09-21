// Passthrough to the real implementation: driver jest maps every '@shared/*'
// import into __mocks__, but this table is pure data + a pure lookup, and
// stubbing it would mean no driver-app test ever exercises the real mapping.
// Same reasoning as the toastMessage mock next door.
module.exports = require('../../../../shared/errors/sentinelMessages');
