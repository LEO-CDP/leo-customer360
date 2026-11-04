import { QuartzConfig } from "./quartz/cfg"
import * as Plugin from "./quartz/plugins"

const config: QuartzConfig = {
  configuration: {
    pageTitle: "LEO Playbook",
    pageTitleSuffix: "",
    enableSPA: true,
    enablePopovers: false,
    analytics: null,
    locale: "vi-VN",
    baseUrl: "playbook.leocdp.com",
    ignorePatterns: ["private", "templates", ".git"],
    defaultDateType: "modified",
    theme: {
      fontOrigin: "googleFonts",
      cdnCaching: true,
      typography: { header: "Source Sans 3", body: "Source Sans 3", code: "IBM Plex Mono" },
      colors: {
        lightMode: {
          light: "#ffffff",
          lightgray: "#e1e1e1",
          gray: "#8e8e8e",
          darkgray: "#4a4a4a",
          dark: "#4a4a4a",
          secondary: "#0268e3",
          tertiary: "#0350ad",
          highlight: "rgba(2, 104, 227, 0.08)",
          textHighlight: "#fff23688",
        },
        darkMode: {
          light: "#111827",
          lightgray: "#313c4e",
          gray: "#7f8ba0",
          darkgray: "#cbd3df",
          dark: "#eef1f6",
          secondary: "#6cb0ff",
          tertiary: "#b5d6ff",
          highlight: "rgba(108, 176, 255, 0.12)",
          textHighlight: "#b3aa0288",
        },
      },
    },
  },
  plugins: {
    transformers: [
      Plugin.FrontMatter(),
      Plugin.CreatedModifiedDate({ priority: ["frontmatter", "filesystem"] }),
      Plugin.SyntaxHighlighting({ theme: { light: "github-light", dark: "github-dark" }, keepBackground: false }),
      Plugin.ObsidianFlavoredMarkdown({ enableInHtmlEmbed: false }),
      Plugin.GitHubFlavoredMarkdown(),
      Plugin.TableOfContents({ maxDepth: 2, minEntries: 1 }),
      Plugin.CrawlLinks({ markdownLinkResolution: "relative" }),
      Plugin.Description(),
    ],
    filters: [Plugin.RemoveDrafts()],
    emitters: [
      Plugin.AliasRedirects(),
      Plugin.ComponentResources(),
      Plugin.ContentPage(),
      Plugin.FolderPage(),
      Plugin.TagPage(),
      Plugin.ContentIndex({ enableSiteMap: true, enableRSS: false }),
      Plugin.Assets(),
      Plugin.Static(),
      Plugin.NotFoundPage(),
    ],
  },
}

export default config
