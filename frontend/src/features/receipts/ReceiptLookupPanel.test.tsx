import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ReceiptLookupPanel } from "./ReceiptLookupPanel";

describe("ReceiptLookupPanel", () => {
  it("submits the entered transaction reference", async () => {
    const mutate = vi.fn();
    const user = userEvent.setup();

    render(
      <ReceiptLookupPanel
        lookup={{
          data: undefined,
          error: null,
          isPending: false,
          mutate,
        } as never}
      />,
    );

    await user.type(
      screen.getByPlaceholderText("Checkout request ID or provider transaction ID"),
      "ws_CO_123",
    );
    await user.click(screen.getByRole("button", { name: /lookup receipt/i }));

    expect(mutate).toHaveBeenCalledWith("ws_CO_123");
  });
});
