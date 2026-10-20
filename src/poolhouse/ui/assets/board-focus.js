/* Local display choices only. Authenticated board metadata supplies authors and scope. */
import { h } from './base.js';

const PREFIX = 'poolhouse-board-focus:';
export const NAV_MAX = 100;

export function restoreFocus(scope) {
  const initial = {hideAgents:true, pins:['#announcements']};
  try {
    const raw = localStorage.getItem(PREFIX + scope);
    if (!raw || raw.length > 16384) return initial;
    const value = JSON.parse(raw);
    if (!value || Object.keys(value).sort().join(',') !== 'hideAgents,pins' || typeof value.hideAgents !== 'boolean'
        || !Array.isArray(value.pins) || value.pins.length > NAV_MAX
        || value.pins.some(name => typeof name !== 'string' || !name.startsWith('#') || name.length > 64)
        || new Set(value.pins).size !== value.pins.length) return initial;
    return value;
  } catch { return initial; }
}

export function bindFocus(board, identity) {
  const scope = typeof identity === 'string' && identity && board.me ? JSON.stringify([board.base(), identity, board.me]) : '';
  if (scope !== board.focusScope) {
    board.focusScope = scope;
    board.focusView = scope ? restoreFocus(scope) : {hideAgents:false, pins:[]};
  }
}

export function pinnedChannels(board) {
  const allowed = new Set(board.boards.map(row => row.name));
  return (board.focusView?.pins || []).filter(name => allowed.has(name));
}

export function visibleMessages(board) {
  const all = board.items;
  if (!board.integrated || !board.focusScope || !board.focusView.hideAgents || board.view.kind !== 'board'
      || board.view.name === '#announcements' || pinnedChannels(board).includes(board.view.name)) return all;
  return all.filter(message => message.role !== 'agent' || message.from === board.me || message.personal_thread !== false
    || Array.isArray(message.mentions) && message.mentions.includes(board.me));
}

export function focusControls(board) {
  if (!board.integrated || !board.focusScope) return null;
  const scope = board.focusScope, me = board.me, view = board.view, revision = board.viewRevision, preference = board.focusView;
  let controls;
  const current = () => controls?.isConnected && !board.stopped && board.isConnected && scope === board.focusScope
    && me === board.me && view === board.view && revision === board.viewRevision && preference === board.focusView;
  const change = update => {
    if (!current()) return;
    update(board.focusView);
    try { localStorage.setItem(PREFIX + scope, JSON.stringify(board.focusView)); } catch {}
    board.update();
  };
  const hidden = board.items.length - visibleMessages(board).length;
  const toggle = h('input', {type:'checkbox', checked:board.focusView.hideAgents,
    onchange:event => change(value => { value.hideAgents = event.target.checked; })});
  const channel = board.view.kind === 'board' ? board.view.name : board.view.board;
  const pinned = pinnedChannels(board).includes(channel);
  controls = h('section', {class:'focus-controls', 'aria-label':'Message focus'},
    h('label', {}, toggle, ' Hide agent chatter'),
    h('span', {role:'status'}, `${hidden} agent ${hidden === 1 ? 'message' : 'messages'} hidden on this loaded page.`),
    board.focusView.hideAgents ? h('button', {type:'button', onclick:() => change(value => { value.hideAgents = false; })}, 'Show all messages') : null,
    channel && board.boards.some(row => row.name === channel) ? h('button', {type:'button', 'aria-pressed':String(pinned),
      onclick:() => change(value => {
        value.pins = pinned ? value.pins.filter(name => name !== channel) : [...value.pins, channel].slice(-NAV_MAX);
      })}, pinned ? 'Unpin channel' : 'Pin channel') : null,
    board.view.kind === 'thread' ? h('small', {}, 'All messages stay visible in an opened thread.') : null);
  return controls;
}
