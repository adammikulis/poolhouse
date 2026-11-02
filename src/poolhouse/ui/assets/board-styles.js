export const INTEGRATED_STYLES = `
:host([integrated]) { min-height:0; font-family:var(--poolhouse-ui-font); font-size:1rem; }
:host([integrated]) .shell { display:block; border:0; border-radius:0; height:100%; min-height:0; }
:host([integrated]) nav { display:none; }
:host([integrated]) main { display:flex; flex-direction:column; height:100%; min-height:0; padding:0; overflow:hidden; }
:host([integrated]) main > div:last-child { flex:none; min-height:0; }
:host([integrated]) main > div:first-child { flex:1; min-height:0; overflow-y:auto; padding:0 calc(28px * var(--ui-density,1)) calc(24px * var(--ui-density,1)); }
:host([integrated]) main > div:last-child { flex:none; }
:host([integrated]) main header { min-height:77px; margin:0 calc(-28px * var(--ui-density,1)) calc(20px * var(--ui-density,1)); padding:calc(16px * var(--ui-density,1)) calc(28px * var(--ui-density,1));
  border-bottom:1px solid var(--poolhouse-ui-line); background:var(--poolhouse-ui-surface); position:sticky; top:0; z-index:1; align-items:center; }
:host([integrated]) main header h2 { font-size:1.214rem; }
:host([integrated]) .badge { background:color-mix(in srgb,var(--poolhouse-cyan,var(--poolhouse-ui-accent)) 20%,var(--poolhouse-ui-surface)); color:var(--poolhouse-ui-text); border:0; }
:host([integrated]) .msg { position:relative; border:0; padding:calc(14px * var(--ui-density,1)) calc(10px * var(--ui-density,1)) calc(14px * var(--ui-density,1)) calc(48px * var(--ui-density,1)); margin:calc(4px * var(--ui-density,1)) 0; border-radius:10px; }
:host([integrated]) .msg:hover { background:var(--poolhouse-ui-bg); }
:host([integrated]) .msg::before { content:"●"; display:grid; place-items:center; width:30px; height:30px;
  position:absolute; left:4px; top:12px; border-radius:10px; background:color-mix(in srgb,var(--poolhouse-yellow) 33%,var(--poolhouse-ui-surface)); color:var(--poolhouse-ui-text); }
:host([integrated]) .msg pre { line-height:1.7; }
:host([integrated]) .composer { margin:0 calc(28px * var(--ui-density,1)) calc(18px * var(--ui-density,1)); padding:calc(12px * var(--ui-density,1)); border:1px solid var(--poolhouse-ui-line-strong); border-radius:12px; background:var(--poolhouse-ui-surface); }
:host([integrated]) .composer:focus-within { border-color:var(--poolhouse-pink); box-shadow:0 0 0 3px color-mix(in srgb,var(--poolhouse-pink) 10%,transparent); }
:host([integrated]) .composer textarea { background:var(--poolhouse-ui-surface); border:0; min-height:48px; padding:calc(6px * var(--ui-density,1)); }
:host([integrated]) .composer input { background:var(--poolhouse-ui-bg); border:0; font-size:0.7857rem; }
:host([integrated]) .composer button { justify-self:end; padding:calc(8px * var(--ui-density,1)) calc(18px * var(--ui-density,1)); }
:host([integrated]) .channel-tabs button[aria-pressed=true] { background:color-mix(in srgb,var(--poolhouse-pink) 15%,var(--poolhouse-ui-surface)); }
:host([integrated]) .who { font-size:.9286rem; }
:host([integrated]) .meta { font-size:.8571rem; }
:host([integrated]) .badge { font-size:.7857rem; }
:host([integrated]) .history-controls { display:flex; gap:8px; padding:8px calc(28px * var(--ui-density,1)); flex-wrap:wrap; }
:host([integrated]) .history-controls button { font:inherit; color:var(--poolhouse-ui-text); background:var(--poolhouse-ui-surface); border:1px solid var(--poolhouse-ui-line); border-radius:6px; padding:5px 10px; }
:host([integrated]) .history-controls button:disabled { opacity:.5; }
:host([integrated]) .state { padding:calc(30px * var(--ui-density,1)) calc(10px * var(--ui-density,1)); }
@media (max-height:600px) {
  :host([integrated]) main header { position:static; min-height:48px; padding-top:8px; padding-bottom:8px; margin-bottom:8px; }
  :host([integrated]) main > div:first-child { padding-bottom:8px; }
  :host([integrated]) .composer { grid-template-columns:minmax(0,1fr) auto; gap:4px; padding:8px; margin-bottom:8px; }
  :host([integrated]) .composer .composer-recipient, :host([integrated]) .composer input,
  :host([integrated]) .composer .composer-hint, :host([integrated]) .composer .err { grid-column:1 / -1; }
  :host([integrated]) .composer textarea { min-height:48px; height:48px; }
  :host([integrated]) .composer button { align-self:end; }
}
`;
