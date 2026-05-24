import { afterEach, describe, expect, it, vi } from "vitest";
import { clearToken, getStoredToken, storeToken } from "./client";

describe("operator token storage", () => {
  afterEach(() => {
    clearToken();
    vi.restoreAllMocks();
  });

  it("stores and clears bearer tokens in localStorage", () => {
    storeToken("operator-token");

    expect(getStoredToken()).toBe("operator-token");

    clearToken();

    expect(getStoredToken()).toBe("");
  });
});
