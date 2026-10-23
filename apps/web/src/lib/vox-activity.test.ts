import { describe, expect, it, vi } from "vitest";
import { VoxActivityTracker } from "./vox-activity";

function rig() {
  const authority = { transmit: true, play: true };
  const onChange = vi.fn();
  const activity = new VoxActivityTracker({
    agentIdentity: "admitted-agent",
    mayTransmitAudio: () => authority.transmit,
    mayPlayAudio: () => authority.play,
    onChange,
  });
  return { activity, authority, onChange };
}

describe("Vox owned call activity", () => {
  it("keeps the agent speaking after local speech ends and prioritizes the user", () => {
    const { activity, onChange } = rig();
    activity.remote("admitted-agent", true);
    expect(activity.current()).toBe("quiet");
    activity.playback(true);
    activity.local(true);
    expect(activity.current()).toBe("listening");
    activity.local(false);
    expect(activity.current()).toBe("speaking");
    activity.remote("admitted-agent", false);
    expect(onChange.mock.calls).toEqual([
      ["speaking"],
      ["listening"],
      ["speaking"],
      ["quiet"],
    ]);
  });

  it("ignores other participants, including their quiet events", () => {
    const { activity, onChange } = rig();
    activity.playback(true);
    activity.remote("another-participant", true);
    expect(activity.current()).toBe("quiet");
    expect(onChange).not.toHaveBeenCalled();
    activity.remote("admitted-agent", true);
    activity.remote("another-participant", false);
    expect(activity.current()).toBe("speaking");
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("returns to quiet when playback is stopped without erasing local speech", () => {
    const { activity } = rig();
    activity.playback(true);
    activity.remote("admitted-agent", true);
    activity.playback(false);
    expect(activity.current()).toBe("quiet");
    activity.local(true);
    expect(activity.current()).toBe("listening");
    activity.local(false);
    expect(activity.current()).toBe("quiet");
  });

  it("intersects both inputs with current media authority", () => {
    const { activity, authority, onChange } = rig();
    activity.playback(true);
    activity.remote("admitted-agent", true);
    activity.local(true);
    authority.transmit = false;
    activity.refresh();
    expect(activity.current()).toBe("speaking");
    authority.play = false;
    activity.refresh();
    expect(activity.current()).toBe("quiet");
    expect(onChange).toHaveBeenLastCalledWith("quiet");
  });

  it("emits transitions only so quiet frames cannot keep resetting standby", () => {
    const { activity, onChange } = rig();
    for (let index = 0; index < 100; index++) {
      activity.local(false);
      activity.remote("admitted-agent", false);
      activity.playback(false);
      activity.refresh();
    }
    expect(onChange).not.toHaveBeenCalled();
    activity.local(true);
    activity.local(true);
    activity.local(false);
    activity.local(false);
    expect(onChange.mock.calls).toEqual([["listening"], ["quiet"]]);
  });

  it("clears both speakers and playback on terminal cleanup", () => {
    const { activity, onChange } = rig();
    activity.playback(true);
    activity.remote("admitted-agent", true);
    activity.local(true);
    activity.reset();
    expect(activity.current()).toBe("quiet");
    expect(onChange).toHaveBeenLastCalledWith("quiet");
    activity.playback(true);
    expect(activity.current()).toBe("quiet");
  });

  it("fails quiet when a live authority check throws", () => {
    const onChange = vi.fn();
    const activity = new VoxActivityTracker({
      agentIdentity: "admitted-agent",
      mayTransmitAudio: () => {
        throw new Error("revoked");
      },
      mayPlayAudio: () => {
        throw new Error("revoked");
      },
      onChange,
    });
    activity.playback(true);
    activity.remote("admitted-agent", true);
    activity.local(true);
    expect(activity.current()).toBe("quiet");
    expect(onChange).not.toHaveBeenCalled();
  });
});
