import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

type EasConfig = {
  build?: Record<string, {
    distribution?: string;
    developmentClient?: boolean;
    android?: { buildType?: string };
  }>;
  submit?: Record<string, {
    android?: { track?: string; releaseStatus?: string };
  }>;
};

describe('Android Auto real-car distribution', () => {
  const easConfig = JSON.parse(
    readFileSync(resolve(__dirname, '..', 'eas.json'), 'utf8'),
  ) as EasConfig;

  it('ships the Android Auto test build through a completed Play internal release', () => {
    const build = easConfig.build?.['android-auto'];
    const submit = easConfig.submit?.['android-auto']?.android;

    expect(build?.distribution).toBe('store');
    expect(build?.developmentClient).not.toBe(true);
    expect(build?.android?.buildType).not.toBe('apk');
    expect(submit?.track).toBe('internal');
    expect(submit?.releaseStatus).toBe('completed');
  });
});
