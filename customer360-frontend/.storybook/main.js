/** @type {import('@storybook/html-vite').StorybookConfig} */
const config = {
  stories: ["../static/js/__stories__/**/*.stories.js"],
  framework: {
    name: "@storybook/html-vite",
    options: {}
  },
  staticDirs: ["../static"]
};

export default config;
