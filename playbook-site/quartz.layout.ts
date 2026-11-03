import { PageLayout, SharedLayout } from "./quartz/cfg"
import * as Component from "./quartz/components"
import PlaybookMeta from "./quartz/components/PlaybookMeta"
import PlaybookEnhance from "./quartz/components/PlaybookEnhance"

export const sharedPageComponents: SharedLayout = {
  head: Component.Head(),
  header: [],
  afterBody: [PlaybookEnhance()],
  footer: Component.Footer({ links: {} }),
}

const left = [
  Component.PageTitle(),
  Component.Search(),
  Component.Darkmode(),
  Component.Explorer({
    title: "Playbook",
    folderDefaultState: "open",
    folderClickBehavior: "link",
    // runs in the browser (serialised): keep it self-contained, no inner named functions
    sortFn: (a, b) =>
      (a.isFolder ? ["use-cases", "features"].indexOf(a.slugSegment) : 99) -
        (b.isFolder ? ["use-cases", "features"].indexOf(b.slugSegment) : 99) ||
      a.displayName.localeCompare(b.displayName, "vi"),
  }),
]

export const defaultContentPageLayout: PageLayout = {
  beforeBody: [
    Component.ConditionalRender({
      component: Component.Breadcrumbs({ rootName: "LEO Playbook" }),
      condition: (page) => page.fileData.slug !== "index",
    }),
    Component.ArticleTitle(),
    PlaybookMeta(),
  ],
  left,
  right: [Component.TableOfContents()],
}

export const defaultListPageLayout: PageLayout = {
  beforeBody: [Component.Breadcrumbs({ rootName: "LEO Playbook" }), Component.ArticleTitle()],
  left,
  right: [],
}
