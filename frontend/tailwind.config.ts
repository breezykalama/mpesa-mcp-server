import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#101820",
        muted: "#637083",
        line: "#dfe5ec",
        surface: "#ffffff",
        canvas: "#f5f7f9",
        money: "#0f7b55",
        ember: "#b54708",
      },
      boxShadow: {
        panel: "0 14px 40px rgba(16, 24, 32, 0.08)",
      },
    },
  },
  plugins: [],
} satisfies Config;
