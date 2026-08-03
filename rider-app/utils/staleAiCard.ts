/**
 * ACTION_ITEMS.md AI8: a quote/suggestion/map-pin card in the AI chat is
 * only "live" while it's still part of the most recent conversation turn.
 * The moment the rider sends a new message, every card before it belongs to
 * a turn the conversation has moved past and must not stay tappable — a
 * stale-but-still-consistent card could re-book or re-resolve at a possibly
 * different price or location than what the rider currently sees discussed.
 */
export function lastUserMessageIndex(messages: { role: string }[]): number {
  let idx = -1;
  for (let i = 0; i < messages.length; i++) {
    if (messages[i].role === 'user') idx = i;
  }
  return idx;
}
