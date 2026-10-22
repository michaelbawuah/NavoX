import { createBrowserVoxAudioCaptureFactory } from "./vox-audio-capture";
import type { VoxTransport } from "./vox-call";
import { createLiveKitVoxTransport } from "./vox-livekit";
import { createVoxMediaAdmission } from "./vox-media-admission";
import { VoxReservationClient, wrapVoxReservations } from "./vox-reservations";

/** New private lifecycle per provider; construction performs no network or media I/O. */
export function createDefaultVoxTransport(): VoxTransport {
  const reservations = new VoxReservationClient();
  return wrapVoxReservations(
    createLiveKitVoxTransport({
      admission: createVoxMediaAdmission({
        reservations,
        qualified:
          process.env.NEXT_PUBLIC_NAVOX_STUDENT_VOX_MEDIA_ENABLED === "true",
      }),
      capture: createBrowserVoxAudioCaptureFactory({
        qualified:
          process.env.NEXT_PUBLIC_NAVOX_STUDENT_VOX_CAPTURE_QUALIFIED ===
          "true",
      }),
    }),
    reservations,
  );
}
