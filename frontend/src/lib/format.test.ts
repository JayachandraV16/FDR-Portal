import { describe, expect, it } from "vitest";

import { fdrStatusLabel, formatDate, formatInr } from "./format";

describe("formatInr", () => {
  it.each([
    ["0.00", "₹0.00"],
    ["999.50", "₹999.50"],
    ["1000.00", "₹1,000.00"],
    ["100000.00", "₹1,00,000.00"],
    ["12345678.90", "₹1,23,45,678.90"],
    ["99999999999999.99", "₹9,99,99,99,99,99,999.99"],
    ["5", "₹5.00"],
    ["7.1", "₹7.10"],
  ])("formats %s as %s without float rounding", (input, expected) => {
    expect(formatInr(input)).toBe(expected);
  });

  it("leaves a value that is not a plain decimal visible instead of altering it", () => {
    expect(formatInr("not-a-number")).toBe("not-a-number");
    expect(formatInr("1e6")).toBe("1e6");
  });
});

describe("formatDate", () => {
  it("formats ISO calendar dates without timezone shifting", () => {
    expect(formatDate("2027-03-31")).toBe("31 Mar 2027");
    expect(formatDate("2026-01-05")).toBe("5 Jan 2026");
  });
  it("returns anything unrecognised unchanged", () => {
    expect(formatDate("soon")).toBe("soon");
    expect(formatDate("2026-13-01")).toBe("2026-13-01");
  });
});

describe("fdrStatusLabel", () => {
  it("labels known statuses and passes unknown ones through", () => {
    expect(fdrStatusLabel("CORRECTION_REQUESTED")).toBe("Correction requested");
    expect(fdrStatusLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });
});
