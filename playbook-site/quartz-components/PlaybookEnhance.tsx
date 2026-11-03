import { QuartzComponent, QuartzComponentConstructor } from "./types"

// Renders nothing; only ships a script (runs once, re-applies on every SPA "nav").
const PlaybookEnhance: QuartzComponent = () => null

PlaybookEnhance.afterDOMLoaded = `
(function () {
  // Search: visible close button (touch devices have no Esc). Dispatching Escape reuses Quartz's own handler.
  function addSearchClose() {
    var c = document.querySelector(".search-container")
    if (!c || c.querySelector(".pb-search-close")) return
    var b = document.createElement("button")
    b.type = "button"
    b.className = "pb-search-close"
    b.textContent = "Đóng"
    b.addEventListener("click", function () {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))
    })
    c.prepend(b)
  }

  // Mermaid: keep text >= ~13px (min-width = 85% of viewBox width, labels are 16px) and flag overflow for the scroll cue.
  function fitMermaid() {
    document.querySelectorAll("article code.mermaid").forEach(function (code) {
      var svg = code.querySelector("svg")
      var vb = svg && svg.viewBox && svg.viewBox.baseVal
      if (!vb || !vb.width) return
      svg.style.minWidth = Math.ceil(vb.width * 0.85) + "px"
      var pre = code.closest("pre")
      var more = code.scrollWidth > code.clientWidth + 1
      pre.toggleAttribute("data-pb-more", more && code.scrollLeft + code.clientWidth < code.scrollWidth - 4)
      if (!code.dataset.pbScroll) {
        code.dataset.pbScroll = "1"
        code.addEventListener("scroll", fitMermaid, { passive: true })
      }
    })
  }

  var mo
  function setup() {
    addSearchClose()
    fitMermaid()
    if (mo) mo.disconnect()
    var center = document.querySelector(".center")
    if (center) {
      mo = new MutationObserver(fitMermaid) // mermaid renders / re-renders (theme change) after nav
      mo.observe(center, { childList: true, subtree: true })
    }
  }
  document.addEventListener("nav", setup)
  window.addEventListener("resize", fitMermaid)
  setup()
})()
`

export default (() => PlaybookEnhance) satisfies QuartzComponentConstructor
