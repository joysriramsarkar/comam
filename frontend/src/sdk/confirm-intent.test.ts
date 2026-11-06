import { describe, expect, it } from "vitest";
import { confirmIntent } from "./confirm-intent";

describe("confirmIntent", () => {
  it("accepts clear yeses", () => {
    expect(confirmIntent("হ্যাঁ")).toBe("yes");
    expect(confirmIntent("হ্যা")).toBe("yes");
    expect(confirmIntent("ঠিক আছে")).toBe("yes");
    expect(confirmIntent("বিল করো")).toBe("yes");
    expect(confirmIntent("সেভ করো")).toBe("yes");
    expect(confirmIntent("ok")).toBe("yes");
  });

  it("accepts clear nos", () => {
    expect(confirmIntent("না")).toBe("no");
    expect(confirmIntent("বাতিল করো")).toBe("no");
    expect(confirmIntent("cancel")).toBe("no");
    expect(confirmIntent("থাক")).toBe("no");
  });

  it("breaks the tie toward NOT billing", () => {
    // The exact bug: refusal speech contains the word "করো".
    expect(confirmIntent("না, বাতিল করো")).toBe("no");
    expect(confirmIntent("বাতিল করে দাও")).toBe("no");
    expect(confirmIntent("বিল করো না")).toBe("no");
  });

  it("returns null for unrelated speech — never a default yes", () => {
    expect(confirmIntent("")).toBeNull();
    expect(confirmIntent("আজকের বিক্রি কত")).toBeNull();
    expect(confirmIntent("এলোমেলো কথা")).toBeNull();
  });
});
