// These pinned packages publish browser ESM without a Node main/exports entry.
// Explicit ESM imports use the same public declarations as each package root.
declare module "@picovoice/porcupine-web/dist/esm/index.js" {
  export { PorcupineWorker } from "@picovoice/porcupine-web";
}

declare module "@picovoice/web-voice-processor/dist/esm/index.js" {
  export { WebVoiceProcessor } from "@picovoice/web-voice-processor";
}
