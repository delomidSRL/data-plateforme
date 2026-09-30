// Module 19 §1 — Monaco loaded and bundled locally, never from a CDN (on-premise deployment,
// no outbound call allowed). `@monaco-editor/react`'s default `loader` fetches its bundle from
// jsdelivr; `loader.config({ monaco })` below points it at the `monaco-editor` package Vite
// already bundles instead, and `MonacoEnvironment.getWorker` gives it locally-bundled workers
// (Vite's `?worker` import) rather than letting it reach for CDN worker URLs.
import * as monaco from "monaco-editor";
import { loader } from "@monaco-editor/react";
import EditorWorker from "monaco-editor/editor/editor.worker?worker";

self.MonacoEnvironment = {
  // Every language this module uses in étape 1 (sql, yaml, markdown, csv-as-plaintext) is a
  // pure Monarch grammar with no language-service worker of its own — the generic editor
  // worker covers all of them. json/css/html/ts workers are added here only if a later étape
  // actually needs one of those languages.
  getWorker() {
    return new EditorWorker();
  },
};

loader.config({ monaco });

export { monaco };
