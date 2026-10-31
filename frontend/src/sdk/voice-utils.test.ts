import { describe, expect, it } from "vitest";
import {
  assessClip,
  extensionForMime,
  levelKind,
  pickMimeType,
  voiceErrorMessage,
} from "./voice-utils";

describe("pickMimeType", () => {
  it("prefers opus webm, falls back in order", () => {
    expect(pickMimeType(() => true)).toBe("audio/webm;codecs=opus");
    expect(pickMimeType((t) => t === "audio/mp4")).toBe("audio/mp4");
    expect(pickMimeType(() => false)).toBe("");
  });
});

describe("extensionForMime", () => {
  it("maps real mime types, never assumes webm", () => {
    expect(extensionForMime("audio/mp4")).toBe("m4a");
    expect(extensionForMime("audio/webm;codecs=opus")).toBe("webm");
    expect(extensionForMime("audio/wav")).toBe("wav");
    expect(extensionForMime("audio/ogg;codecs=opus")).toBe("ogg");
  });
});

describe("assessClip quality gate", () => {
  it("rejects too-short and too-quiet clips before upload", () => {
    expect(assessClip(300, 0.5)).toBe("too_short");
    expect(assessClip(2000, 0.001)).toBe("too_quiet");
    expect(assessClip(2000, 0.4)).toBe("ok");
  });
});

describe("voiceErrorMessage (plan Appendix A)", () => {
  it("maps known codes to Bengali guidance", () => {
    expect(voiceErrorMessage("not-allowed")).toContain("অনুমতি");
    expect(voiceErrorMessage("no-speech")).toContain("শোনা যায়নি");
    expect(voiceErrorMessage("aborted")).toBe("");
  });
  it("never returns empty for unknown codes", () => {
    expect(voiceErrorMessage("something-new").length).toBeGreaterThan(0);
  });
});

describe("levelKind", () => {
  it("classifies silent / ok / clipping", () => {
    expect(levelKind(0)).toBe("silent");
    expect(levelKind(0.3)).toBe("ok");
    expect(levelKind(0.95)).toBe("clipping");
  });
});
