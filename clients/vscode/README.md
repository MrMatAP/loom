# Loom Catalog — VS Code extension (contributor notes)

A thin client for the Loom Catalog API: browse, create, and edit
`Capability`, `Agent`, and `ModelEndpoint` entities from the editor
sidebar. See `/CLAUDE.md` at the repo root for the entity model it mirrors.

**User documentation lives in the plugin, not here.** Run **Loom: Open
Manual** from the command palette (or the Catalog view's `...` menu). Its
source is [`media/manual.md`](media/manual.md), bundled into the `.vsix` —
edit that file when a user-facing flow changes.

## Build

```
npm install
npm run compile      # bundle src/ -> out/extension.js
npm run watch        # same, rebuilding on change
```

## Test

There is no unit-test suite yet. The checks are:

```
npm run typecheck    # strict tsc, no emit
npm run compile      # must bundle clean
```

Then press **F5** with this folder open to launch an Extension Development
Host with the extension loaded, and exercise the flows from the manual
against a running Catalog API.
