# Automatic documentation refresh

Automatic refresh omits a deployment when the complete committed change since the selected runtime's actual built-from revision consists only of regular Markdown in `docs/notes/` (including descendants), `docs/native-plan-macos.md`, `docs/native-plan-windows-linux.md`, or `docs/node-contract.md`.

This deliberately avoids deployment for coordination notes. It does not assert artifact equality, build-input equivalence, or verification of the new source revision. The selected runtime keeps its original prefix, full source revision, and built-from marker. If a project uses those notes as build inputs, changing them still does not request automatic deployment; an explicit runtime ensure builds the requested source normally.

Any accumulated code, contract, configuration, other document, or unknown-path change keeps the normal refresh path. Executable Markdown, symlinks, missing history, and unstable selection or committed source also keep that path. Existing authentication, health, rejection, epoch, and publication checks remain in force. Explicit ensure and force retain their exact behavior.

Uncommitted work does not alter this committed comparison. Omitting an automatic refresh preserves that work and does not build or deploy its contents.
