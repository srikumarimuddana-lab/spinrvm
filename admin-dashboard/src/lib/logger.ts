import pino from 'pino';

const logger = pino({
  name: 'spinr-admin',
  level: process.env.LOG_LEVEL ?? (process.env.NODE_ENV === 'production' ? 'info' : 'debug'),
  base: {
    surface: 'admin',
    env: process.env.NODE_ENV ?? 'development',
  },
  formatters: {
    level(label: string) {
      return { level: label };
    },
  },
  timestamp: pino.stdTimeFunctions.isoTime,
});

export default logger;
