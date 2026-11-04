import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"

const PlaybookMeta: QuartzComponent = ({ fileData }: QuartzComponentProps) => {
  const fm = fileData.frontmatter as Record<string, any> | undefined
  const toc = fileData.toc ?? []
  const channels = ((fm?.channels ?? []) as string[]).join(" | ")
  const rows: [string, string][] = []
  if (fm?.industry) rows.push(["NGÀNH", String(fm.industry)])
  if (fm?.business_model) rows.push(["MÔ HÌNH", String(fm.business_model)])
  if (channels) rows.push(["KÊNH", channels])
  if (fm?.audience) rows.push(["DÀNH CHO", String(fm.audience)])
  return (
    <div class="pb-head">
      {rows.length > 0 && (
        <div class="pb-meta">
          {rows.map(([k, v]) => (
            <div>
              <b>{k}</b> {v}
            </div>
          ))}
        </div>
      )}
      <hr />
      {toc.length > 1 && (
        <div class="pb-guide">
          <p>Trong trang này:</p>
          <ul>
            {toc.map((t) => (
              <li>
                <a href={`#${t.slug}`}>{t.text}</a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export default (() => PlaybookMeta) satisfies QuartzComponentConstructor
