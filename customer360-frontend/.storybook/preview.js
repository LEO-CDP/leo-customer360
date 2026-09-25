// Loads the real app stylesheet (Tailwind utility classes used in the chat
// markup are NOT available here -- only the hand-written .docs-chat-* rules
// in app.css, which is all renderAnswerHtml()'s output needs).
import "../static/css/app.css";

/** @type {import('@storybook/html-vite').Preview} */
const preview = {
  parameters: {
    layout: "padded",
    backgrounds: {
      default: "light",
      values: [
        { name: "light", value: "#f8fafc" },
        { name: "dark", value: "#0f172a" }
      ]
    }
  }
};

export default preview;
