/**
 * Unit tests for the CarPlay / Android Auto smoke-test bootstrap (car/carSpike.ts).
 * The native module is fully mocked, so these run without a head unit and assert
 * the JS wiring: template shape + connect/disconnect registration + root set.
 */

jest.mock('@g4rb4g3/react-native-carplay', () => {
  class ListTemplate {
    config: unknown;
    constructor(config: unknown) {
      this.config = config;
    }
  }
  return {
    CarPlay: {
      connected: false,
      registerOnConnect: jest.fn(),
      registerOnDisconnect: jest.fn(),
      unregisterOnConnect: jest.fn(),
      unregisterOnDisconnect: jest.fn(),
      setRootTemplate: jest.fn(),
    },
    ListTemplate,
  };
});

import { CarPlay, ListTemplate } from '@g4rb4g3/react-native-carplay';
import { buildSpikeTemplate, initCarSpike, CAR_SPIKE_ENABLED } from '../car/carSpike';

type AnyConfig = {
  id: string;
  title: string;
  sections: { header?: string; items: { text: string }[] }[];
  items: { text: string }[];
  onItemSelect: (arg: { index: number }) => Promise<void>;
};

const configOf = (t: unknown) => (t as { config: AnyConfig }).config;

beforeEach(() => {
  jest.clearAllMocks();
  (CarPlay as unknown as { connected: boolean }).connected = false;
});

describe('buildSpikeTemplate', () => {
  it('builds a list template with id, title, and a single cross-platform item', () => {
    const t = buildSpikeTemplate(ListTemplate as never);
    const cfg = configOf(t);

    expect(t).toBeInstanceOf(ListTemplate);
    expect(cfg.id).toBe('spinr-car-spike');
    expect(cfg.title).toBe('Spinr (spike)');
    // iOS path
    expect(cfg.sections[0].items[0].text).toContain('Spinr');
    // Android path
    expect(cfg.items).toHaveLength(1);
    expect(cfg.items[0].text).toContain('Spinr');
  });

  it('wires an item-select callback that resolves without throwing', async () => {
    const cfg = configOf(buildSpikeTemplate(ListTemplate as never));
    expect(typeof cfg.onItemSelect).toBe('function');
    await expect(cfg.onItemSelect({ index: 0 })).resolves.toBeUndefined();
  });
});

describe('initCarSpike', () => {
  it('registers connect + disconnect handlers and sets the root template on connect', () => {
    const cleanup = initCarSpike();

    // Not connected at init → template is NOT set until the connect event fires.
    expect(CarPlay.setRootTemplate).not.toHaveBeenCalled();
    expect(CarPlay.registerOnConnect).toHaveBeenCalledTimes(1);
    expect(CarPlay.registerOnDisconnect).toHaveBeenCalledTimes(1);

    // Simulate the head unit connecting by invoking the registered callback.
    const onConnect = (CarPlay.registerOnConnect as jest.Mock).mock.calls[0][0];
    onConnect();

    expect(CarPlay.setRootTemplate).toHaveBeenCalledTimes(1);
    const rootArg = (CarPlay.setRootTemplate as jest.Mock).mock.calls[0][0];
    expect(rootArg).toBeInstanceOf(ListTemplate);

    // Cleanup unregisters both handlers.
    cleanup();
    expect(CarPlay.unregisterOnConnect).toHaveBeenCalledTimes(1);
    expect(CarPlay.unregisterOnDisconnect).toHaveBeenCalledTimes(1);
  });

  it('sets the root template immediately when a head unit is already connected at init', () => {
    // App cold-started while already connected: registerOnConnect won't replay the
    // past connect event, so initCarSpike must set the template right away.
    (CarPlay as unknown as { connected: boolean }).connected = true;

    initCarSpike();

    expect(CarPlay.setRootTemplate).toHaveBeenCalledTimes(1);
    const rootArg = (CarPlay.setRootTemplate as jest.Mock).mock.calls[0][0];
    expect(rootArg).toBeInstanceOf(ListTemplate);
  });

  it('is enabled for the spike (guard flag is on)', () => {
    expect(CAR_SPIKE_ENABLED).toBe(true);
  });
});
