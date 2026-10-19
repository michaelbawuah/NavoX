import { createBrowserVoxAudioCaptureFactory } from "./vox-audio-capture";
import type { VoxTransport } from "./vox-call";
import {
  createLiveKitVoxTransport,
  type VoxMediaAdmission,
} from "./vox-livekit";
import { VoxReservationClient, wrapVoxReservations } from "./vox-reservations";

/** A reservation is never a media grant. No public setting may manufacture one. */
const unavailableAdmission: VoxMediaAdmission = {
  available: false,
  async join() {
    throw new Error("Vox calls are not available on this deployment.");
  },
};

/** New private lifecycle per provider; construction performs no network or media I/O. */
export function createDefaultVoxTransport(): VoxTransport {
  return wrapVoxReservations(
    createLiveKitVoxTransport({
      admission: unavailableAdmission,
      capture: createBrowserVoxAudioCaptureFactory({ qualified: false }),
    }),
    new VoxReservationClient(),
  );
}
