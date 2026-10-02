import { describe, expect, it } from "vitest";
import { resolveDeliveryIntent } from "./delivery";

describe("delivery preference", () => {
  it("treats the exact wake phrase as a read-only greeting cue", () => {
    expect(resolveDeliveryIntent("Hey NavoX!")).toEqual({
      intent: "SPEAK",
      cue_only: true,
      ordinal: null,
      wake_only: true,
    });
    expect(
      resolveDeliveryIntent("Hey NavoX, what is next?").wake_only,
    ).toBeUndefined();
  });
  it("handles standalone social cues without consuming questions or actions", () => {
    expect(resolveDeliveryIntent("Thank you.")).toMatchObject({
      cue_only: true,
      intent: "AUTOMATIC",
      conversation_only: "ACKNOWLEDGMENT",
    });
    expect(resolveDeliveryIntent("How are you doing today?")).toMatchObject({
      cue_only: true,
      conversation_only: "CHECK_IN",
    });
    for (const text of [
      "Thank you, and send it.",
      "Thanks. What is the news today?",
      "How are you doing today, and what's the weather?",
    ]) {
      expect(resolveDeliveryIntent(text), text).toMatchObject({
        cue_only: false,
      });
      expect(
        resolveDeliveryIntent(text).conversation_only,
        text,
      ).toBeUndefined();
    }
  });

  it("leaves an ordinary question alone", () => {
    expect(resolveDeliveryIntent("What am I missing today?")).toEqual({
      intent: "AUTOMATIC",
      cue_only: false,
      ordinal: null,
    });
    expect(resolveDeliveryIntent("What is trending in the news?")).toEqual({
      intent: "AUTOMATIC",
      cue_only: false,
      ordinal: null,
    });
  });

  it("does not treat a general use of 'read' as a delivery cue", () => {
    expect(resolveDeliveryIntent("Did I read it yesterday?").intent).toBe(
      "AUTOMATIC",
    );
    expect(resolveDeliveryIntent("I don't read the news much").intent).toBe(
      "AUTOMATIC",
    );
    // A cue phrase that is the object of a statement is not an instruction.
    expect(resolveDeliveryIntent("He read it to me yesterday").intent).toBe(
      "AUTOMATIC",
    );
    expect(resolveDeliveryIntent("I read it again last night").intent).toBe(
      "AUTOMATIC",
    );
    expect(resolveDeliveryIntent("Did she read it aloud?").intent).toBe(
      "AUTOMATIC",
    );
  });

  it("accepts a cue that starts a clause in a longer utterance", () => {
    expect(
      resolveDeliveryIntent("What is the weather, and read it to me").intent,
    ).toBe("SPEAK");
    expect(
      resolveDeliveryIntent("What is the weather, could you read it to me")
        .intent,
    ).toBe("SPEAK");
    expect(
      resolveDeliveryIntent("What is the weather? Do not read it aloud.")
        .intent,
    ).toBe("SUPPRESS");
  });

  it("recognises an explicit read-aloud request", () => {
    const resolution = resolveDeliveryIntent("read it to me");
    expect(resolution.intent).toBe("SPEAK");
    expect(resolution.cue_only).toBe(true);
    expect(resolution.ordinal).toBeNull();
  });

  it("accepts natural phrasing and leading fillers", () => {
    expect(
      resolveDeliveryIntent("Hey NavoX, could you read that aloud please?"),
    ).toMatchObject({ intent: "SPEAK", cue_only: true });
    expect(
      resolveDeliveryIntent("Please read the last answer out loud for me."),
    ).toMatchObject({ intent: "SPEAK", cue_only: true });
  });

  it("resolves a named saved turn through a bounded ordinal", () => {
    expect(resolveDeliveryIntent("read the second one to me")).toMatchObject({
      intent: "SPEAK",
      cue_only: true,
      ordinal: 2,
    });
    expect(resolveDeliveryIntent("read the most recent answer")).toMatchObject({
      intent: "SPEAK",
      cue_only: true,
      ordinal: -1,
    });
  });

  it("accepts the short read-aloud forms only when they stand alone", () => {
    expect(resolveDeliveryIntent("read it")).toMatchObject({
      intent: "SPEAK",
      cue_only: true,
      ordinal: null,
    });
    expect(resolveDeliveryIntent("read that")).toMatchObject({
      intent: "SPEAK",
      cue_only: true,
    });
    // The same words inside a question are not a delivery request.
    expect(resolveDeliveryIntent("Did I read it yesterday?")).toMatchObject({
      intent: "AUTOMATIC",
      cue_only: false,
    });
  });

  it("leaves an embedded cue in a question without claiming the turn", () => {
    const resolution = resolveDeliveryIntent(
      "What is the weather? And read it to me.",
    );
    expect(resolution.intent).toBe("SPEAK");
    expect(resolution.cue_only).toBe(false);
    expect(resolution.ordinal).toBeNull();
  });

  it("lets a suppression cue win over the read-aloud phrase it contains", () => {
    expect(resolveDeliveryIntent("don't read it to me")).toMatchObject({
      intent: "SUPPRESS",
      cue_only: true,
    });
    const embedded = resolveDeliveryIntent(
      "What is today? Don't read it aloud.",
    );
    expect(embedded.intent).toBe("SUPPRESS");
    expect(embedded.cue_only).toBe(false);
  });

  it("recognises the other natural suppression phrasings", () => {
    for (const text of [
      "do not read it out loud",
      "keep it silent",
      "no need to read it aloud",
      "read it silently please",
      "stay silent for this one",
    ]) {
      expect(resolveDeliveryIntent(text).intent, text).toBe("SUPPRESS");
    }
  });
});
