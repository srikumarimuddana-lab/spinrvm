/**
 * AI chat stale-card gating (ACTION_ITEMS.md AI8). Pins the guardrail: a
 * quote/suggestion/map-pin card must be treated as stale the moment the
 * rider sends a newer message, not just when a newer assistant reply lands.
 */
import { lastUserMessageIndex } from '../staleAiCard';

describe('lastUserMessageIndex', () => {
  it('returns -1 for an empty conversation', () => {
    expect(lastUserMessageIndex([])).toBe(-1);
  });

  it('returns -1 when no user message exists yet', () => {
    expect(lastUserMessageIndex([{ role: 'assistant' }, { role: 'assistant' }])).toBe(-1);
  });

  it('returns the index of the only user message', () => {
    expect(lastUserMessageIndex([{ role: 'user' }, { role: 'assistant' }, { role: 'assistant' }])).toBe(0);
  });

  it('returns the LAST user message index when several exist', () => {
    const messages = [
      { role: 'user' }, // 0 — first turn
      { role: 'assistant' }, // 1
      { role: 'assistant' }, // 2 — e.g. a fare_quote card
      { role: 'user' }, // 3 — second turn starts; card at index 2 is now stale
      { role: 'assistant' }, // 4
    ];
    expect(lastUserMessageIndex(messages)).toBe(3);
    // Everything before index 3 belongs to a turn the conversation moved past.
    expect(2 < lastUserMessageIndex(messages)).toBe(true);
    // The most recent assistant reply is still part of the live turn.
    expect(4 < lastUserMessageIndex(messages)).toBe(false);
  });
});
